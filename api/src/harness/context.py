"""RunEventからモデルへの要求を組み立てる（harness.md §5）。

同じRunEventの並びとモデル設定の版からは、どのWorkerでも同じ要求になる。圧縮は範囲の置き換えとして読む。
同じ会話の次の依頼（子のRun）は、親のRunの文脈をそのまま先頭に引き継ぐ（design.md §5.1、harness.md §5.6）。
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from django.utils import timezone

from llm.providers.base import Message, Part, TextPart, ToolCall
from run.models import Run, RunEvent, RunEventKind
from runtime.launcher import WORK_DIR

ADDITIONAL_INSTRUCTION_PREFIX = "[Additional instruction during the work]"
RUNTIME_CONTEXT_TAG = "<runtime_context>"


def parts_to_json(parts: Iterable[Part]) -> list[dict[str, Any]]:
    """RunEventに保存する形。画像等のbytesはArtifactの段階でArtifactの参照として保存する"""
    result = []
    for part in parts:
        if isinstance(part, TextPart):
            result.append({"type": "text", "text": part.text})
        else:
            raise ValueError(f"unsupported part for storage: {type(part).__name__}")
    return result


def parts_from_json(data: list[dict[str, Any]]) -> tuple[Part, ...]:
    return tuple(TextPart(p["text"]) for p in data if p.get("type") == "text")


def provider_key(run: Run) -> dict[str, str]:
    version = run.model_version
    return {"connection": version.connection.uid, "model": version.model_name}


@dataclass(frozen=True)
class Item:
    """モデルに見せる履歴の1件と、その元のRunEvent"""

    sequence: int
    kind: int
    message: Message


# 引き継いだ文脈の項目の sequence。このRunの記録（1から）より前に並び、圧縮では1つのまとまりとして扱う
INHERITED_SEQUENCE = 0
UNFINISHED_CALL = (
    "[The earlier request ended before this tool call finished, so its result is unknown. Check the current state "
    "before relying on it.]"
)


def build_items(run: Run, events: Iterable[RunEvent], inherited: Iterable[Item] = ()) -> list[Item]:
    """モデルに見せる履歴。`inherited`（inherited_items()）を先頭に置く。
    推論の途中経過は、同じ接続・同じモデルの応答のものだけを戻す（harness.md §5.5）"""
    return _build(provider_key(run), events, list(inherited))


def inherited_items(run: Run) -> list[Item]:
    """親のRun（同じ会話の前の依頼）の、最後にモデルへ送った文脈と同じ履歴。親のさらに前も、親が引き継いだものとして
    含む。親は終わっていて記録は変わらないので、Runnerは1回だけ読む。結果のないまま終わったTool呼出しには、
    結果が分からないことを結果として添える（プロバイダは呼出しと結果の対応を求める）"""
    return [Item(INHERITED_SEQUENCE, item.kind, item.message) for item in _ancestor_items(run, provider_key(run))]


def _ancestor_items(run: Run, key: dict[str, str]) -> list[Item]:
    parent = run.parent
    if parent is None:
        return []
    events = list(RunEvent.objects.filter(run=parent).order_by("sequence"))
    # 親が引き継いだ部分をまるごと圧縮していれば、さらに前は読まなくてよい
    covered = any(e.kind == RunEventKind.COMPACTION and e.data["from"] <= INHERITED_SEQUENCE for e in events)
    above = [] if covered else [Item(INHERITED_SEQUENCE, i.kind, i.message) for i in _ancestor_items(parent, key)]
    return _close_unfinished_calls(_build(key, events, above))


def _close_unfinished_calls(items: list[Item]) -> list[Item]:
    result: list[Item] = []
    pending: dict[str, tuple[Item, str]] = {}

    def flush() -> None:
        for call_id, (owner, name) in pending.items():
            message = Message("tool", UNFINISHED_CALL, tool_call_id=call_id, tool_name=name, is_error=True)
            result.append(Item(owner.sequence, RunEventKind.TOOL_RESULT, message))
        pending.clear()

    for item in items:
        if item.message.role == "tool":
            pending.pop(item.message.tool_call_id, None)
        else:
            flush()
        result.append(item)
        for call in item.message.tool_calls:
            pending[call.id] = (item, call.name)
    flush()
    return result


def _build(key: dict[str, str], events: Iterable[RunEvent], items: list[Item]) -> list[Item]:
    for event in events:
        data = event.data
        match event.kind:
            case RunEventKind.USER_MESSAGE:
                text = data["text"]
                if data.get("attachments"):
                    text = f"{text}\n\n{attachments_text(data['attachments'])}".strip()
                if data.get("earlier_attachments"):
                    text = f"{text}\n\n{attachments_text(data['earlier_attachments'], earlier=True)}".strip()
                items.append(Item(event.sequence, event.kind, Message("user", text)))
            case RunEventKind.CONTEXT | RunEventKind.JOB_FINISHED:
                items.append(Item(event.sequence, event.kind, Message("user", data["text"])))
            case RunEventKind.ASSISTANT_MESSAGE:
                calls = tuple(ToolCall(c["id"], c["name"], c["arguments"]) for c in data.get("tool_calls", []))
                state = data.get("provider_state") if data.get("provider") == key else None
                message = Message("assistant", data.get("text", ""), calls, provider_state=state)
                items.append(Item(event.sequence, event.kind, message))
            case RunEventKind.TOOL_RESULT:
                message = Message(
                    "tool",
                    parts_from_json(data["content"]),
                    tool_call_id=data["call_id"],
                    tool_name=data["name"],
                    is_error=data.get("is_error", False),
                )
                items.append(Item(event.sequence, event.kind, message))
            case RunEventKind.COMPACTION:
                start, end = data["from"], data["to"]
                position = sum(1 for item in items if item.sequence < start)
                items = [item for item in items if not start <= item.sequence <= end]
                items.insert(position, Item(start, event.kind, Message("user", data["text"])))
    return items


def build_messages(run: Run, events: Iterable[RunEvent]) -> list[Message]:
    return [item.message for item in build_items(run, events)]


def pending_tool_calls(events: list[RunEvent]) -> list[dict[str, Any]]:
    """結果のないTool呼出し（新しい順ではなく呼出しの順）"""
    answered = {e.data["call_id"] for e in events if e.kind == RunEventKind.TOOL_RESULT}
    calls = []
    for event in events:
        if event.kind == RunEventKind.ASSISTANT_MESSAGE:
            calls += [c for c in event.data.get("tool_calls", []) if c["id"] not in answered]
    return calls


def attachments_text(attachments: list[dict[str, Any]], *, earlier: bool = False) -> str:
    """利用者が添付したファイルの一覧。中身はSandboxの /workspace/input に読取り専用で置く。
    `earlier` は同じ会話の前の依頼で添付したもの"""
    lines = [f"- {a['path']} ({a.get('content_type', '')}, {_size(a.get('size', 0))})" for a in attachments]
    if earlier:
        head = (
            '<attachments scope="earlier">\nFiles attached earlier in this conversation (read-only). Files you made in '
            "earlier requests are still in /workspace/work and /workspace/output:\n"
        )
    else:
        head = "<attachments>\nThe user attached these files (read-only):\n"
    return head + "\n".join(lines) + "\n</attachments>"


def _size(size: int) -> str:
    if size < 1024:
        return f"{size} bytes"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def runtime_context(run: Run, *, user_name: str) -> str:
    """実行時の文脈（harness.md §6.2）。日時は分の単位にし、変わったときだけ追記する"""
    version = run.model_version
    now = timezone.localtime().strftime("%Y-%m-%d %H:%M %Z")
    lines = [
        RUNTIME_CONTEXT_TAG,
        f"  <now>{now}</now>",
        f'  <user name="{_escape(user_name)}" />',
        f"  <mode>{'conversation' if run.is_interactive else 'task'}</mode>",
        f'  <model profile="{_escape(version.profile.name)}" effort="{_escape(run.effort)}" />',
        (
            f'  <sandbox available="true" workdir="{WORK_DIR}" />'
            if run.runtime_version_id
            else '  <sandbox available="false" />'
        ),
        f"  <confidential>{'true' if run.handles_sensitive_data else 'false'}</confidential>",
        "</runtime_context>",
    ]
    return "\n".join(lines)


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
