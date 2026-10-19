"""Anthropic（Claude）のMessages API"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import anthropic

from .base import (
    ChatEvent,
    ChatRequest,
    Completed,
    Message,
    ProviderAdapter,
    StopReason,
    TextDelta,
    ToolCall,
    ToolCallRequested,
    Usage,
    connection_error,
    parse_arguments,
    status_error,
    timeout_error,
)

STOP_REASONS = {"end_turn": StopReason.END, "tool_use": StopReason.TOOL_USE, "max_tokens": StopReason.MAX_TOKENS}
# Messages APIでは最大出力tokenが必須。指定がなければこの値にする
DEFAULT_MAX_OUTPUT_TOKENS = 4096


def _blocks(message: Message) -> list[dict[str, Any]]:
    if message.role == "tool":
        return [{"type": "tool_result", "tool_use_id": message.tool_call_id, "content": message.content}]
    blocks: list[dict[str, Any]] = [{"type": "text", "text": message.content}] if message.content else []
    blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in message.tool_calls]
    return blocks


def _messages(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    """systemを分け、Toolの結果はuserの発言にする。同じroleが続く場合は1つにまとめる（Messages APIの制約）"""
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    result: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            continue
        role = "assistant" if message.role == "assistant" else "user"
        if result and result[-1]["role"] == role:
            result[-1]["content"] += _blocks(message)
        else:
            result.append({"role": role, "content": _blocks(message)})
    return system, result


class AnthropicAdapter(ProviderAdapter):
    def _client(self) -> anthropic.Anthropic:
        return anthropic.Anthropic(
            api_key=self.connection.get_api_key(),
            timeout=self.connection.timeout_seconds,
            max_retries=0,  # 再試行と代替モデルへの切替えはRunnerが判断する
            http_client=self.http_client,
        )

    def stream(self, request: ChatRequest) -> Iterator[ChatEvent]:
        system, messages = _messages(list(request.messages))
        params: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS,
            "stream": True,
        }
        if system:
            params["system"] = system
        if request.tools:
            params["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in request.tools
            ]
        if request.temperature is not None:
            params["temperature"] = request.temperature

        with _errors():
            stream = self._client().messages.create(**params)
            request_id = stream.response.headers.get("request-id", "")
            tool_calls: dict[int, dict[str, str]] = {}
            input_tokens = cached_tokens = output_tokens = 0
            stop_reason = ""
            for event in stream:
                match event.type:
                    case "message_start":
                        usage = event.message.usage
                        cached_tokens = usage.cache_read_input_tokens or 0
                        # input_tokens はキャッシュから読んだ分を含まないので、足して全体の入力にする
                        input_tokens = usage.input_tokens + cached_tokens + (usage.cache_creation_input_tokens or 0)
                    case "content_block_start" if event.content_block.type == "tool_use":
                        block = event.content_block
                        tool_calls[event.index] = {"id": block.id, "name": block.name, "arguments": ""}
                    case "content_block_delta" if event.delta.type == "text_delta":
                        yield TextDelta(event.delta.text)
                    case "content_block_delta" if event.delta.type == "input_json_delta":
                        tool_calls[event.index]["arguments"] += event.delta.partial_json
                    case "message_delta":
                        stop_reason = event.delta.stop_reason or stop_reason
                        output_tokens = event.usage.output_tokens

        for _index, call in sorted(tool_calls.items()):
            yield ToolCallRequested(ToolCall(call["id"], call["name"], parse_arguments(call["arguments"])))
        yield Usage(input_tokens, output_tokens, cached_tokens)
        yield Completed(STOP_REASONS.get(stop_reason, StopReason.OTHER), request_id)

    def list_models(self) -> list[str]:
        with _errors():
            return sorted(model.id for model in self._client().models.list())


@contextmanager
def _errors() -> Iterator[None]:
    """SDKの例外を ProviderError にする"""
    try:
        yield
    except anthropic.APITimeoutError as exc:
        raise timeout_error() from exc
    except anthropic.APIConnectionError as exc:
        raise connection_error() from exc
    except anthropic.APIStatusError as exc:
        raise status_error(exc.status_code, request_id=exc.request_id or "", provider_message=exc.message) from exc
