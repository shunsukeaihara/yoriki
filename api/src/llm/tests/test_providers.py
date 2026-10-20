"""Provider Adapterのテスト。各社のAPIの応答（SSE）を差し替えたHTTPクライアントで返し、共通の型への変換を確かめる"""

import json
from collections.abc import Sequence

import httpx
import httpx2
import pytest

from llm.factories import LLMConnectionFactory
from llm.models import ProviderKind
from llm.providers import get_adapter
from llm.providers.base import (
    ChatRequest,
    Completed,
    Message,
    ProviderError,
    StopReason,
    TextDelta,
    ToolCall,
    ToolCallRequested,
    ToolDefinition,
    Usage,
)

WEATHER = ToolDefinition("get_weather", "天気を返す", {"type": "object", "properties": {"city": {"type": "string"}}})
REQUEST = ChatRequest(
    model="test-model",
    messages=[
        Message("system", "簡潔に答える"),
        Message("user", "東京の天気は？"),
        Message("assistant", tool_calls=(ToolCall("call_0", "get_weather", {"city": "大阪"}),)),
        Message("tool", "晴れ", tool_call_id="call_0", tool_name="get_weather"),
    ],
    tools=[WEATHER],
    max_output_tokens=100,
)


def _sse(events: Sequence[tuple[str | None, dict | str]]) -> bytes:
    lines = []
    for name, data in events:
        if name:
            lines.append(f"event: {name}")
        lines.append(f"data: {data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)}")
        lines.append("")
    return ("\n".join(lines) + "\n").encode()


class Recorder:
    """送られた要求を記録し、決めた応答を返す"""

    def __init__(self, status: int = 200, body: bytes | dict = b"", headers: dict | None = None):
        self.status, self.body, self.headers = status, body, headers or {}
        self.requests: list = []

    def __call__(self, request):
        self.requests.append(request)
        body = self.body
        if isinstance(body, dict):
            content, content_type = json.dumps(body).encode(), "application/json"
        else:
            content, content_type = body, "text/event-stream"
        module = httpx2 if isinstance(request, httpx2.Request) else httpx
        return module.Response(
            self.status, content=content, headers={"content-type": content_type, **self.headers}, request=request
        )

    @property
    def sent(self) -> dict:
        return json.loads(self.requests[-1].content)


def _httpx2(recorder: Recorder) -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(recorder))


