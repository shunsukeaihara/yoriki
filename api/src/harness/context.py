"""RunEventからモデルへの要求を組み立てる（harness.md §5）。

同じRunEventの並びとモデル設定の版からは、どのWorkerでも同じ要求になる。圧縮は範囲の置き換えとして読む。
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from django.utils import timezone

from llm.providers.base import Message, Part, TextPart, ToolCall
from run.models import Run, RunEvent, RunEventKind

ADDITIONAL_INSTRUCTION_PREFIX = "[Additional instruction during the work]"


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


def build_items(run: Run, events: Iterable[RunEvent]) -> list[Item]:
    """モデルに見せる履歴。推論の途中経過は、同じ接続・同じモデルの応答のものだけを戻す（harness.md §5.5）"""
    key = provider_key(run)
    items: list[Item] = []
    for event in events:
        data = event.data
        match event.kind:
            case RunEventKind.USER_MESSAGE | RunEventKind.CONTEXT:
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


def runtime_context(run: Run, *, user_name: str) -> str:
    """実行時の文脈（harness.md §6.2）。日時は分の単位にし、変わったときだけ追記する"""
    version = run.model_version
    now = timezone.localtime().strftime("%Y-%m-%d %H:%M %Z")
    lines = [
        "<runtime_context>",
        f"  <now>{now}</now>",
        f'  <user name="{_escape(user_name)}" />',
        f"  <mode>{'conversation' if run.is_interactive else 'task'}</mode>",
        f'  <model profile="{_escape(version.profile.name)}" effort="{_escape(run.effort)}" />',
        '  <sandbox available="false" />',
        f"  <confidential>{'true' if run.handles_sensitive_data else 'false'}</confidential>",
        "</runtime_context>",
    ]
    return "\n".join(lines)


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
