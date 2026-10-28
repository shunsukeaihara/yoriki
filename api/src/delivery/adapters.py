"""配信Adapter（design.md §4.4）。共通の送る内容（`payload.build_payload()`）を、出口の種類ごとの形で送る。

Adapterは送る内容の形と送り先の確かめ方だけを持ち、再試行の上限と記録は共通の処理（`delivery/tasks.py`）が持つ。
Zapierは汎用のWebhookと同じ送り方で、送り先をZapierのCatch HookのURLに限る。Plugin Registryはまだないので、出口の
種類からコードで選ぶ（Channel Pluginとあわせて作る）。
"""

import base64
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, ClassVar
from urllib.parse import SplitResult

import httpx
from django.utils import timezone
from django.utils.translation import gettext as _

from utils.http import PinnedTransport

from .models import SIGNING_SECRET_PREFIX, AttemptOutcome, DestinationKind
from .network import BlockedAddress, check_url, resolve

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(30.0, connect=10.0)
USER_AGENT = "Yoriki-Delivery/1"
# 送り直してよい応答（一時的な障害）
RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}


@dataclass
class Outcome:
    outcome: AttemptOutcome
    http_status: int | None = None
    error: str = ""
    # 送り先が指定した次に送ってよいまでの秒数（Retry-After）
    retry_after: int | None = None


def sign(secret: str, message_id: str, timestamp: int, body: bytes) -> str:
    """Standard Webhooksの署名（`v1,<base64(HMAC-SHA256)>`）。受け手は同じ鍵で確かめられる"""
    key = base64.b64decode(secret.removeprefix(SIGNING_SECRET_PREFIX))
    digest = hmac.new(key, f"{message_id}.{timestamp}.".encode() + body, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


def _retry_after(response: httpx.Response) -> int | None:
    value = response.headers.get("Retry-After", "").strip()
    if not value:
        return None
    if value.isdigit():
        return int(value)
    try:
        return max(0, int((parsedate_to_datetime(value) - timezone.now()).total_seconds()))
    except (TypeError, ValueError):
        return None


class WebhookAdapter:
    """設定したendpointへ、共通のイベントをJSONでPOSTする"""

    kind: ClassVar[DestinationKind] = DestinationKind.WEBHOOK
    # テストで差し替える
    transport: ClassVar[httpx.BaseTransport | None] = None

    def validate_url(self, url: str) -> SplitResult:
        """登録するときの確認。誤りは BlockedAddress（理由は利用者に見せてよい文）"""
        return check_url(url)

    def prepare(self, payload: dict[str, Any]) -> bytes:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()

    def deliver(self, *, url: str, secret: str, payload: dict[str, Any], attempt: int) -> Outcome:
        try:
            parts = self.validate_url(url)
            address = resolve(parts)
        except BlockedAddress as exc:
            return Outcome(AttemptOutcome.FAILED, error=str(exc))
        body = self.prepare(payload)
        timestamp = int(timezone.now().timestamp())
        message_id = payload["event_id"]
        headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "webhook-id": message_id,
            "webhook-timestamp": str(timestamp),
            "webhook-signature": sign(secret, message_id, timestamp, body),
            "Yoriki-Event-Type": payload["event_type"],
            "Yoriki-Delivery-Attempt": str(attempt),
        }
        # 確かめたアドレスへ接続を固定する（HostとTLSの名前は元のホスト名）。環境変数のproxyは使わない
        transport = PinnedTransport(self.transport or httpx.HTTPTransport(), lambda _request: str(address))
        try:
            with httpx.Client(timeout=TIMEOUT, follow_redirects=False, trust_env=False, transport=transport) as client:
                response = client.post(url, content=body, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            # 送る前に失敗した（届いていない）ので、送り直してよい
            return Outcome(
                AttemptOutcome.RETRYABLE, error=_("送り先につながりません（%(type)s）") % {"type": type(exc).__name__}
            )
        except httpx.HTTPError as exc:
            # 送った後に応答が失われた等。届いたか分からないので、自動では送り直さない（design.md §4.5）
            return Outcome(
                AttemptOutcome.UNKNOWN,
                error=_("送り先の応答を受け取れません（%(type)s）") % {"type": type(exc).__name__},
            )
        status = response.status_code
        if 200 <= status < 300:
            return Outcome(AttemptOutcome.ACCEPTED, http_status=status)
        if status in RETRYABLE_STATUSES:
            return Outcome(
                AttemptOutcome.RETRYABLE,
                http_status=status,
                error=_("送り先が一時的に受け付けません（HTTP %(status)s）") % {"status": status},
                retry_after=_retry_after(response),
            )
        if 300 <= status < 400:
            error = _("送り先がリダイレクトを返しました（HTTP %(status)s）。リダイレクトは辿りません") % {
                "status": status
            }
        else:
            error = _("送り先が受け付けませんでした（HTTP %(status)s）") % {"status": status}
        return Outcome(AttemptOutcome.FAILED, http_status=status, error=error)


ZAPIER_HOST = "hooks.zapier.com"
ZAPIER_PATH_PREFIX = "/hooks/catch/"


class ZapierAdapter(WebhookAdapter):
    """ZapierのCatch Hookへ送る。送り方は汎用のWebhookと同じ（Zapierは入れ子のJSONを項目に展開する）"""

    kind = DestinationKind.ZAPIER

    def validate_url(self, url: str) -> SplitResult:
        parts = super().validate_url(url)
        if parts.scheme != "https" or parts.hostname != ZAPIER_HOST or not parts.path.startswith(ZAPIER_PATH_PREFIX):
            raise BlockedAddress(
                _("ZapierのCatch HookのURL（https://%(host)s%(path)s…）を入力してください")
                % {"host": ZAPIER_HOST, "path": ZAPIER_PATH_PREFIX}
            )
        return parts


ADAPTERS: dict[int, WebhookAdapter] = {a.kind: a for a in (WebhookAdapter(), ZapierAdapter())}


def adapter_for(kind: int) -> WebhookAdapter:
    return ADAPTERS[kind]