class TestOpenAI:
    BODY = _sse(
        [
            (None, {"id": "c", "object": "chat.completion.chunk", "created": 1, "model": "m",
                    "choices": [{"index": 0, "delta": {"content": "東京は"}, "finish_reason": None}]}),
            (None, {"id": "c", "object": "chat.completion.chunk", "created": 1, "model": "m",
                    "choices": [{"index": 0, "delta": {"tool_calls": [
                        {"index": 0, "id": "call_1", "type": "function",
                         "function": {"name": "get_weather", "arguments": "{\"ci"}}]}, "finish_reason": None}]}),
            (None, {"id": "c", "object": "chat.completion.chunk", "created": 1, "model": "m",
                    "choices": [{"index": 0, "delta": {"tool_calls": [
                        {"index": 0, "function": {"arguments": "ty\": \"東京\"}"}}]}, "finish_reason": "tool_calls"}]}),
            (None, {"id": "c", "object": "chat.completion.chunk", "created": 1, "model": "m", "choices": [],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 7, "total_tokens": 57,
                              "prompt_tokens_details": {"cached_tokens": 20}}}),
            (None, "[DONE]"),
        ]
    )  # fmt: skip

    @pytest.mark.django_db
    def test_stream(self):
        recorder = Recorder(body=self.BODY, headers={"x-request-id": "req_1"})
        connection = LLMConnectionFactory.create(kind=ProviderKind.OPENAI)
        events = list(get_adapter(connection, http_client=_httpx2(recorder)).stream(REQUEST))

        assert events == [
            TextDelta("東京は"),
            ToolCallRequested(ToolCall("call_1", "get_weather", {"city": "東京"})),
            Usage(input_tokens=50, output_tokens=7, cached_input_tokens=20),
            Completed(StopReason.TOOL_USE, "req_1"),
        ]
        request = recorder.requests[-1]
        assert str(request.url) == "https://api.openai.com/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer sk-test-0123456789abcd"
        sent = recorder.sent
        assert sent["max_completion_tokens"] == 100
        assert sent["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"city": "大阪"}'
        assert sent["messages"][3] == {"role": "tool", "tool_call_id": "call_0", "content": "晴れ"}
        assert sent["tools"][0]["function"]["name"] == "get_weather"

    @pytest.mark.django_db
    def test_incomplete_stream_is_an_error(self):
        # 停止の理由と利用量が届く前に切れた。途中までの文を完了として返さない
        chunks = [json.loads(line[6:]) for line in self.BODY.decode().split("\n") if line.startswith("data: {")]
        recorder = Recorder(body=_sse([(None, chunks[0])]))
        connection = LLMConnectionFactory.create(kind=ProviderKind.OPENAI)
        events = []
        with pytest.raises(ProviderError) as info:
            for event in get_adapter(connection, http_client=_httpx2(recorder)).stream(REQUEST):
                events.append(event)
        assert info.value.retryable
        assert events == [TextDelta("東京は")]

    @pytest.mark.django_db
    def test_local_may_omit_usage(self):
        # ローカルLLMのサーバーには利用量を返さないものがある。停止の理由があれば完了にする
        chunks = [json.loads(line[6:]) for line in self.BODY.decode().split("\n") if line.startswith("data: {")]
        body = _sse([(None, c) for c in chunks if "usage" not in c] + [(None, "[DONE]")])
        recorder = Recorder(body=body)
        connection = LLMConnectionFactory.create(kind=ProviderKind.LOCAL, base_url="http://llm.internal/v1", api_key="")
        events = list(get_adapter(connection, http_client=_httpx2(recorder)).stream(REQUEST))
        assert events[-2:] == [Usage(0, 0), Completed(StopReason.TOOL_USE, "")]

    @pytest.mark.django_db
    def test_local_uses_base_url_without_key(self):
        recorder = Recorder(
            body={"object": "list", "data": [{"id": "llama", "object": "model", "created": 1, "owned_by": "me"}]}
        )
        connection = LLMConnectionFactory.create(
            kind=ProviderKind.LOCAL, base_url="http://llm.internal:8000/v1", api_key=""
        )
        assert get_adapter(connection, http_client=_httpx2(recorder)).list_models() == ["llama"]
        assert str(recorder.requests[-1].url) == "http://llm.internal:8000/v1/models"

    @pytest.mark.django_db
    @pytest.mark.parametrize(("status", "retryable"), [(401, False), (429, True), (503, True), (400, False)])
    def test_errors(self, status, retryable):
        recorder = Recorder(status=status, body={"error": {"message": "provider says no"}})
        connection = LLMConnectionFactory.create()
        with pytest.raises(ProviderError) as info:
            list(get_adapter(connection, http_client=_httpx2(recorder)).stream(REQUEST))
        assert (info.value.status_code, info.value.retryable) == (status, retryable)
        assert info.value.details["provider_message"]
        assert "sk-test" not in str(info.value)

    @pytest.mark.django_db
    def test_connection_error(self):
        def refuse(request):
            raise httpx2.ConnectError("refused", request=request)

        connection = LLMConnectionFactory.create()
        client = httpx2.Client(transport=httpx2.MockTransport(refuse))
        with pytest.raises(ProviderError) as info:
            get_adapter(connection, http_client=client).list_models()
        assert info.value.retryable


class TestAnthropic:
    BODY = _sse(
        [
            ("message_start", {"type": "message_start", "message": {
                "id": "msg_1", "type": "message", "role": "assistant", "model": "m", "content": [],
                "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 1, "cache_read_input_tokens": 30,
                          "cache_creation_input_tokens": 5}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "text_delta", "text": "調べます"}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {
                "type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {}}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 1,
                                     "delta": {"type": "input_json_delta", "partial_json": "{\"city\":"}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 1,
                                     "delta": {"type": "input_json_delta", "partial_json": " \"東京\"}"}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 1}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                               "usage": {"output_tokens": 12}}),
            ("message_stop", {"type": "message_stop"}),
        ]
    )  # fmt: skip

    @pytest.mark.django_db
    def test_stream(self):
        recorder = Recorder(body=self.BODY, headers={"request-id": "req_a"})
        connection = LLMConnectionFactory.create(kind=ProviderKind.ANTHROPIC)
        events = list(get_adapter(connection, http_client=_httpx2(recorder)).stream(REQUEST))

        assert events == [
            TextDelta("調べます"),
            ToolCallRequested(ToolCall("toolu_1", "get_weather", {"city": "東京"})),
            Usage(input_tokens=45, output_tokens=12, cached_input_tokens=30),
            Completed(StopReason.TOOL_USE, "req_a"),
        ]
        request = recorder.requests[-1]
        assert request.headers["x-api-key"] == "sk-test-0123456789abcd"
        sent = recorder.sent
        assert sent["system"] == "簡潔に答える"
        assert sent["max_tokens"] == 100
        # assistantのTool呼出しと、userの発言にしたToolの結果が交互に並ぶ
        assert [m["role"] for m in sent["messages"]] == ["user", "assistant", "user"]
        assert sent["messages"][1]["content"] == [
            {"type": "tool_use", "id": "call_0", "name": "get_weather", "input": {"city": "大阪"}}
        ]
        assert sent["messages"][2]["content"] == [{"type": "tool_result", "tool_use_id": "call_0", "content": "晴れ"}]
        assert sent["tools"][0]["input_schema"] == WEATHER.parameters

    @pytest.mark.django_db
    def test_incomplete_stream_is_an_error(self):
        # message_delta と message_stop の前に切れた
        truncated = self.BODY.decode().split("event: message_delta")[0].encode()
        recorder = Recorder(body=truncated)
        connection = LLMConnectionFactory.create(kind=ProviderKind.ANTHROPIC)
        with pytest.raises(ProviderError) as info:
            list(get_adapter(connection, http_client=_httpx2(recorder)).stream(REQUEST))
        assert info.value.retryable

    @pytest.mark.django_db
    def test_overloaded_is_retryable(self):
        recorder = Recorder(status=529, body={"type": "error", "error": {"type": "overloaded_error", "message": "x"}})
        connection = LLMConnectionFactory.create(kind=ProviderKind.ANTHROPIC)
        with pytest.raises(ProviderError) as info:
            list(get_adapter(connection, http_client=_httpx2(recorder)).stream(REQUEST))
        assert info.value.retryable


