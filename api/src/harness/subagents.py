"""サブエージェント（harness.md §10.11）。

親のLLMが `subagent` で起動し、親のワークスペースのコピーの上で別のAttemptとして動く。親へ返すのはテキストと
ファイルだけで、親は終わった子を、次の区切りで `subagent.finished` として受け取る（子のプロセスは親の記録の途中に
書き込まない。Tool呼出しとその結果の間に挟まらないように）。
"""

import logging
from typing import Any

from django.db import transaction
from django.utils import timezone

from artifact.models import SUBAGENT_RESULTS_DIR
from artifact.storage import StorageError, get_storage
from llm.models import ModelProfile, ModelProfileVersion, ProviderKind
from run.models import (
    LIVE_SUBAGENT_STATUSES,
    Run,
    RunEvent,
    RunEventKind,
    RunTrigger,
    Subagent,
    SubagentStatus,
)

from .skills import explicit_text
from .tools import SANDBOX_TOOLS
from .tools.base import Tool
from .tools.datasource import DATASOURCE_TOOLS
from .tools.skill_tool import SKILL
from .untrusted import untrusted

logger = logging.getLogger(__name__)

# 子に出してよいTool。親に出しているToolとの共通部分だけを出す（harness.md §10.11）
CHILD_TOOL_NAMES = frozenset(t.name for t in (*SANDBOX_TOOLS, *DATASOURCE_TOOLS, SKILL))
# 親へ返すテキストの上限（文字）。超えた分は切って、そのことを添える
MAX_RESULT_CHARS = 50_000


def child_tools(parent_tools: tuple[Tool, ...]) -> tuple[Tool, ...]:
    """子のTool：Sandboxの土台のTool（`todo_write` を除く）と `skill`・`datasource_*` のうち、親に出しているもの"""
    return tuple(t for t in parent_tools if t.name in CHILD_TOOL_NAMES)


def model_candidates(run: Run) -> list[ModelProfileVersion]:
    """子に選べるモデル設定の版。agentセッションで使えるもの（機密ならローカルLLM）で、Tool呼出しに対応するもの。
    タスクのRunでは親と同じ（タスクに固定したもの）だけ（harness.md §10.11）"""
    if run.trigger != RunTrigger.CONVERSATION:
        return [run.model_version]
    profiles = ModelProfile.objects.filter(
        deleted=False,
        is_active=True,
        current_version__connection__is_active=True,
        current_version__connection__deleted=False,
        current_version__supports_tools=True,
    ).select_related("current_version__connection", "current_version__profile")
    if run.handles_sensitive_data:
        profiles = profiles.filter(current_version__connection__kind=ProviderKind.LOCAL)
    versions = [p.current_version for p in profiles.order_by("-is_default", "name") if p.current_version]
    # 親と同じモデル設定の版は、今の版でなくても選べる（既定）
    return [run.model_version, *(v for v in versions if v.profile_id != run.model_version.profile_id)]


def resolve_model(run: Run, name: str, effort: str) -> tuple[ModelProfileVersion, str] | str:
    """`subagent` の `model`・`effort` を確かめて固定する。使えなければモデルへ返す誤りの文"""
    candidates = model_candidates(run)
    if name:
        version = next((v for v in candidates if v.profile.name == name), None)
        if version is None:
            names = ", ".join(f'"{v.profile.name}"' for v in candidates)
            return f'No model named "{name}" can be used for a subagent. Available: {names}.'
    else:
        version = run.model_version
    if effort:
        if effort not in version.effort_values:
            values = ", ".join(version.effort_values) or "(none)"
            return f'The effort "{effort}" is not accepted by this model. Accepted values: {values}.'
        return version, effort
    return version, run.effort if version.pk == run.model_version_id else version.default_effort


def instructed_skills_text(run: Run, events: list[RunEvent]) -> str:
    """親に使うよう指示したSkill（`/` で指定・タスクの定義・評価するDraft）の本文。子の最初の発言に入れる"""
    entries: dict[str, dict[str, Any]] = {}
    for entry in ((run.manifest or {}).get("fixed") or {}).get("skills", []):
        entries[entry["skill"]] = entry
    for event in events:
        if event.kind == RunEventKind.USER_MESSAGE:
            for entry in event.data.get("skills") or []:
                entries[entry["skill"]] = entry
    if not entries:
        return ""
    return explicit_text(list(entries.values()), run.current_catalog("connector_catalog"), result_tool=False)


