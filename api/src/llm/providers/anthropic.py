"""Anthropic（Claude）のMessages API"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import anthropic

from .base import (
    ChatEvent,
    ChatRequest,
    Completed,
    DocumentPart,
    ImagePart,
    Message,
    Part,
    ProviderAdapter,
    ProviderState,
    StopReason,
    TextDelta,
    TextPart,
    ToolCall,
    ToolCallRequested,
    ToolChoice,
    Usage,
    b64,
    connection_error,
    context_overflow_error,
    document_text,
    incomplete_stream_error,
    is_context_overflow,
    object_schema,
    parse_arguments,
    status_error,
    thinking_budget,
    timeout_error,
    unsupported_part_error,
)

STOP_REASONS = {
    "end_turn": StopReason.END,
    "stop_sequence": StopReason.END,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.MAX_TOKENS,
    "model_context_window_exceeded": StopReason.MAX_TOKENS,
}
# Messages APIでは最大出力tokenが必須。指定がなければこの値にする
DEFAULT_MAX_OUTPUT_TOKENS = 4096
CACHE_CONTROL = {"type": "ephemeral"}
# 推論の途中経過のブロック。cacheの区切りを付けられない
THINKING_TYPES = {"thinking", "redacted_thinking"}


def _part(part: Part) -> dict[str, Any]:
    if isinstance(part, TextPart):
        return {"type": "text", "text": part.text}
    if isinstance(part, ImagePart):
        return {"type": "image", "source": {"type": "base64", "media_type": part.media_type, "data": b64(part.data)}}
    if isinstance(part, DocumentPart):
        if part.media_type == "text/plain":
            source = {"type": "text", "media_type": "text/plain", "data": document_text(part)}
        else:
            source = {"type": "base64", "media_type": part.media_type, "data": b64(part.data)}
        block: dict[str, Any] = {"type": "document", "source": source}
        if part.name:
            block["title"] = part.name
        return block
    raise unsupported_part_error(part, "Anthropic")


def _blocks(message: Message) -> list[dict[str, Any]]:
    # 空の文字列のブロックは受け付けられない
    parts = [_part(p) for p in message.parts if not (isinstance(p, TextPart) and not p.text)]
    if message.role == "tool":
        result: dict[str, Any] = {"type": "tool_result", "tool_use_id": message.tool_call_id, "content": parts}
        if message.is_error:
            result["is_error"] = True
        return [result]
    if message.role == "user":
        return parts
    # 同じ応答のthinkingブロック（署名付き）を、その発言の先頭にそのまま戻す（harness.md §5.5）
    blocks: list[dict[str, Any]] = list((message.provider_state or {}).get("blocks", []))
    blocks += parts
    blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in message.tool_calls]
    return blocks


def _messages(messages: Sequence[Message]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """systemを分け、Toolの結果はuserの発言にする。同じroleが続く場合は1つにまとめる（Messages APIの制約）"""
    system = [{"type": "text", "text": m.text} for m in messages if m.role == "system" and m.text]
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


def _mark_cache(system: list[dict[str, Any]], tools: list[dict[str, Any]], messages: list[dict[str, Any]]) -> None:
    """cacheの区切りを、systemの最後、Toolの定義の最後、最後の2つの発言の最後のブロックに付ける（上限の4つ）"""
    if system:
        system[-1]["cache_control"] = CACHE_CONTROL
    if tools:
        tools[-1]["cache_control"] = CACHE_CONTROL
    for message in messages[-2:]:
        blocks = [b for b in message["content"] if b["type"] not in THINKING_TYPES]
        if blocks:
            blocks[-1]["cache_control"] = CACHE_CONTROL


def _tool_choice(request: ChatRequest) -> dict[str, Any] | None:
    if request.forced_tool:
        choice: dict[str, Any] = {"type": "tool", "name": request.forced_tool}
    else:
        types = {ToolChoice.AUTO: "auto", ToolChoice.NONE: "none", ToolChoice.REQUIRED: "any"}
        choice = {"type": types[request.tool_choice]}
    if not request.parallel_tool_calls and choice["type"] != "none":
        choice["disable_parallel_tool_use"] = True
    # 既定（auto・並列可）なら送らない
    return None if choice == {"type": "auto"} else choice


def _params(request: ChatRequest) -> dict[str, Any]:
    system, messages = _messages(request.messages)
    tools = [
        {"name": t.name, "description": t.description, "input_schema": object_schema(t.parameters)}
        for t in request.tools
    ]
    if request.use_cache:
        _mark_cache(system, tools, messages)
    params: dict[str, Any] = {
        "model": request.model,
        "messages": messages,
        "max_tokens": request.max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS,
        "stream": True,
    }
    if system:
        params["system"] = system
    if tools:
        params["tools"] = tools
        if choice := _tool_choice(request):
            params["tool_choice"] = choice
    if request.temperature is not None:
        params["temperature"] = request.temperature
    if (budget := thinking_budget(request.effort)) is not None:
        # 数字だけのeffortは、thinkingのtoken予算（budget_tokens を受け付けるモデル）。予算は最大出力tokenより小さくする
        params["thinking"] = {"type": "enabled", "budget_tokens": budget}
        params["max_tokens"] = max(params["max_tokens"], budget + DEFAULT_MAX_OUTPUT_TOKENS)
    elif request.effort:
        # 水準の名前（low・medium・high等）。thinkingの量はモデルに任せる（adaptive）
        params["thinking"] = {"type": "adaptive"}
        params["output_config"] = {"effort": request.effort}
    return params


class _Blocks:
    """応答の文以外のブロック（Tool呼出しとthinking）を、届いた順（index）に組み立てる"""

    def __init__(self) -> None:
        self.tool_calls: dict[int, dict[str, str]] = {}
        # 推論の途中経過のブロック。署名ごと、次の要求で戻す
        self.thinking: dict[int, dict[str, Any]] = {}

    def start(self, index: int, block: Any) -> None:
        if block.type == "tool_use":
            self.tool_calls[index] = {"id": block.id, "name": block.name, "arguments": ""}
        elif block.type == "thinking":
            self.thinking[index] = {
                "type": "thinking",
                "thinking": getattr(block, "thinking", "") or "",
                "signature": getattr(block, "signature", "") or "",
            }
        elif block.type == "redacted_thinking":
            self.thinking[index] = {"type": "redacted_thinking", "data": block.data}

    def add(self, index: int, delta: Any) -> str:
        """差分をつなげる。文の差分なら、その文を返す"""
        if delta.type == "text_delta":
            return delta.text
        if delta.type == "input_json_delta":
            self.tool_calls[index]["arguments"] += delta.partial_json
        elif delta.type == "thinking_delta":
            self.thinking[index]["thinking"] += delta.thinking
        elif delta.type == "signature_delta":
            self.thinking[index]["signature"] += delta.signature
        return ""

    def events(self) -> Iterator[ChatEvent]:
        for _index, call in sorted(self.tool_calls.items()):
            yield ToolCallRequested(ToolCall(call["id"], call["name"], parse_arguments(call["arguments"])))
        if self.thinking:
            yield ProviderState({"blocks": [block for _index, block in sorted(self.thinking.items())]})


class AnthropicAdapter(ProviderAdapter):
    def _client(self) -> anthropic.Anthropic:
        return anthropic.Anthropic(
            api_key=self.connection.api_key.reveal(),
            timeout=self.connection.timeout_seconds,
            max_retries=0,  # 再試行と代替モデルへの切替えはRunnerが判断する
            http_client=self.http_client,
        )

    def stream(self, request: ChatRequest) -> Iterator[ChatEvent]:
        params = _params(request)
        blocks = _Blocks()
        with _errors():
            stream = self._client().messages.create(**params)
            request_id = stream.response.headers.get("request-id", "")
            input_tokens = cached_tokens = cache_write_tokens = output_tokens = 0
            stop_reason = ""
            stopped = False
            for event in stream:
                match event.type:
                    case "message_start":
                        usage = event.message.usage
                        cached_tokens = usage.cache_read_input_tokens or 0
                        cache_write_tokens = usage.cache_creation_input_tokens or 0
                        # input_tokens はcacheから読んだ分・書いた分を含まないので、足して全体の入力にする
                        input_tokens = usage.input_tokens + cached_tokens + cache_write_tokens
                    case "content_block_start":
                        blocks.start(event.index, event.content_block)
                    case "content_block_delta":
                        if text := blocks.add(event.index, event.delta):
                            yield TextDelta(text)
                    case "message_delta":
                        stop_reason = event.delta.stop_reason or stop_reason
                        output_tokens = event.usage.output_tokens
                    case "message_stop":
                        stopped = True

        # 終わりの合図（message_delta の停止の理由と message_stop）がないまま切れた応答は完了にしない
        if not stopped or not stop_reason:
            raise incomplete_stream_error()

        yield from blocks.events()
        yield Usage(input_tokens, output_tokens, cached_tokens, cache_write_tokens)
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
        request_id = exc.request_id or ""
        if is_context_overflow(exc.status_code, exc.message):
            raise context_overflow_error(exc.status_code, request_id=request_id, provider_message=exc.message) from exc
        raise status_error(exc.status_code, request_id=request_id, provider_message=exc.message) from exc