class TestGemini:
    BODY = _sse(
        [
            (None, {"candidates": [{"content": {"role": "model", "parts": [
                {"text": "考え中", "thought": True}, {"text": "東京は"}]}}], "responseId": "resp_g"}),
            (None, {"candidates": [{"content": {"role": "model", "parts": [
                {"functionCall": {"name": "get_weather", "args": {"city": "東京"}}}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 40, "candidatesTokenCount": 6, "thoughtsTokenCount": 4,
                                  "cachedContentTokenCount": 8},
                "responseId": "resp_g"}),
        ]
    )  # fmt: skip

    @pytest.mark.django_db
    def test_stream(self):
        recorder = Recorder(body=self.BODY)
        connection = LLMConnectionFactory.create(kind=ProviderKind.GEMINI)
        client = httpx.Client(transport=httpx.MockTransport(recorder))
        events = list(get_adapter(connection, http_client=client).stream(REQUEST))

        assert events[0] == TextDelta("東京は")
        assert isinstance(events[1], ToolCallRequested)
        assert (events[1].call.name, events[1].call.arguments) == ("get_weather", {"city": "東京"})
        assert events[1].call.id  # IDがなければ付ける
        assert events[2:] == [
            Usage(input_tokens=40, output_tokens=10, cached_input_tokens=8),
            Completed(StopReason.TOOL_USE, "resp_g"),
        ]
        request = recorder.requests[-1]
        assert "test-model:streamGenerateContent" in str(request.url)
        assert request.headers["x-goog-api-key"] == "sk-test-0123456789abcd"
        sent = recorder.sent
        assert sent["systemInstruction"]["parts"][0]["text"] == "簡潔に答える"
        assert [c["role"] for c in sent["contents"]] == ["user", "model", "user"]
        assert sent["contents"][2]["parts"][0]["functionResponse"]["name"] == "get_weather"
        assert sent["generationConfig"]["maxOutputTokens"] == 100

    @pytest.mark.django_db
    def test_incomplete_stream_is_an_error(self):
        # finishReason と usageMetadata を持つ最後の塊が届く前に切れた
        truncated = self.BODY.decode().split("\n\n")[0].encode() + b"\n\n"
        recorder = Recorder(body=truncated)
        connection = LLMConnectionFactory.create(kind=ProviderKind.GEMINI)
        client = httpx.Client(transport=httpx.MockTransport(recorder))
        with pytest.raises(ProviderError) as info:
            list(get_adapter(connection, http_client=client).stream(REQUEST))
        assert info.value.retryable

    @pytest.mark.django_db
    def test_error(self):
        recorder = Recorder(
            status=403, body={"error": {"code": 403, "message": "denied", "status": "PERMISSION_DENIED"}}
        )
        connection = LLMConnectionFactory.create(kind=ProviderKind.GEMINI)
        client = httpx.Client(transport=httpx.MockTransport(recorder))
        with pytest.raises(ProviderError) as info:
            get_adapter(connection, http_client=client).list_models()
        assert (info.value.status_code, info.value.retryable) == (403, False)
