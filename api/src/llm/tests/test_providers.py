"""Provider Adapterのテスト。各社のAPIの応答（SSE）を差し替えたHTTPクライアントで返し、共通の型への変換を確かめる"""

import base64
import json
from collections.abc import Sequence
from dataclasses import replace

import httpx
import httpx2
import pytest

from llm.factories import LLMConnectionFactory
from llm.models import ProviderKind
from llm.providers import get_adapter
from llm.providers.base import (
    ChatRequest,
    Completed,
    DocumentPart,
    ImagePart,
    Message,
    ProviderError,
    ProviderState,
    StopReason,
    TextDelta,
    TextPart,
    ToolCall,
    ToolCallRequested,
    ToolChoice,
    ToolDefinition,
    Usage,
    VideoPart,
    is_context_overflow,
    object_schema,
)
from llm.providers.gemini import MEDIA_NOTE, gemini_schema

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

PNG = b"\x89PNG-test"
PDF = b"%PDF-1.7 test"
IMAGE = ImagePart("image/png", PNG)
PDF_DOCUMENT = DocumentPart("application/pdf", PDF, name="請求書.pdf")
TEXT_DOCUMENT = DocumentPart("text/plain", "メモの本文".encode(), name="memo.txt")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _media_request(provider_state: dict | None = None, **kwargs) -> ChatRequest:
    """画像・文書を含む発言、画像を返すToolの結果、推論の途中経過を持つassistantの発言"""
    return ChatRequest(
        model="test-model",
        messages=[
            Message("system", "簡潔に答える"),
            Message("user", (TextPart("この請求書を読んで"), IMAGE, PDF_DOCUMENT)),
            Message(
                "assistant",
                "表を見ます",
                tool_calls=(
                    ToolCall("call_1", "render_page", {"page": 1}),
                    ToolCall("call_2", "get_weather", {"city": "東京"}),
                ),
                provider_state=provider_state,
            ),
            Message(
                "tool",
                (
                    TextPart("1ページ目"),
                    TextPart("<open>", with_media=True),
                    IMAGE,
                    TextPart("</close>", with_media=True),
                ),
                tool_call_id="call_1",
                tool_name="render_page",
            ),
            Message("tool", "見つかりません", tool_call_id="call_2", tool_name="get_weather", is_error=True),
        ],
        tools=[WEATHER],
        **kwargs,
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


def _httpx(recorder: Recorder) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(recorder))


class TestBase:
    def test_string_content_becomes_a_text_part(self):
        assert Message("user", "こんにちは").parts == (TextPart("こんにちは"),)
        assert Message("assistant", "").parts == ()

    def test_object_schema(self):
        assert object_schema({}) == {"type": "object", "properties": {}}
        schema = {"properties": {"a": {"type": "string"}}, "required": ["a"]}
        assert object_schema(schema) == {"type": "object", **schema}

    @pytest.mark.parametrize(
        ("status", "message", "code", "expected"),
        [
            (400, "This model's maximum context length is 8192 tokens.", "", True),
            (400, "prompt is too long: 210000 tokens > 200000 maximum", "", True),
            (400, "The input token count exceeds the maximum number of tokens allowed.", "", True),
            (400, "anything", "context_length_exceeded", True),
            (400, "invalid tool schema", "", False),
            (429, "too many tokens per minute", "", False),
        ],
    )
    def test_is_context_overflow(self, status, message, code, expected):
        assert is_context_overflow(status, message, code) is expected


