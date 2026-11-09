"""Skillを読み込む（harness.md §10.1）。引数の名前は文字列で、今の一覧にある名前だけを受け付ける（一覧がRunの中で
変わるので、Toolの定義をprompt cacheのために変えない）"""

from typing import Any

from run.models import RunEvent, RunEventKind, Subagent
from skill.models import Skill, SkillVersion

from .. import tasks
from ..catalogs import unlisted_skill
from ..connectors import catalog as connector_catalog
from ..skills import loaded_entries, manifest_entry, render, skill_catalog
from .base import Effect, Tool, ToolContext, ToolResult
from .submit_result import NAME as SUBMIT_RESULT

NAME = "skill"

DESCRIPTION = """Load a skill: the business procedure, reference files and rules for a kind of work.

The available skills are listed in <skills> in the runtime context, with their descriptions. When the request names \
a skill or clearly matches a skill's description, load it before you start the work, and follow its instructions. \
If several match, load the smallest set that covers the request. The list shows summaries only: do not guess a \
skill's procedure without loading it. If no skill matches, work without one.

The result contains the instructions, the paths of the skill's files (read-only under /workspace/master), the tools \
the skill uses, and the result schema if it has one. Loading the same skill again in the same work returns the same \
version."""


# 一覧にない名前のエラーに添える候補の数
MAX_CANDIDATES = 20

PARAMETERS = {
    "type": "object",
    "properties": {"name": {"type": "string", "description": "The skill name, exactly as listed in <skills>."}},
    "required": ["name"],
    "additionalProperties": False,
}


def execute(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    run = context.run
    skills = skill_catalog(run).get("skills", [])
    loaded_here = loaded_entries(run, _current(context.subagent))
    uid = next((e["skill"] for e in loaded_here if e["name"] == arguments["name"]), None)
    uid = uid or next((e["skill"] for e in skills if e["name"] == arguments["name"]), None)
    uid = uid or unlisted_skill(run, arguments["name"])
    if uid is None:
        candidates = ", ".join(e["name"] for e in skills[:MAX_CANDIDATES]) or "(none)"
        return ToolResult.error(f'No skill named "{arguments["name"]}" is available. Available skills: {candidates}.')

    skill = Skill.objects.select_related("current_version", "department").filter(uid=uid).first()
    if skill is None:
        return ToolResult.error("This skill was deleted. Continue without it, and tell the user.")
    loaded = next((e for e in loaded_here if e["skill"] == skill.uid), None)
    if loaded is not None:
        return _load_again(skill, loaded, arguments["name"], context)
    if skill.deleted:
        return ToolResult.error("This skill was deleted. Continue without it, and tell the user.")
    if reason := _problem(run, skill):
        return _unusable(reason)
    assert skill.current_version_id is not None  # unusable_reason() が確かめた
    version = SkillVersion.objects.prefetch_related("assets__resource_version").get(pk=skill.current_version_id)
    # 資料を置けたことを確かめてから追記する。置けなければ追記しない（harness.md §10.1）。
    # Sandboxをまだ起動していなければ、起動するときにManifestの全Skillの資料を置く
    if context.session is not None and context.session.started:
        context.session.place_skill_assets(skill, version)
    value = manifest_entry(skill, version, arguments["name"])
    with run.fenced(context.attempt) as current:
        if context.subagent is not None:
            # 子が読み込んだものは子にだけ足す。親や別の子の読込みにしない（harness.md §10.11）
            subagent = Subagent.objects.select_for_update().get(pk=context.subagent.pk)
            subagent.skills = [*subagent.skills, value]
            subagent.save(update_fields=["skills"])
        else:
            current.append_manifest("skill", value)
            current.save(update_fields=["manifest", "updated"])
            run.manifest = current.manifest
        RunEvent.record(current, RunEventKind.SKILL_LOADED, value, comment=skill.name, subagent=context.subagent)
    return _loaded(skill, version, arguments["name"], context)


def _current(subagent: Subagent | None) -> Subagent | None:
    return Subagent.objects.get(pk=subagent.pk) if subagent is not None else None


def _problem(run, skill: Skill) -> str:
    """このセッションの権限で今選べない理由（選べれば空文字）。会話は依頼した人、タスクはタスクの範囲で判定する"""
    if tasks.is_task_run(run):
        return tasks.skill_problem(run, skill, skill.current_version)
    return skill.unusable_reason(run.owner, sensitive=run.handles_sensitive_data)


def _load_again(skill: Skill, loaded: dict[str, Any], name: str, context: ToolContext) -> ToolResult:
    """同じRunでは追記した版を返す（途中で公開し直されても切り替えない）。権限が決めるのは新しく選べるかだけなので、停止・
    失効した後も読み直せる（圧縮の後に手順を読み直すため。harness.md §10.1）"""
    version = SkillVersion.objects.prefetch_related("assets__resource_version").get(pk=loaded["version"])
    return _loaded(skill, version, name, context)


def _loaded(skill: Skill, version: SkillVersion, name: str, context: ToolContext) -> ToolResult:
    return ToolResult.text(
        render(
            skill,
            version,
            name=name,
            # submit_result の形はタスクの定義で決まる。一覧から読み込んだSkillの形は最後の応答に書かせる
            result_tool=SUBMIT_RESULT in context.exposed and _instructed(context.run, skill),
            connector_names={e["connector"]: e["name"] for e in connector_catalog(context.run).get("connectors", [])},
        ),
        value={"skill": skill.uid, "version": version.number},
        meta={"skill": skill.name, "version": version.number},
    )


def _instructed(run, skill: Skill) -> bool:
    return any(e["skill"] == skill.uid for e in ((run.manifest or {}).get("fixed") or {}).get("skills", []))


def _unusable(reason: str) -> ToolResult:
    return ToolResult.error(f"This skill cannot be used now ({reason}). Continue without it, and tell the user.")


# 読み込みはManifestへの追記とRunEventの記録を伴うので、Runnerのスレッドで1つずつ実行する（並べない）
SKILL = Tool(NAME, DESCRIPTION, PARAMETERS, execute, Effect.READ, replay_safe=True)
