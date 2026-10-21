"""テスト用のProvider Adapter。決めた応答を順に返し、送られた要求を記録する"""

from collections.abc import Iterator
from typing import Any

from llm.providers.base import (
    ChatEvent,
    ChatRequest,
    Completed,
    ProviderAdapter,
    ProviderError,
    ProviderState,
    StopReason,
    TextDelta,
    ToolCall,
    ToolCallRequested,
    Usage,
)


def reply(text: str, *, stop: StopReason = StopReason.END, state: dict[str, Any] | None = None) -> list[ChatEvent]:
    events: list[ChatEvent] = [TextDelta(text)] if text else []
    if state:
        events.append(ProviderState(state))
    return [*events, Usage(100, 20), Completed(stop, "req-1")]


def calls(*items: tuple[str, str, dict[str, Any]], text: str = "") -> list[ChatEvent]:
    events: list[ChatEvent] = [TextDelta(text)] if text else []
    events += [ToolCallRequested(ToolCall(call_id, name, args)) for call_id, name, args in items]
    return [*events, Usage(100, 20), Completed(StopReason.TOOL_USE, "req-2")]


class FakeAdapter(ProviderAdapter):
    def __init__(self, script: list[list[ChatEvent] | ProviderError]):
        self.script = list(script)
        self.requests: list[ChatRequest] = []

    def __call__(self, connection, **kwargs) -> "FakeAdapter":
        # Runnerの adapter_factory として使う
        return self

    def stream(self, request: ChatRequest) -> Iterator[ChatEvent]:
        self.requests.append(request)
        if not self.script:
            raise AssertionError("no more scripted responses")
        item = self.script.pop(0)
        if isinstance(item, ProviderError):
            raise item
        yield from item

    def list_models(self) -> list[str]:
        return []
