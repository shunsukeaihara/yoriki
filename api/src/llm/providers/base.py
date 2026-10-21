"""Provider Adapterの共通の型（design.md §4.8）。

各社のAPIの違い（メッセージの形式、Tool呼出し、利用量、エラー）を、ここで定める共通の形に変換する。
Runnerはこの型だけを使い、プロバイダごとのSDKを直接呼ばない（harness.md §12）。
"""

import base64
import json
import re
from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from django.utils.translation import gettext as _

from ..models import LLMConnection


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema（harness.md §7.3 の範囲）。各社の制約への変換はAdapterが行う


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


# --- メッセージの部品（harness.md §12）。bytesはbase64にしてその場で送る（各社のFiles APIは使わない） ---


@dataclass(frozen=True)
class TextPart:
    text: str


@dataclass(frozen=True)
class ImagePart:
    media_type: str  # image/png、image/jpeg、image/webp、image/gif
    data: bytes


@dataclass(frozen=True)
class DocumentPart:
    media_type: str  # application/pdf、text/plain
    data: bytes
    name: str = ""


@dataclass(frozen=True)
class AudioPart:
    media_type: str
    data: bytes


@dataclass(frozen=True)
class VideoPart:
    media_type: str
    data: bytes


Part = TextPart | ImagePart | DocumentPart | AudioPart | VideoPart


class InputKind(StrEnum):
    """テキスト以外の入力の種類。ModelProfileの能力（supports_images 等）と対応する"""

    IMAGE = "image"
    DOCUMENT = "document"
    AUDIO = "audio"
    VIDEO = "video"


PART_KINDS: dict[type, InputKind] = {
    ImagePart: InputKind.IMAGE,
    DocumentPart: InputKind.DOCUMENT,
    AudioPart: InputKind.AUDIO,
    VideoPart: InputKind.VIDEO,
}


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    # 文字列を渡すと TextPart 1つにする
    content: str | tuple[Part, ...] = ()
    # assistant: モデルが要求したTool呼出し
    tool_calls: tuple[ToolCall, ...] = ()
    # tool: どの呼出しへの結果か（Geminiは呼出しのIDではなくToolの名前で対応づける）
    tool_call_id: str = ""
    tool_name: str = ""
    is_error: bool = False
    # assistant: 推論の途中経過など、次の要求へそのまま戻すプロバイダ固有の部分（ProviderState.data）。
    # 同じ接続・同じモデルの応答のものだけをRunnerが渡す（harness.md §5.5）
    provider_state: dict[str, Any] | None = None

    def __post_init__(self):
        if isinstance(self.content, str):
            object.__setattr__(self, "content", (TextPart(self.content),) if self.content else ())

    @property
    def parts(self) -> tuple[Part, ...]:
        return self.content  # type: ignore[return-value]  # __post_init__ でtupleにしている

    @property
    def text(self) -> str:
        return "".join(p.text for p in self.parts if isinstance(p, TextPart))

    def input_kinds(self) -> set[InputKind]:
        return {PART_KINDS[type(p)] for p in self.parts if type(p) in PART_KINDS}


class ToolChoice(StrEnum):
    AUTO = "auto"
    NONE = "none"  # Toolの定義は送ったまま、使わせない（prompt cacheを保つ。harness.md §4.4）
    REQUIRED = "required"


@dataclass(frozen=True)
class ChatRequest:
    model: str
    messages: Sequence[Message]
    tools: Sequence[ToolDefinition] = ()
    max_output_tokens: int | None = None
    temperature: float | None = None
    # モデル設定の受け付ける値そのまま（各社の指定。harness.md §12）。数字だけの値は、thinkingのtoken予算として扱う
    effort: str | None = None
    tool_choice: ToolChoice = ToolChoice.AUTO
    # 指定すると、そのToolを必ず呼ばせる（tool_choiceより優先）
    forced_tool: str = ""
    parallel_tool_calls: bool = True
    # prompt cachingに対応するモデルで、cacheの区切りを付ける
    use_cache: bool = False
    # 推論の制御に対応するモデル。推論の途中経過を受け取って次の要求へ戻す（OpenAIの暗号化されたreasoning等）
    reasoning: bool = False


class StopReason(StrEnum):
    END = "end"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    OTHER = "other"


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCallRequested:
    call: ToolCall


@dataclass(frozen=True)
class ProviderState:
    """次の要求で assistant のメッセージに付けて戻す部分。

    thinkingブロック、暗号化されたreasoning、thought signature。中身はAdapterだけが解釈する
    """

    data: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    input_tokens: int  # cacheから読んだ分・書いた分を含む入力の全体
    output_tokens: int  # 推論の分を含む出力の全体
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass(frozen=True)
class Completed:
    stop_reason: StopReason
    # プロバイダの要求ID。障害の問い合わせと、利用量の照合に使う
    request_id: str = ""


ChatEvent = TextDelta | ToolCallRequested | ProviderState | Usage | Completed


@dataclass
class ProviderError(Exception):
    """プロバイダの呼出しの失敗。messageは画面やRunEventに出してよい文にする（API keyを含めない）"""

    message: str
    # 時間をおけば成功する見込みがある（rate limit、一時的な障害、タイムアウト）。代替モデルへの切替えの判断にも使う
    retryable: bool = False
    # 入力が文脈の上限を超えた。Runnerは圧縮してからやり直す（harness.md §9.2）
    context_overflow: bool = False
    status_code: int | None = None
    request_id: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


