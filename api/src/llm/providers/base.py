"""Provider Adapterの共通の型（design.md §4.8）。

各社のAPIの違い（メッセージの形式、Tool呼出し、利用量、エラー）を、ここで定める共通の形に変換する。
Runnerはこの型だけを使い、プロバイダごとのSDKを直接呼ばない。画像入力などの能力は、使う段階で追加する。
"""

import json
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
    parameters: dict[str, Any]  # JSON Schema


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    # assistant: モデルが要求したTool呼出し
    tool_calls: tuple[ToolCall, ...] = ()
    # tool: どの呼出しへの結果か（Geminiは呼出しのIDではなくToolの名前で対応づける）
    tool_call_id: str = ""
    tool_name: str = ""


@dataclass(frozen=True)
class ChatRequest:
    model: str
    messages: Sequence[Message]
    tools: Sequence[ToolDefinition] = ()
    max_output_tokens: int | None = None
    temperature: float | None = None


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
class Usage:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0


@dataclass(frozen=True)
class Completed:
    stop_reason: StopReason
    # プロバイダの要求ID。障害の問い合わせと、利用量の照合に使う
    request_id: str = ""


ChatEvent = TextDelta | ToolCallRequested | Usage | Completed


@dataclass
class ProviderError(Exception):
    """プロバイダの呼出しの失敗。messageは画面やRunEventに出してよい文にする（API keyを含めない）"""

    message: str
    # 時間をおけば成功する見込みがある（rate limit、一時的な障害、タイムアウト）。代替モデルへの切替えの判断にも使う
    retryable: bool = False
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
        """応答を逐次返す。最後に Usage と Completed を1つずつ返す。失敗したら ProviderError"""

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
    return ProviderError(message, retryable, status_code, request_id, details)


def timeout_error() -> ProviderError:
    return ProviderError(_("プロバイダの応答がタイムアウトしました。"), retryable=True)


def incomplete_stream_error() -> ProviderError:
    """応答が終わりの合図（停止の理由と利用量）なしに途切れた。途中までの文を完了として扱わない"""
    return ProviderError(_("プロバイダの応答が途中で切れました。"), retryable=True)


def connection_error() -> ProviderError:
    return ProviderError(
        _("プロバイダに接続できませんでした。接続先とネットワークを確認してください。"), retryable=True
    )


def parse_arguments(raw: str) -> dict[str, Any]:
    """Tool呼出しの引数（JSONの文字列）を辞書にする"""
    try:
        value = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        # モデルが壊れたJSONを返した。Runnerがモデルにエラーとして返せるよう、元の文字列を残す
        return {"_raw": raw}
    return value if isinstance(value, dict) else {"_raw": raw}