class TestOpenAIResponses:
    REASONING = {"id": "rs_1", "type": "reasoning", "summary": [], "encrypted_content": "gAAAA-secret"}
    CALL = {
        "id": "fc_1",
        "type": "function_call",
        "call_id": "call_1",
        "name": "get_weather",
        "arguments": '{"city": "東京"}',
        "status": "completed",
    }
    RESPONSE = {
        "id": "resp_1",
        "object": "response",
        "created_at": 1,
        "model": "m",
        "output": [],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
    }
    USAGE = {
        "input_tokens": 50,
        "output_tokens": 7,
        "total_tokens": 57,
        "input_tokens_details": {"cached_tokens": 20, "cache_write_tokens": 4},
        "output_tokens_details": {"reasoning_tokens": 3},
    }
    BODY = _sse(
        [
            ("response.created", {"type": "response.created", "sequence_number": 0,
                                  "response": {**RESPONSE, "status": "in_progress"}}),
            ("response.output_item.done", {"type": "response.output_item.done", "sequence_number": 1,
                                           "output_index": 0, "item": {**REASONING, "status": "completed"}}),
            ("response.output_text.delta", {"type": "response.output_text.delta", "sequence_number": 2,
                                            "item_id": "msg_1", "output_index": 1, "content_index": 0,
                                            "delta": "東京は", "logprobs": []}),
            ("response.output_item.done", {"type": "response.output_item.done", "sequence_number": 3,
                                           "output_index": 2, "item": CALL}),
            ("response.completed", {"type": "response.completed", "sequence_number": 4,
                                    "response": {**RESPONSE, "status": "completed", "usage": USAGE}}),
        ]
    )  # fmt: skip

    def _stream(self, request: ChatRequest, recorder: Recorder) -> list:
        connection = LLMConnectionFactory.create(kind=ProviderKind.OPENAI)
        return list(get_adapter(connection, http_client=_httpx2(recorder)).stream(request))

    @pytest.mark.django_db
    def test_stream(self):
        recorder = Recorder(body=self.BODY, headers={"x-request-id": "req_1"})
        events = self._stream(replace(REQUEST, reasoning=True), recorder)

        assert events == [
            TextDelta("東京は"),
            ToolCallRequested(ToolCall("call_1", "get_weather", {"city": "東京"})),
            # 次の要求で戻せるよう、暗号化されたreasoningをそのまま返す（statusは除く）
            ProviderState({"items": [self.REASONING]}),
            Usage(input_tokens=50, output_tokens=7, cached_input_tokens=20, cache_write_tokens=4, reasoning_tokens=3),
            Completed(StopReason.TOOL_USE, "req_1"),
        ]
        request = recorder.requests[-1]
        assert str(request.url) == "https://api.openai.com/v1/responses"
        assert request.headers["authorization"] == "Bearer sk-test-0123456789abcd"
        sent = recorder.sent
        assert sent["store"] is False
        assert sent["include"] == ["reasoning.encrypted_content"]
        assert sent["instructions"] == "簡潔に答える"
        assert sent["max_output_tokens"] == 100
        assert sent["input"] == [
            {"role": "user", "content": [{"type": "input_text", "text": "東京の天気は？"}]},
            {"type": "function_call", "call_id": "call_0", "name": "get_weather", "arguments": '{"city": "大阪"}'},
            {"type": "function_call_output", "call_id": "call_0", "output": "晴れ"},
        ]
        assert sent["tools"] == [
            {"type": "function", "name": "get_weather", "description": "天気を返す",
             "parameters": WEATHER.parameters, "strict": False}
        ]  # fmt: skip
        assert (sent["tool_choice"], sent["parallel_tool_calls"]) == ("auto", True)
        assert "reasoning" not in sent

    @pytest.mark.django_db
    def test_parts_and_reasoning_replay(self):
        recorder = Recorder(body=self.BODY)
        self._stream(_media_request({"items": [self.REASONING]}), recorder)

        items = recorder.sent["input"]
        assert items[0]["content"] == [
            {"type": "input_text", "text": "この請求書を読んで"},
            {"type": "input_image", "image_url": f"data:image/png;base64,{_b64(PNG)}"},
            {"type": "input_file", "filename": "請求書.pdf", "file_data": f"data:application/pdf;base64,{_b64(PDF)}"},
        ]
        # reasoningは、その応答の発言とTool呼出しの前に戻す
        assert [i.get("type") or i["role"] for i in items[1:]] == [
            "reasoning", "assistant", "function_call", "function_call", "function_call_output", "function_call_output",
        ]  # fmt: skip
        assert items[1] == self.REASONING
        assert items[2] == {"role": "assistant", "content": "表を見ます"}
        # Toolの結果の画像は function_call_output の部品にする
        # 画像に付く文（囲みの印）も、順を保って並べる
        assert items[5]["output"] == [
            {"type": "input_text", "text": "1ページ目"},
            {"type": "input_text", "text": "<open>"},
            {"type": "input_image", "image_url": f"data:image/png;base64,{_b64(PNG)}"},
            {"type": "input_text", "text": "</close>"},
        ]
        assert items[6]["output"] == "見つかりません"

    @pytest.mark.django_db
    def test_text_document_is_sent_as_text(self):
        recorder = Recorder(body=self.BODY)
        self._stream(ChatRequest("test-model", [Message("user", (TEXT_DOCUMENT,))]), recorder)
        assert recorder.sent["input"][0]["content"] == [{"type": "input_text", "text": "メモの本文"}]

    @pytest.mark.django_db
    def test_effort_and_tool_choice(self):
        recorder = Recorder(body=self.BODY)
        self._stream(replace(REQUEST, effort="high", forced_tool="get_weather", parallel_tool_calls=False), recorder)
        sent = recorder.sent
        assert sent["reasoning"] == {"effort": "high"}
        assert sent["tool_choice"] == {"type": "function", "name": "get_weather"}
        assert sent["parallel_tool_calls"] is False

        self._stream(replace(REQUEST, tool_choice=ToolChoice.NONE), recorder)
        assert recorder.sent["tool_choice"] == "none"
        self._stream(replace(REQUEST, tool_choice=ToolChoice.REQUIRED), recorder)
        assert recorder.sent["tool_choice"] == "required"

    @pytest.mark.django_db
    def test_effort_budget_is_unsupported(self):
        recorder = Recorder(body=self.BODY)
        with pytest.raises(ProviderError) as info:
            self._stream(replace(REQUEST, effort="2048"), recorder)
        assert not info.value.retryable
        assert recorder.requests == []

    @pytest.mark.django_db
    def test_incomplete_by_max_output_tokens(self):
        body = _sse(
            [
                ("response.output_text.delta", {"type": "response.output_text.delta", "sequence_number": 1,
                                                "item_id": "msg_1", "output_index": 0, "content_index": 0,
                                                "delta": "途中まで", "logprobs": []}),
                ("response.incomplete", {"type": "response.incomplete", "sequence_number": 2, "response": {
                    **self.RESPONSE, "status": "incomplete", "usage": self.USAGE,
                    "incomplete_details": {"reason": "max_output_tokens"}}}),
            ]
        )  # fmt: skip
        events = self._stream(REQUEST, Recorder(body=body))
        assert events[0] == TextDelta("途中まで")
        # 要求IDのヘッダーがなければ、応答のIDを使う
        assert events[-1] == Completed(StopReason.MAX_TOKENS, "resp_1")

    @pytest.mark.django_db
    def test_end_without_tool_calls(self):
        body = self.BODY.decode().replace('"type": "function_call"', '"type": "message_x"').encode()
        events = self._stream(REQUEST, Recorder(body=body))
        assert events[-1].stop_reason == StopReason.END

    @pytest.mark.django_db
    def test_incomplete_stream_is_an_error(self):
        truncated = self.BODY.decode().split("event: response.completed")[0].encode()
        with pytest.raises(ProviderError) as info:
            self._stream(REQUEST, Recorder(body=truncated))
        assert info.value.retryable

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        ("event", "retryable", "overflow"),
        [
            ({"type": "error", "sequence_number": 1, "code": "context_length_exceeded", "message": "too long"},
             False, True),
            ({"type": "response.failed", "sequence_number": 1, "response": {
                **RESPONSE, "status": "failed", "error": {"code": "server_error", "message": "oops"}}}, True, False),
            ({"type": "response.failed", "sequence_number": 1, "response": {
                **RESPONSE, "status": "failed", "error": {"code": "invalid_image", "message": "bad"}}}, False, False),
        ],
    )  # fmt: skip
    def test_stream_errors(self, event, retryable, overflow):
        with pytest.raises(ProviderError) as info:
            self._stream(REQUEST, Recorder(body=_sse([(event["type"], event)])))
        assert (info.value.retryable, info.value.context_overflow) == (retryable, overflow)
        assert info.value.details["provider_message"]

    @pytest.mark.django_db
    def test_context_overflow(self):
        body = {"error": {"message": "Your input exceeds the context window of this model.",
                          "type": "invalid_request_error", "code": "context_length_exceeded"}}  # fmt: skip
        with pytest.raises(ProviderError) as info:
            self._stream(REQUEST, Recorder(status=400, body=body))
        assert info.value.context_overflow
        assert (info.value.status_code, info.value.retryable) == (400, False)

    @pytest.mark.django_db
    @pytest.mark.parametrize(("status", "retryable"), [(401, False), (429, True), (503, True), (400, False)])
    def test_errors(self, status, retryable):
        recorder = Recorder(
            status=status, body={"error": {"message": "provider says no"}}, headers={"x-request-id": "r"}
        )
        with pytest.raises(ProviderError) as info:
            self._stream(REQUEST, recorder)
        assert (info.value.status_code, info.value.retryable) == (status, retryable)
        assert not info.value.context_overflow
        assert info.value.request_id == "r"
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


