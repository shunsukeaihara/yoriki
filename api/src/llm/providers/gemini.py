"""Google GeminiのAPI（Google AI Studio のAPI key）"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
from google import genai
from google.genai import errors, types

from utils.fields import generate_uid

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
    status_error,
    timeout_error,
)

STOP_REASONS = {types.FinishReason.STOP: StopReason.END, types.FinishReason.MAX_TOKENS: StopReason.MAX_TOKENS}


def _content(message: Message) -> types.Content:
    if message.role == "tool":
        if not message.tool_name:
            raise ValueError("Geminiでは、toolのメッセージに tool_name が必要です")
        response = types.FunctionResponse(
            id=message.tool_call_id or None, name=message.tool_name, response={"result": message.content}
        )
        return types.Content(role="user", parts=[types.Part(function_response=response)])
    parts = [types.Part(text=message.content)] if message.content else []
    parts += [
        types.Part(function_call=types.FunctionCall(id=c.id, name=c.name, args=c.arguments)) for c in message.tool_calls
    ]
    return types.Content(role="model" if message.role == "assistant" else "user", parts=parts)


class GeminiAdapter(ProviderAdapter):
    def _client(self) -> genai.Client:
        return genai.Client(
            api_key=self.connection.get_api_key(),
            http_options=types.HttpOptions(
                timeout=self.connection.timeout_seconds * 1000,  # ミリ秒
                retry_options=types.HttpRetryOptions(attempts=1),  # 再試行はRunnerが判断する
                httpx_client=self.http_client,
            ),
        )

    def stream(self, request: ChatRequest) -> Iterator[ChatEvent]:
        system = "\n\n".join(m.content for m in request.messages if m.role == "system")
        config = types.GenerateContentConfig(
            system_instruction=system or None,
            max_output_tokens=request.max_output_tokens,
            temperature=request.temperature,
            # Toolの実行はRunnerが行う。SDKに関数を自動で呼ばせない
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        if request.tools:
            config.tools = [
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=t.name, description=t.description, parameters_json_schema=t.parameters
                        )
                        for t in request.tools
                    ]
                )
            ]
        contents = [_content(m) for m in request.messages if m.role != "system"]

        tool_calls: list[ToolCall] = []
        usage = Usage(0, 0)
        finish_reason: Any = None
        request_id = ""
        with _errors():
            for chunk in self._client().models.generate_content_stream(
                model=request.model, contents=contents, config=config
            ):
                request_id = chunk.response_id or request_id
                if meta := chunk.usage_metadata:
                    usage = Usage(
                        input_tokens=meta.prompt_token_count or 0,
                        # 推論（thinking）のtokenも出力として課金される
                        output_tokens=(meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0),
                        cached_input_tokens=meta.cached_content_token_count or 0,
                    )
                for candidate in chunk.candidates or []:
                    finish_reason = candidate.finish_reason or finish_reason
                    for part in (candidate.content.parts if candidate.content else None) or []:
                        if part.function_call:
                            call = part.function_call
                            # GeminiはIDを返さないことがある。結果を対応づけられるよう、ここで付ける
                            tool_calls.append(
                                ToolCall(call.id or generate_uid(), call.name or "", dict(call.args or {}))
                            )
                        elif part.text and not part.thought:
                            yield TextDelta(part.text)

        for tool_call in tool_calls:
            yield ToolCallRequested(tool_call)
        yield usage
        stop = StopReason.TOOL_USE if tool_calls else STOP_REASONS.get(finish_reason, StopReason.OTHER)
        yield Completed(stop, request_id)

    def list_models(self) -> list[str]:
        with _errors():
            return sorted((model.name or "").removeprefix("models/") for model in self._client().models.list())


@contextmanager
def _errors() -> Iterator[None]:
    """SDKの例外を ProviderError にする"""
    try:
        yield
    except httpx.TimeoutException as exc:
        raise timeout_error() from exc
    except httpx.TransportError as exc:
        raise connection_error() from exc
    except errors.APIError as exc:
        raise status_error(exc.code, provider_message=exc.message or "") from exc
