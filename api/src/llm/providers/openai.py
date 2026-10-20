"""OpenAIと、OpenAI互換のAPIを持つローカルLLM（vLLM、Ollama、LM Studio、llama.cpp server等）。Chat Completionsで呼ぶ"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import openai

from ..models import ProviderKind
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
    incomplete_stream_error,
    parse_arguments,
    status_error,
    timeout_error,
)

STOP_REASONS = {"stop": StopReason.END, "tool_calls": StopReason.TOOL_USE, "length": StopReason.MAX_TOKENS}


def _message(message: Message) -> dict[str, Any]:
    if message.role == "tool":
        return {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
    data: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        data["content"] = message.content or None
        data["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)},
            }
            for call in message.tool_calls
        ]
    return data


class OpenAIAdapter(ProviderAdapter):
    def _client(self) -> openai.OpenAI:
        local = self.connection.kind == ProviderKind.LOCAL
        return openai.OpenAI(
            # SDKはAPI keyを必須にするので、API keyのないローカルLLMには仮の値を渡す
            api_key=self.connection.api_key.reveal() or "not-required",
            base_url=self.connection.base_url if local else None,
            timeout=self.connection.timeout_seconds,
            max_retries=0,  # 再試行と代替モデルへの切替えはRunnerが判断する
            http_client=self.http_client,
        )

    def stream(self, request: ChatRequest) -> Iterator[ChatEvent]:
        params: dict[str, Any] = {
            "model": request.model,
            "messages": [_message(m) for m in request.messages],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.tools:
            params["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in request.tools
            ]
        if request.max_output_tokens is not None:
            # OpenAIの新しいモデルは max_tokens を受け付けない。ローカルLLMのサーバーは max_tokens だけを知るものが多い
            key = "max_tokens" if self.connection.kind == ProviderKind.LOCAL else "max_completion_tokens"
            params[key] = request.max_output_tokens
        if request.temperature is not None:
            params["temperature"] = request.temperature

        with _errors():
            stream = self._client().chat.completions.create(**params)
            request_id = stream.response.headers.get("x-request-id", "")
            tool_calls: dict[int, dict[str, str]] = {}
            finish_reason = ""
            usage: Usage | None = None
            for chunk in stream:
                if chunk.usage:
                    details = chunk.usage.prompt_tokens_details
                    usage = Usage(
                        input_tokens=chunk.usage.prompt_tokens,
                        output_tokens=chunk.usage.completion_tokens,
                        cached_input_tokens=(details.cached_tokens or 0) if details else 0,
                    )
                for choice in chunk.choices:
                    if choice.delta.content:
                        yield TextDelta(choice.delta.content)
                    # Tool呼出しは、名前と引数が分割されて届くので、indexごとにつなげてから返す
                    for delta in choice.delta.tool_calls or []:
                        call = tool_calls.setdefault(delta.index, {"id": "", "name": "", "arguments": ""})
                        call["id"] = delta.id or call["id"]
                        if delta.function:
                            call["name"] += delta.function.name or ""
                            call["arguments"] += delta.function.arguments or ""
                    finish_reason = choice.finish_reason or finish_reason

        # 終わりの合図がないまま切れた応答は完了にしない（途中までの文と0の利用量を返さない）。
        # 利用量の塊は停止の理由の後に届く。ローカルLLMのサーバーには利用量を返さないものがあるので、そこだけは許す
        if not finish_reason or (usage is None and self.connection.kind != ProviderKind.LOCAL):
            raise incomplete_stream_error()

        for _index, call in sorted(tool_calls.items()):
            yield ToolCallRequested(ToolCall(call["id"], call["name"], parse_arguments(call["arguments"])))
        yield usage or Usage(0, 0)
        yield Completed(STOP_REASONS.get(finish_reason, StopReason.OTHER), request_id)

    def list_models(self) -> list[str]:
        with _errors():
            return sorted(model.id for model in self._client().models.list())


@contextmanager
def _errors() -> Iterator[None]:
    """SDKの例外を ProviderError にする"""
    try:
        yield
    except openai.APITimeoutError as exc:
        raise timeout_error() from exc
    except openai.APIConnectionError as exc:
        raise connection_error() from exc
    except openai.APIStatusError as exc:
        raise status_error(exc.status_code, request_id=exc.request_id or "", provider_message=exc.message) from exc