class TestChatCompletions:
    """ローカルLLM（OpenAI互換のChat Completions）"""

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
                              "prompt_tokens_details": {"cached_tokens": 20},
                              "completion_tokens_details": {"reasoning_tokens": 2}}}),
            (None, "[DONE]"),
        ]
    )  # fmt: skip

    def _stream(self, request: ChatRequest, recorder: Recorder) -> list:
        connection = LLMConnectionFactory.create(kind=ProviderKind.LOCAL, base_url="http://llm.internal/v1", api_key="")
        return list(get_adapter(connection, http_client=_httpx2(recorder)).stream(request))

    @pytest.mark.django_db
    def test_stream(self):
        recorder = Recorder(body=self.BODY, headers={"x-request-id": "req_1"})
        events = self._stream(REQUEST, recorder)

        assert events == [
            TextDelta("東京は"),
            ToolCallRequested(ToolCall("call_1", "get_weather", {"city": "東京"})),
            Usage(input_tokens=50, output_tokens=7, cached_input_tokens=20, reasoning_tokens=2),
            Completed(StopReason.TOOL_USE, "req_1"),
        ]
        request = recorder.requests[-1]
        assert str(request.url) == "http://llm.internal/v1/chat/completions"
        sent = recorder.sent
        # ローカルLLMのサーバーは max_tokens だけを知るものが多い
        assert sent["max_tokens"] == 100
        assert sent["messages"][0] == {"role": "system", "content": "簡潔に答える"}
        assert sent["messages"][1] == {"role": "user", "content": "東京の天気は？"}
        assert sent["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"city": "大阪"}'
        assert sent["messages"][3] == {"role": "tool", "tool_call_id": "call_0", "content": "晴れ"}
        assert sent["tools"][0]["function"]["name"] == "get_weather"
        # 既定のTool呼出しの指定は送らない
        assert "tool_choice" not in sent
        assert "parallel_tool_calls" not in sent

    @pytest.mark.django_db
    def test_tool_result_images_follow_as_a_user_message(self):
        recorder = Recorder(body=self.BODY)
        request = _media_request({"items": ["ignored"]})
        request = replace(request, messages=[m for m in request.messages if PDF_DOCUMENT not in m.parts])
        self._stream(request, recorder)

        messages = recorder.sent["messages"]
        assert [m["role"] for m in messages] == ["system", "assistant", "tool", "tool", "user"]
        # 推論の途中経過は戻さない
        assert set(messages[1]) == {"role", "content", "tool_calls"}
        assert messages[2] == {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "1ページ目\n[The images of this result follow in the next message.]",
        }
        assert messages[3] == {"role": "tool", "tool_call_id": "call_2", "content": "見つかりません"}
        # toolのメッセージはテキストだけなので、画像は続くtoolのメッセージの後に別の発言として添える。
        # 利用者の発言ではなくToolが返したデータだと示し、画像に付く文（囲みの印）を順を保って一緒に移す
        assert messages[4]["content"][0]["text"].startswith(
            "[Not a message from the user. These are the images returned by tool call call_1."
        )
        assert messages[4]["content"][1:] == [
            {"type": "text", "text": "<open>"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_b64(PNG)}"}},
            {"type": "text", "text": "</close>"},
        ]

    @pytest.mark.django_db
    def test_user_image(self):
        recorder = Recorder(body=self.BODY)
        self._stream(ChatRequest("test-model", [Message("user", (TextPart("これは？"), IMAGE))]), recorder)
        assert recorder.sent["messages"][0]["content"][1] == {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{_b64(PNG)}"},
        }

    @pytest.mark.django_db
    @pytest.mark.parametrize("part", [PDF_DOCUMENT, VideoPart("video/mp4", b"mp4")])
    def test_unsupported_parts(self, part):
        recorder = Recorder(body=self.BODY)
        with pytest.raises(ProviderError) as info:
            self._stream(ChatRequest("test-model", [Message("user", (part,))]), recorder)
        assert not info.value.retryable
        assert part.media_type in str(info.value)
        assert recorder.requests == []

    @pytest.mark.django_db
    def test_effort_and_tool_choice(self):
        recorder = Recorder(body=self.BODY)
        self._stream(
            replace(REQUEST, effort="low", tool_choice=ToolChoice.REQUIRED, parallel_tool_calls=False), recorder
        )
        sent = recorder.sent
        assert sent["reasoning_effort"] == "low"
        assert sent["tool_choice"] == "required"
        assert sent["parallel_tool_calls"] is False

        self._stream(replace(REQUEST, forced_tool="get_weather"), recorder)
        assert recorder.sent["tool_choice"] == {"type": "function", "function": {"name": "get_weather"}}

        # Toolがなければ、Tool呼出しの指定は送らない
        self._stream(replace(REQUEST, tools=(), parallel_tool_calls=False, tool_choice=ToolChoice.NONE), recorder)
        assert "parallel_tool_calls" not in recorder.sent
        assert "tool_choice" not in recorder.sent

    @pytest.mark.django_db
    def test_incomplete_stream_is_an_error(self):
        # 停止の理由が届く前に切れた。途中までの文を完了として返さない
        chunks = [json.loads(line[6:]) for line in self.BODY.decode().split("\n") if line.startswith("data: {")]
        recorder = Recorder(body=_sse([(None, chunks[0])]))
        connection = LLMConnectionFactory.create(kind=ProviderKind.LOCAL, base_url="http://llm.internal/v1")
        events = []
        with pytest.raises(ProviderError) as info:
            for event in get_adapter(connection, http_client=_httpx2(recorder)).stream(REQUEST):
                events.append(event)
        assert info.value.retryable
        assert events == [TextDelta("東京は")]

    @pytest.mark.django_db
    def test_may_omit_usage(self):
        # ローカルLLMのサーバーには利用量を返さないものがある。停止の理由があれば完了にする
        chunks = [json.loads(line[6:]) for line in self.BODY.decode().split("\n") if line.startswith("data: {")]
        body = _sse([(None, c) for c in chunks if "usage" not in c] + [(None, "[DONE]")])
        events = self._stream(REQUEST, Recorder(body=body))
        assert events[-2:] == [Usage(0, 0), Completed(StopReason.TOOL_USE, "")]

    @pytest.mark.django_db
    def test_uses_base_url_without_key(self):
        recorder = Recorder(
            body={"object": "list", "data": [{"id": "llama", "object": "model", "created": 1, "owned_by": "me"}]}
        )
        connection = LLMConnectionFactory.create(
            kind=ProviderKind.LOCAL, base_url="http://llm.internal:8000/v1", api_key=""
        )
        assert get_adapter(connection, http_client=_httpx2(recorder)).list_models() == ["llama"]
        assert str(recorder.requests[-1].url) == "http://llm.internal:8000/v1/models"

    @pytest.mark.django_db
    def test_context_overflow(self):
        body = {"object": "error", "message": "This model's maximum context length is 8192 tokens. However, ...",
                "type": "BadRequestError", "code": 400}  # fmt: skip
        with pytest.raises(ProviderError) as info:
            self._stream(REQUEST, Recorder(status=400, body=body))
        assert info.value.context_overflow
        assert not info.value.retryable


class TestAnthropic:
    BODY = _sse(
        [
            ("message_start", {"type": "message_start", "message": {
                "id": "msg_1", "type": "message", "role": "assistant", "model": "m", "content": [],
                "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 1, "cache_read_input_tokens": 30,
                          "cache_creation_input_tokens": 5}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "thinking", "thinking": "", "signature": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "thinking_delta", "thinking": "天気を"}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "thinking_delta", "thinking": "調べる"}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "signature_delta", "signature": "sig-1"}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("content_block_start", {"type": "content_block_start", "index": 1,
                                     "content_block": {"type": "redacted_thinking", "data": "opaque"}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 1}),
            ("content_block_start", {"type": "content_block_start", "index": 2,
                                     "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 2,
                                     "delta": {"type": "text_delta", "text": "調べます"}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 2}),
            ("content_block_start", {"type": "content_block_start", "index": 3, "content_block": {
                "type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {}}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 3,
                                     "delta": {"type": "input_json_delta", "partial_json": "{\"city\":"}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 3,
                                     "delta": {"type": "input_json_delta", "partial_json": " \"東京\"}"}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 3}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                               "usage": {"output_tokens": 12}}),
            ("message_stop", {"type": "message_stop"}),
        ]
    )  # fmt: skip
    THINKING = [
        {"type": "thinking", "thinking": "天気を調べる", "signature": "sig-1"},
        {"type": "redacted_thinking", "data": "opaque"},
    ]

    def _stream(self, request: ChatRequest, recorder: Recorder) -> list:
        connection = LLMConnectionFactory.create(kind=ProviderKind.ANTHROPIC)
        return list(get_adapter(connection, http_client=_httpx2(recorder)).stream(request))

    @pytest.mark.django_db
    def test_stream(self):
        recorder = Recorder(body=self.BODY, headers={"request-id": "req_a"})
        events = self._stream(REQUEST, recorder)

        assert events == [
            TextDelta("調べます"),
            ToolCallRequested(ToolCall("toolu_1", "get_weather", {"city": "東京"})),
            # thinkingブロックは署名ごと返し、次の要求で戻す
            ProviderState({"blocks": self.THINKING}),
            Usage(input_tokens=45, output_tokens=12, cached_input_tokens=30, cache_write_tokens=5),
            Completed(StopReason.TOOL_USE, "req_a"),
        ]
        request = recorder.requests[-1]
        assert request.headers["x-api-key"] == "sk-test-0123456789abcd"
        sent = recorder.sent
        assert sent["system"] == [{"type": "text", "text": "簡潔に答える"}]
        assert sent["max_tokens"] == 100
        # assistantのTool呼出しと、userの発言にしたToolの結果が交互に並ぶ
        assert [m["role"] for m in sent["messages"]] == ["user", "assistant", "user"]
        assert sent["messages"][1]["content"] == [
            {"type": "tool_use", "id": "call_0", "name": "get_weather", "input": {"city": "大阪"}}
        ]
        assert sent["messages"][2]["content"] == [
            {"type": "tool_result", "tool_use_id": "call_0", "content": [{"type": "text", "text": "晴れ"}]}
        ]
        assert sent["tools"][0]["input_schema"] == WEATHER.parameters
        assert not {"tool_choice", "thinking", "output_config"} & set(sent)
        # 既定ではcacheの区切りを付けない
        assert "cache_control" not in json.dumps(sent)

    @pytest.mark.django_db
    def test_parts_and_thinking_replay(self):
        recorder = Recorder(body=self.BODY)
        request = _media_request({"blocks": self.THINKING})
        request = replace(request, messages=[*request.messages, Message("user", (TEXT_DOCUMENT,))])
        self._stream(request, recorder)

        messages = recorder.sent["messages"]
        assert [m["role"] for m in messages] == ["user", "assistant", "user"]
        assert messages[0]["content"] == [
            {"type": "text", "text": "この請求書を読んで"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _b64(PNG)}},
            {"type": "document", "title": "請求書.pdf",
             "source": {"type": "base64", "media_type": "application/pdf", "data": _b64(PDF)}},
        ]  # fmt: skip
        # thinkingブロックは、その発言の先頭にそのまま戻す
        assert messages[1]["content"][:3] == [*self.THINKING, {"type": "text", "text": "表を見ます"}]
        assert [b["type"] for b in messages[1]["content"][3:]] == ["tool_use", "tool_use"]
        # 並列のTool呼出しの結果は1つのuserの発言にまとめる。結果の画像とエラーをそのまま渡す
        assert messages[2]["content"] == [
            {"type": "tool_result", "tool_use_id": "call_1", "content": [
                {"type": "text", "text": "1ページ目"},
                {"type": "text", "text": "<open>"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _b64(PNG)}},
                {"type": "text", "text": "</close>"},
            ]},
            {"type": "tool_result", "tool_use_id": "call_2", "is_error": True,
             "content": [{"type": "text", "text": "見つかりません"}]},
            {"type": "document", "title": "memo.txt",
             "source": {"type": "text", "media_type": "text/plain", "data": "メモの本文"}},
        ]  # fmt: skip

    @pytest.mark.django_db
    def test_cache_breakpoints(self):
        recorder = Recorder(body=self.BODY)
        self._stream(replace(_media_request(), use_cache=True), recorder)

        sent = recorder.sent
        ephemeral = {"type": "ephemeral"}
        assert sent["system"][-1]["cache_control"] == ephemeral
        assert sent["tools"][-1]["cache_control"] == ephemeral
        assert sent["messages"][-1]["content"][-1]["cache_control"] == ephemeral
        assert sent["messages"][-2]["content"][-1]["cache_control"] == ephemeral
        # 区切りは上限の4つまで
        assert json.dumps(sent).count('"cache_control"') == 4

    @pytest.mark.django_db
    def test_effort(self):
        recorder = Recorder(body=self.BODY)
        # 数字だけのeffortは、thinkingのtoken予算。最大出力tokenは予算より大きくする
        self._stream(replace(REQUEST, effort="2048"), recorder)
        sent = recorder.sent
        assert sent["thinking"] == {"type": "enabled", "budget_tokens": 2048}
        assert sent["max_tokens"] > 2048
        assert "output_config" not in sent

        self._stream(replace(REQUEST, effort="high"), recorder)
        sent = recorder.sent
        assert sent["thinking"] == {"type": "adaptive"}
        assert sent["output_config"] == {"effort": "high"}
        assert sent["max_tokens"] == 100

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        ("changes", "expected"),
        [
            ({"tool_choice": ToolChoice.REQUIRED}, {"type": "any"}),
            ({"tool_choice": ToolChoice.NONE, "parallel_tool_calls": False}, {"type": "none"}),
            ({"forced_tool": "get_weather"}, {"type": "tool", "name": "get_weather"}),
            ({"parallel_tool_calls": False}, {"type": "auto", "disable_parallel_tool_use": True}),
            ({"tool_choice": ToolChoice.AUTO}, None),
        ],
    )
    def test_tool_choice(self, changes, expected):
        recorder = Recorder(body=self.BODY)
        self._stream(replace(REQUEST, **changes), recorder)
        assert recorder.sent.get("tool_choice") == expected

    @pytest.mark.django_db
    def test_unsupported_part(self):
        recorder = Recorder(body=self.BODY)
        with pytest.raises(ProviderError):
            self._stream(ChatRequest("test-model", [Message("user", (VideoPart("video/mp4", b"mp4"),))]), recorder)
        assert recorder.requests == []

    @pytest.mark.django_db
    def test_incomplete_stream_is_an_error(self):
        # message_delta と message_stop の前に切れた
        truncated = self.BODY.decode().split("event: message_delta")[0].encode()
        with pytest.raises(ProviderError) as info:
            self._stream(REQUEST, Recorder(body=truncated))
        assert info.value.retryable

    @pytest.mark.django_db
    def test_overloaded_is_retryable(self):
        recorder = Recorder(status=529, body={"type": "error", "error": {"type": "overloaded_error", "message": "x"}})
        with pytest.raises(ProviderError) as info:
            self._stream(REQUEST, recorder)
        assert info.value.retryable
        assert info.value.status_code == 529

    @pytest.mark.django_db
    def test_context_overflow(self):
        message = "prompt is too long: 210000 tokens > 200000 maximum"
        body = {"type": "error", "error": {"type": "invalid_request_error", "message": message}}
        with pytest.raises(ProviderError) as info:
            self._stream(REQUEST, Recorder(status=400, body=body))
        assert info.value.context_overflow
        assert not info.value.retryable