class ProviderAdapter(ABC):
    """1つのLLMConnectionでLLMを呼ぶ。`http_client` はテストで通信を差し替えるときだけ渡す"""

    def __init__(self, connection: LLMConnection, *, http_client: Any = None):
        self.connection = connection
        self.http_client = http_client

    @abstractmethod
    def stream(self, request: ChatRequest) -> Iterator[ChatEvent]:
        """応答を逐次返す。最後に（あれば ProviderState と）Usage と Completed を1つずつ返す。

        失敗したら ProviderError
        """

    @abstractmethod
    def list_models(self) -> list[str]:
        """接続先で使えるモデル名。接続の確認に使う。失敗したら ProviderError"""


def status_error(status_code: int, *, request_id: str = "", provider_message: str = "") -> ProviderError:
    """HTTPのステータスから、画面に出せる共通のエラーにする。プロバイダの文は details にだけ残す"""
    details = {"provider_message": provider_message} if provider_message else {}
    if status_code in (401, 403):
        message, retryable = _("API keyが正しくないか、このモデルを使う権限がありません。"), False
    elif status_code == 404:
        message, retryable = _("モデルまたは接続先が見つかりません。モデル名と接続先を確認してください。"), False
    elif status_code == 429:
        message, retryable = _("プロバイダの要求の上限に達しました。時間をおいて再度お試しください。"), True
    elif status_code >= 500:
        message, retryable = _("プロバイダで一時的な障害が起きています。時間をおいて再度お試しください。"), True
    else:
        message = _("プロバイダが要求を受け付けませんでした（HTTP %(status)s）。") % {"status": status_code}
        retryable = False
    return ProviderError(message, retryable, status_code=status_code, request_id=request_id, details=details)


# 各社が文脈の上限の超過を知らせる文（HTTP 400）。専用のエラーコードがない社もあるので、文で見分ける
CONTEXT_OVERFLOW_PATTERN = re.compile(
    r"context[ _-]?(length|window)|prompt is too long|input is too long|maximum number of tokens|too many tokens",
    re.IGNORECASE,
)


def is_context_overflow(status_code: int, provider_message: str, code: str = "") -> bool:
    if code == "context_length_exceeded":
        return True
    return status_code == 400 and bool(CONTEXT_OVERFLOW_PATTERN.search(provider_message))


def context_overflow_error(
    status_code: int | None, *, request_id: str = "", provider_message: str = ""
) -> ProviderError:
    """入力が文脈の上限を超えた。再試行しても通らないので、Runnerが圧縮してからやり直す（harness.md §9.2）"""
    return ProviderError(
        _("入力がモデルの文脈の上限を超えました。"),
        context_overflow=True,
        status_code=status_code,
        request_id=request_id,
        details={"provider_message": provider_message} if provider_message else {},
    )


def unsupported_part_error(part: Part, provider: str) -> ProviderError:
    """このAPIで送れない種類の入力。Runnerは送る前に能力を確かめる（harness.md §12）ので、ここは安全網"""
    labels = {
        InputKind.IMAGE: _("画像"),
        InputKind.DOCUMENT: _("文書"),
        InputKind.AUDIO: _("音声"),
        InputKind.VIDEO: _("動画"),
    }
    label = labels[PART_KINDS[type(part)]]
    return ProviderError(
        _("%(provider)sには%(kind)s（%(media_type)s）を送れません。")
        % {"provider": provider, "kind": label, "media_type": getattr(part, "media_type", "")}
    )


def unsupported_budget_error(provider: str) -> ProviderError:
    """effortに数字（thinkingのtoken予算）を指定したが、そのAPIは水準の名前だけを受け付ける"""
    return ProviderError(
        _("%(provider)sのeffortには、token数ではなく水準の名前を指定してください。") % {"provider": provider}
    )


def timeout_error() -> ProviderError:
    return ProviderError(_("プロバイダの応答がタイムアウトしました。"), retryable=True)


def incomplete_stream_error() -> ProviderError:
    """応答が終わりの合図（停止の理由と利用量）なしに途切れた。途中までの文を完了として扱わない"""
    return ProviderError(_("プロバイダの応答が途中で切れました。"), retryable=True)


def connection_error() -> ProviderError:
    return ProviderError(
        _("プロバイダに接続できませんでした。接続先とネットワークを確認してください。"), retryable=True
    )


def thinking_budget(effort: str | None) -> int | None:
    """effortが数字だけなら、thinkingのtoken予算として返す"""
    return int(effort) if effort and effort.isdigit() else None


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def data_url(media_type: str, data: bytes) -> str:
    return f"data:{media_type};base64,{b64(data)}"


def document_text(part: DocumentPart) -> str:
    """テキストの文書（text/plain）の中身"""
    return part.data.decode("utf-8", errors="replace")


def object_schema(parameters: dict[str, Any]) -> dict[str, Any]:
    """Toolの引数のSchemaのルートを、各社が求める形（`type: object` と `properties`）にそろえる"""
    schema = dict(parameters)
    schema["type"] = "object"
    if not isinstance(schema.get("properties"), dict):
        schema["properties"] = {}
    return schema


def parse_arguments(raw: str) -> dict[str, Any]:
    """Tool呼出しの引数（JSONの文字列）を辞書にする"""
    try:
        value = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        # モデルが壊れたJSONを返した。Runnerがモデルにエラーとして返せるよう、元の文字列を残す
        return {"_raw": raw}
    return value if isinstance(value, dict) else {"_raw": raw}
