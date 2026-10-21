"""画面へ送るイベントの型（realtime.md §3）。RunEventの一覧のAPIとSSEのストリームで同じ形を使う"""

from typing import Any

from drf_spectacular.utils import PolymorphicProxySerializer
from rest_framework import serializers

from run.models import ClarificationKind, RunEndReason, RunEvent, RunEventKind, RunStatus

MESSAGE_KINDS = (RunEventKind.USER_MESSAGE, RunEventKind.ASSISTANT_MESSAGE, RunEventKind.TOOL_RESULT)


class ToolCallInfoSerializer(serializers.Serializer):
    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    arguments = serializers.DictField(read_only=True)


class ToolResultInfoSerializer(serializers.Serializer):
    call_id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    is_error = serializers.BooleanField(read_only=True)
    text = serializers.CharField(read_only=True)


class BaseEventSerializer(serializers.Serializer):
    type = serializers.CharField(read_only=True)
    run = serializers.CharField(read_only=True)
    sequence = serializers.IntegerField(read_only=True)
    created = serializers.DateTimeField(read_only=True)


class RunStatusEventSerializer(BaseEventSerializer):
    """Runの状態の変更"""

    status = serializers.ChoiceField(choices=RunStatus.choices, read_only=True)
    status_display = serializers.CharField(read_only=True)
    end_reason = serializers.ChoiceField(choices=RunEndReason.choices, read_only=True, allow_null=True)
    comment = serializers.CharField(read_only=True)


class RunMessageEventSerializer(BaseEventSerializer):
    """確定したメッセージとTool実行（利用者の発言、エージェントの応答、Toolの結果）"""

    kind = serializers.ChoiceField(choices=RunEventKind.choices, read_only=True)
    text = serializers.CharField(read_only=True)
    tool_calls = ToolCallInfoSerializer(many=True, read_only=True)
    tool_result = ToolResultInfoSerializer(read_only=True, allow_null=True)


class ClarificationEventSerializer(BaseEventSerializer):
    """エージェントからの確認（回答待ち）"""

    clarification = serializers.CharField(read_only=True)
    kind = serializers.ChoiceField(choices=ClarificationKind.choices, read_only=True)


class RunOtherEventSerializer(BaseEventSerializer):
    """そのほかの記録（モデルの切り替え、再試行、圧縮等）。詳細のログに出す"""

    kind = serializers.ChoiceField(choices=RunEventKind.choices, read_only=True)
    kind_display = serializers.CharField(read_only=True)
    comment = serializers.CharField(read_only=True)


class RunDeltaEventSerializer(serializers.Serializer):
    """LLM出力の差分。保存しない一時イベントで、`id:` を付けない"""

    type = serializers.CharField(read_only=True)
    run = serializers.CharField(read_only=True)
    step = serializers.IntegerField(read_only=True)
    text = serializers.CharField(read_only=True)


class UserRunStatusEventSerializer(serializers.Serializer):
    """ログインユーザー宛ての通知：自分のRunの状態の変更"""

    type = serializers.CharField(read_only=True)
    run = serializers.CharField(read_only=True)
    conversation = serializers.CharField(read_only=True)
    status = serializers.ChoiceField(choices=RunStatus.choices, read_only=True)
    sequence = serializers.IntegerField(read_only=True)


EVENT_TYPES = {
    "run.status": RunStatusEventSerializer,
    "run.message": RunMessageEventSerializer,
    "clarification.requested": ClarificationEventSerializer,
    "run.event": RunOtherEventSerializer,
}

RunEventSchema = PolymorphicProxySerializer(
    component_name="RunStreamEvent",
    serializers={**EVENT_TYPES, "run.delta": RunDeltaEventSerializer},
    resource_type_field_name="type",
)


def event_type(event: RunEvent) -> str:
    if event.kind == RunEventKind.STATUS:
        return "run.status"
    if event.kind in MESSAGE_KINDS:
        return "run.message"
    if event.kind == RunEventKind.CLARIFICATION_REQUESTED:
        return "clarification.requested"
    return "run.event"


def serialize_event(event: RunEvent, run_uid: str) -> dict[str, Any]:
    """RunEventを画面へ送る形にする。モデル用の内容（推論の途中経過等）は出さない"""
    data = event.data
    kind = event_type(event)
    base = {"type": kind, "run": run_uid, "sequence": event.sequence, "created": event.created}
    match kind:
        case "run.status":
            status = data.get("status")
            body = {
                "status": status,
                "status_display": RunStatus(status).label if status else "",
                "end_reason": data.get("end_reason"),
                "comment": event.comment,
            }
        case "run.message":
            tool_result = None
            if event.kind == RunEventKind.TOOL_RESULT:
                text = "".join(p.get("text", "") for p in data.get("content", []))
                tool_result = {
                    "call_id": data["call_id"],
                    "name": data["name"],
                    "is_error": data.get("is_error", False),
                    "text": text,
                }
            body = {
                "kind": event.kind,
                "text": data.get("text", ""),
                "tool_calls": data.get("tool_calls", []),
                "tool_result": tool_result,
            }
        case "clarification.requested":
            body = {"clarification": data["clarification"], "kind": data.get("kind", ClarificationKind.QUESTION)}
        case _:
            body = {"kind": event.kind, "kind_display": RunEventKind(event.kind).label, "comment": event.comment}
    return EVENT_TYPES[kind]({**base, **body}).data


class RunEventSerializer(serializers.BaseSerializer):
    """RunEventの一覧のAPIで使う。種類ごとの形は RunEventSchema（OpenAPI）"""

    def to_representation(self, instance: RunEvent) -> dict[str, Any]:
        return serialize_event(instance, self.context["run_uid"])