class TestGemini:
    SIGNATURE = _b64(b"call-signature")
    TEXT_SIGNATURE = _b64(b"text-signature")
    BODY = _sse(
        [
            (None, {"candidates": [{"content": {"role": "model", "parts": [
                {"text": "考え中", "thought": True}, {"text": "東京は"}]}}], "responseId": "resp_g"}),
            (None, {"candidates": [{"content": {"role": "model", "parts": [
                {"functionCall": {"name": "get_weather", "args": {"city": "東京"}}, "thoughtSignature": SIGNATURE},
                {"text": "", "thoughtSignature": TEXT_SIGNATURE}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 40, "candidatesTokenCount": 6, "thoughtsTokenCount": 4,
                                  "cachedContentTokenCount": 8},
                "responseId": "resp_g"}),
        ]
    )  # fmt: skip

    def _stream(self, request: ChatRequest, recorder: Recorder) -> list:
        connection = LLMConnectionFactory.create(kind=ProviderKind.GEMINI)
        return list(get_adapter(connection, http_client=_httpx(recorder)).stream(request))

    @pytest.mark.django_db
    def test_stream(self):
        recorder = Recorder(body=self.BODY)
        events = self._stream(REQUEST, recorder)

        assert events[0] == TextDelta("東京は")
        assert isinstance(events[1], ToolCallRequested)
        call = events[1].call
        assert (call.name, call.arguments) == ("get_weather", {"city": "東京"})
        assert call.id  # IDがなければ付ける
        assert events[2:] == [
            # thought signatureは、Tool呼出しのIDと文ごとに返す
            ProviderState({"function_calls": {call.id: self.SIGNATURE}, "text": self.TEXT_SIGNATURE}),
            Usage(input_tokens=40, output_tokens=10, cached_input_tokens=8, reasoning_tokens=4),
            Completed(StopReason.TOOL_USE, "resp_g"),
        ]
        request = recorder.requests[-1]
        assert "test-model:streamGenerateContent" in str(request.url)
        assert request.headers["x-goog-api-key"] == "sk-test-0123456789abcd"
        sent = recorder.sent
        assert sent["systemInstruction"]["parts"][0]["text"] == "簡潔に答える"
        assert [c["role"] for c in sent["contents"]] == ["user", "model", "user"]
        assert sent["contents"][2]["parts"][0]["functionResponse"]["name"] == "get_weather"
        assert sent["contents"][2]["parts"][0]["functionResponse"]["response"] == {"result": "晴れ"}
        assert sent["generationConfig"]["maxOutputTokens"] == 100
        assert "thinkingConfig" not in sent["generationConfig"]
        assert "toolConfig" not in sent

    @pytest.mark.django_db
    def test_parts_and_signature_replay(self):
        recorder = Recorder(body=self.BODY)
        state = {"function_calls": {"call_1": self.SIGNATURE}, "text": self.TEXT_SIGNATURE}
        request = _media_request(state)
        request = replace(request, messages=[*request.messages, Message("user", (TEXT_DOCUMENT,))])
        self._stream(request, recorder)

        # SDKは入れ子の項目名をsnake_caseのまま送る（APIはどちらも受け付ける）
        contents = recorder.sent["contents"]
        assert [c["role"] for c in contents] == ["user", "model", "user"]
        assert contents[0]["parts"] == [
            {"text": "この請求書を読んで"},
            {"inlineData": {"mime_type": "image/png", "data": _b64(PNG)}},
            {"inlineData": {"mime_type": "application/pdf", "data": _b64(PDF)}},
        ]
        # thought signatureを、元の部品に付け直して戻す
        model = contents[1]["parts"]
        assert model[0] == {"text": "表を見ます", "thoughtSignature": self.TEXT_SIGNATURE}
        assert model[1]["thoughtSignature"] == self.SIGNATURE
        assert model[1]["functionCall"]["id"] == "call_1"
        assert "thoughtSignature" not in model[2]
        # 並列のTool呼出しの結果は1つの発言にまとめる。画像は FunctionResponse の部品にする
        results = contents[2]["parts"]
        # 文と画像の順を混ぜられないので、画像に付く文の代わりに、画像がデータであることを文で示す
        assert results[0]["functionResponse"]["response"] == {"result": "1ページ目\n\n" + MEDIA_NOTE}
        assert results[0]["functionResponse"]["parts"] == [
            {"inline_data": {"mime_type": "image/png", "data": _b64(PNG)}}
        ]
        assert results[1]["functionResponse"]["response"] == {"error": "見つかりません"}
        assert "parts" not in results[1]["functionResponse"]
        assert results[2] == {"text": "メモの本文"}

    @pytest.mark.django_db
    def test_effort(self):
        recorder = Recorder(body=self.BODY)
        self._stream(replace(REQUEST, effort="1024"), recorder)
        assert recorder.sent["generationConfig"]["thinkingConfig"] == {"thinking_budget": 1024}
        self._stream(replace(REQUEST, effort="high"), recorder)
        assert recorder.sent["generationConfig"]["thinkingConfig"] == {"thinking_level": "HIGH"}

        with pytest.raises(ProviderError) as info:
            self._stream(replace(REQUEST, effort="extreme"), recorder)
        assert not info.value.retryable

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        ("changes", "expected"),
        [
            ({"tool_choice": ToolChoice.REQUIRED}, {"mode": "ANY"}),
            ({"tool_choice": ToolChoice.NONE}, {"mode": "NONE"}),
            ({"forced_tool": "get_weather"}, {"mode": "ANY", "allowedFunctionNames": ["get_weather"]}),
        ],
    )
    def test_tool_choice(self, changes, expected):
        recorder = Recorder(body=self.BODY)
        self._stream(replace(REQUEST, **changes), recorder)
        assert recorder.sent["toolConfig"] == {"functionCallingConfig": expected}

    def test_schema(self):
        schema = {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "options": {"type": "object", "properties": {"unit": {"type": "string"}}, "required": ["unit", "x"]},
                "tags": {"type": "array", "items": {"type": "object", "required": ["name"]}},
            },
            "required": ["city", "missing"],
            "additionalProperties": False,
        }
        assert gemini_schema(schema) == {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "options": {"type": "object", "properties": {"unit": {"type": "string"}}, "required": ["unit"]},
                "tags": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["city"],
            "additionalProperties": False,
        }

    @pytest.mark.django_db
    def test_schema_is_sanitized_in_requests(self):
        recorder = Recorder(body=self.BODY)
        tool = ToolDefinition("list_files", "一覧", {"required": ["path"]})
        self._stream(replace(REQUEST, tools=[tool]), recorder)
        declaration = recorder.sent["tools"][0]["functionDeclarations"][0]
        assert declaration["parameters_json_schema"] == {"type": "object", "properties": {}}

    @pytest.mark.django_db
    def test_incomplete_stream_is_an_error(self):
        # finishReason と usageMetadata を持つ最後の塊が届く前に切れた
        truncated = self.BODY.decode().split("\n\n")[0].encode() + b"\n\n"
        with pytest.raises(ProviderError) as info:
            self._stream(REQUEST, Recorder(body=truncated))
        assert info.value.retryable

    @pytest.mark.django_db
    def test_error(self):
        recorder = Recorder(
            status=403, body={"error": {"code": 403, "message": "denied", "status": "PERMISSION_DENIED"}}
        )
        connection = LLMConnectionFactory.create(kind=ProviderKind.GEMINI)
        with pytest.raises(ProviderError) as info:
            get_adapter(connection, http_client=_httpx(recorder)).list_models()
        assert (info.value.status_code, info.value.retryable) == (403, False)

    @pytest.mark.django_db
    def test_context_overflow(self):
        message = "The input token count (1200000) exceeds the maximum number of tokens allowed (1048576)."
        body = {"error": {"code": 400, "message": message, "status": "INVALID_ARGUMENT"}}
        with pytest.raises(ProviderError) as info:
            self._stream(REQUEST, Recorder(status=400, body=body))
        assert info.value.context_overflow
        assert info.value.status_code == 400


class TestOpenAIResponsesWithoutReasoning:
    @pytest.mark.django_db
    def test_encrypted_reasoning_is_not_requested_for_models_without_reasoning(self):
        recorder = Recorder(body=TestOpenAIResponses.BODY)
        connection = LLMConnectionFactory.create(kind=ProviderKind.OPENAI)
        list(get_adapter(connection, http_client=_httpx2(recorder)).stream(REQUEST))
        assert "include" not in recorder.sent
