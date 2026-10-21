"""OpenAIの公式API。Responses APIで呼ぶ（harness.md §12）。

Tool結果に画像・ファイルを入れられるのと、推論の途中経過（暗号化されたreasoning）を次の要求へ戻せるのが、
Responses APIだけのため。会話の状態はOpenAIに保存させず（`store: false`）、毎回すべての入力を送る。
"""

import json
from collections.abc import Iterator, Sequence
from typing import Any

from django.utils.translation import gettext as _

from .base import (
    ChatEvent,
    ChatRequest,
    Completed,
    DocumentPart,
    ImagePart,
    Message,
    Part,
    ProviderError,
    ProviderState,
    StopReason,
    TextDelta,
    TextPart,
    ToolCall,
    ToolCallRequested,
    Usage,
    context_overflow_error,
    data_url,
    document_text,
    incomplete_stream_error,
    is_context_overflow,
    object_schema,
    parse_arguments,
    thinking_budget,
    unsupported_budget_error,
    unsupported_part_error,
)
from .openai import OpenAIAdapter, errors

# 応答の途中のエラーのうち、時間をおけば通る見込みがあるもの
RETRYABLE_CODES = {"server_error", "rate_limit_exceeded"}


def _part(part: Part) -> dict[str, Any]:
    if isinstance(part, TextPart):
        return {"type": "input_text", "text": part.text}
    if isinstance(part, ImagePart):
        return {"type": "input_image", "image_url": data_url(part.media_type, part.data)}
    if isinstance(part, DocumentPart):
        if part.media_type == "text/plain":
            return {"type": "input_text", "text": document_text(part)}
        return {
            "type": "input_file",
            "filename": part.name or "document.pdf",
            "file_data": data_url(part.media_type, part.data),
        }
    raise unsupported_part_error(part, "OpenAI")


def _input(messages: Sequence[Message]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "user":
            items.append({"role": "user", "content": [_part(p) for p in message.parts]})
        elif message.role == "tool":
            # テキストだけなら文字列で返す。画像・ファイルがあれば部品の並びにする
            output: str | list[dict[str, Any]] = (
                message.text
                if all(isinstance(p, TextPart) for p in message.parts)
                else [_part(p) for p in message.parts]
            )
            items.append({"type": "function_call_output", "call_id": message.tool_call_id, "output": output})
        elif message.role == "assistant":
            # 同じ応答のreasoningを、その応答の発言とTool呼出しの前に戻す（harness.md §5.5）
            items += (message.provider_state or {}).get("items", [])
            if message.text:
                items.append({"role": "assistant", "content": message.text})
            items += [
                {
                    "type": "function_call",
                    "call_id": call.id,
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                }
                for call in message.tool_calls
            ]
    return items


def _reasoning_item(item: Any) -> dict[str, Any]:
    """応答のreasoningを、次の要求の入力に戻せる形にする。statusは入力では使わない"""
    data = item.model_dump(mode="json", exclude_none=True)
    data.pop("status", None)
    return data


def _stream_error(code: str, provider_message: str, request_id: str) -> ProviderError:
    if is_context_overflow(400, provider_message, code):
        return context_overflow_error(None, request_id=request_id, provider_message=provider_message)
    return ProviderError(
        _("プロバイダが応答の途中でエラーを返しました。"),
        retryable=code in RETRYABLE_CODES,
        request_id=request_id,
        details={"provider_message": provider_message, "code": code},
    )


def _params(request: ChatRequest) -> dict[str, Any]:
    instructions = "\n\n".join(m.text for m in request.messages if m.role == "system")
    params: dict[str, Any] = {
        "model": request.model,
        "input": _input([m for m in request.messages if m.role != "system"]),
        "stream": True,
        "store": False,
        # store: false では、reasoningを暗号化した内容で受け取り、次の要求で戻す
        "include": ["reasoning.encrypted_content"],
    }
    if instructions:
        params["instructions"] = instructions
    if request.tools:
        params["tools"] = [
            {
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": object_schema(t.parameters),
                # 引数の検証は全プロバイダで同じくRunnerが行う（harness.md §7.3）
                "strict": False,
            }
            for t in request.tools
        ]
        params["tool_choice"] = (
            {"type": "function", "name": request.forced_tool} if request.forced_tool else request.tool_choice.value
        )
        params["parallel_tool_calls"] = request.parallel_tool_calls
    if request.max_output_tokens is not None:
        params["max_output_tokens"] = request.max_output_tokens
    if request.temperature is not None:
        params["temperature"] = request.temperature
    if request.effort:
        if thinking_budget(request.effort) is not None:
            raise unsupported_budget_error("OpenAI")
        params["reasoning"] = {"effort": request.effort}
    return params


class OpenAIResponsesAdapter(OpenAIAdapter):
    def stream(self, request: ChatRequest) -> Iterator[ChatEvent]:
        params = _params(request)
        tool_calls: list[ToolCall] = []
        reasoning: list[dict[str, Any]] = []
        final: Any = None
        with errors():
            stream = self._client().responses.create(**params)
            request_id = stream.response.headers.get("x-request-id", "")
            for event in stream:
                match event.type:
                    case "response.output_text.delta":
                        yield TextDelta(event.delta)
                    case "response.output_item.done" if event.item.type == "function_call":
                        item = event.item
                        tool_calls.append(ToolCall(item.call_id, item.name, parse_arguments(item.arguments)))
                    case "response.output_item.done" if event.item.type == "reasoning":
                        # 暗号化された内容は、output_item.done のものが完全（added のものは途中のことがある）
                        reasoning.append(_reasoning_item(event.item))
                    case "response.completed" | "response.incomplete":
                        final = event.response
                    case "response.failed":
                        error = event.response.error
                        raise _stream_error(error.code if error else "", error.message if error else "", request_id)
                    case "error":
                        raise _stream_error(event.code or "", event.message, request_id)

        # 終わりの合図（response.completed か response.incomplete）がないまま切れた応答は完了にしない
        if final is None or final.usage is None:
            raise incomplete_stream_error()

        for call in tool_calls:
            yield ToolCallRequested(call)
        if reasoning:
            yield ProviderState({"items": reasoning})
        yield _usage(final.usage)
        if final.status == "incomplete":
            reason = final.incomplete_details.reason if final.incomplete_details else None
            stop = StopReason.MAX_TOKENS if reason == "max_output_tokens" else StopReason.OTHER
        else:
            stop = StopReason.TOOL_USE if tool_calls else StopReason.END
        yield Completed(stop, request_id or final.id or "")


def _usage(usage: Any) -> Usage:
    # SDKは応答を検証せずに組み立てるので、古いサーバーが返さない項目は無いものとして読む
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    return Usage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_input_tokens=getattr(input_details, "cached_tokens", 0) or 0,
        cache_write_tokens=getattr(input_details, "cache_write_tokens", 0) or 0,
        reasoning_tokens=getattr(output_details, "reasoning_tokens", 0) or 0,
    )
