"""入口のAdapter（拡張種別 trigger。design.md §3.11・§4.2。書き方は plugin/CONTRACT.md）。

Adapterは、認証済みの要求の本文を共通の形（Payload）にし、重複のキーとタスクの入力の値を取り出す。認証（入口の鍵）、
停止・版の確かめ、重複の判定、Runの作成は共通の処理（task/dispatch.py の `receive_webhook()`）が行う。要求を断るときは
PayloadError（送り元に返す決まったコードと、見せてよい文）を投げる。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from django.utils.translation import gettext as _

from plugin import registry
from plugin.errors import PluginUnavailable

from .webhook import Payload, PayloadError, dedup_key, input_values, parse_payload


@dataclass
class TriggerRequest:
    """認証済みの要求。項目は既定値つきで足し、今のAdapterを壊さない（design.md §3.11.2）"""

    body: bytes
    content_type: str = ""
    # 送り元が付けた Idempotency-Key ヘッダー
    idempotency_key: str | None = None


class TriggerAdapter(ABC):
    @abstractmethod
    def parse(self, request: TriggerRequest) -> Payload:
        """本文を読む。受け付けない形は PayloadError"""

    @abstractmethod
    def dedup_key(self, payload: Payload, event_id_path: str, idempotency_key: str | None) -> str | None:
        """重複の判定のキー（入口の版のイベントIDの場所、Idempotency-Key、推奨の形式の event_id）。なければNone"""

    @abstractmethod
    def input_values(
        self, payload: Payload, mappings: list[dict[str, str]], definitions: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """入口の版の取り出し（`mappings`）とタスクの版の入力の項目（`definitions`）で、タスクの入力の値を作る"""


class WebhookTrigger(TriggerAdapter):
    """Webhook（JSON・フォーム・multipart・テキスト。task/webhook.py）"""

    def parse(self, request: TriggerRequest) -> Payload:
        return parse_payload(request.body, request.content_type)

    def dedup_key(self, payload: Payload, event_id_path: str, idempotency_key: str | None) -> str | None:
        return dedup_key(payload, event_id_path, idempotency_key)

    def input_values(
        self, payload: Payload, mappings: list[dict[str, str]], definitions: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return input_values(payload, mappings, definitions)


def trigger_adapter(extension_id: str) -> TriggerAdapter:
    """入口のAdapter。停止中・配備されていないPluginの入口は、入口を停止したのと同じに断る"""
    try:
        extension, _spec = registry.get(extension_id)
    except PluginUnavailable as exc:
        raise PayloadError("trigger_suspended", _("この入口は停止中です"), status=403) from exc
    return extension.adapter()