def first_message(subagent: Subagent, skills_text: str, *, jobs_running: bool) -> str:
    """子の最初の発言。親の指示と、使うよう指示されたSkillの本文"""
    parts = [subagent.prompt.strip()]
    if jobs_running:
        parts.append(JOBS_RUNNING_NOTE)
    if skills_text:
        parts.append(skills_text)
    return "\n\n".join(parts)


def live_count(run: Run) -> int:
    return Subagent.objects.filter(run=run, status__in=LIVE_SUBAGENT_STATUSES).count()


def result_dir(subagent: Subagent) -> str:
    """親の /workspace/input の中の、子が返したファイルの場所"""
    return f"{SUBAGENT_RESULTS_DIR}/{subagent.uid}"


def finished_text(subagent: Subagent) -> str:
    """親へ渡す `subagent.finished` の文。返すテキストは指示の権限を持たないものとして囲む（harness.md §6.3）"""
    head = f"[Subagent {subagent.uid} ({subagent.description}) {_outcome(subagent)}."
    lines = [head]
    if subagent.result_text.strip():
        text = subagent.result_text
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + "\n…(cut)"
        lines.append("Its report:")
        lines.append(untrusted(text, f"subagent {subagent.uid}", block=True))
    if subagent.result_files:
        base = f"/workspace/input/{result_dir(subagent)}"
        paths = ", ".join(f["path"] for f in subagent.result_files[:50])
        lines.append(
            f"It returned files (read-only) in {base}/: {paths}. Check them, and copy what you use to /workspace/work "
            "or /workspace/output."
        )
    lines.append("]")
    return "\n".join(lines)


def _outcome(subagent: Subagent) -> str:
    if subagent.status == SubagentStatus.COMPLETED:
        return "finished"
    if subagent.status == SubagentStatus.CANCELLED:
        return f"was stopped ({subagent.error})" if subagent.error else "was stopped"
    return f"failed ({subagent.error})" if subagent.error else "failed"


def record_finished(run: Run, subagent: Subagent) -> RunEvent:
    """親の記録に `subagent.finished` を書く（Runの行ロックの中で呼ぶ）"""
    Subagent.objects.filter(pk=subagent.pk).update(reported=True)
    subagent.reported = True
    return RunEvent.record(
        run,
        RunEventKind.SUBAGENT_FINISHED,
        {"subagent": subagent.uid, "status": subagent.status, "text": finished_text(subagent)},
        comment=f"{subagent.description}（{subagent.get_status_display()}）",
    )


def unreported(run: Run) -> list[Subagent]:
    """終わったが、まだ親に知らせていない子（終わった順）"""
    return list(
        Subagent.objects.filter(run=run, reported=False)
        .exclude(status__in=LIVE_SUBAGENT_STATUSES)
        .order_by("finished", "pk")
    )


def end(
    subagent: Subagent,
    status: SubagentStatus,
    *,
    text: str = "",
    files: list[dict[str, str]] | None = None,
    error: str = "",
) -> bool:
    """子を終わりの状態にする（Runの行ロックの中で呼ぶ）。止められていた（もう動いていない）ならFalse"""
    updated = Subagent.objects.filter(pk=subagent.pk, status=SubagentStatus.RUNNING).update(
        status=status, result_text=text, result_files=files or [], error=error[:1000], finished=timezone.now()
    )
    return bool(updated)


def discard_copy(subagent: Subagent) -> None:
    """一時のコピーを消す。消せなければ残し、Reconcilerがやり直す"""
    if not subagent.copy_key:
        return
    try:
        get_storage(subagent.copy_storage).delete(subagent.copy_key)
    except (StorageError, OSError):
        logger.warning("cannot delete the workspace copy of subagent %s", subagent.uid, exc_info=True)
        return
    Subagent.objects.filter(pk=subagent.pk).update(copy_storage="", copy_key="")
    subagent.copy_storage = subagent.copy_key = ""


def discard_copy_later(subagent: Subagent) -> None:
    transaction.on_commit(lambda: discard_copy(subagent))


JOBS_RUNNING_NOTE = (
    "[Background jobs were running in the parent's workspace when this copy was made, so files they were writing may "
    "be incomplete.]"
)
