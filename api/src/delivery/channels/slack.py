"""SlackのChannel Plugin（design.md §4.6・§4.7）。

- 送信：Web API（`chat.postMessage`・`chat.update`・`chat.postEphemeral`）。Bot token（`xoxb-`）で呼ぶ。
- 受信：Events API（`message` のイベント）とInteractivity（ボタン）を同じURLで受け、Signing secretで確かめる。
- 必要なscope：`chat:write`、`channels:read`、`channels:history`（公開チャネルのメッセージのイベント）、`users:read`。

Slackの投稿には重複のキーがないので、`chat.postMessage` を送った後に応答が失われたとき（5xxを含む）は「届いたか
分からない」にする（自動では送り直さない。design.md §4.7）。書き換え（`chat.update`）は同じ内容を何度送ってもよいので
送り直す。
"""

import hashlib
import hmac
import json
import time
from typing import Any, ClassVar
from urllib.parse import parse_qs

import httpx
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from plugin.errors import PluginConfigError, PluginPermanentError, PluginTemporaryError

from ..adapters import Outcome, _retry_after
from ..models import AttemptOutcome
from .base import (
    ChannelAdapter,
    ChannelInfo,
    ConnectionInfo,
    InboundEvent,
    InboundKind,
    InboundRequest,
    QuestionView,
    ThreadMessage,
)

API = "https://slack.com/api/"
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
USER_AGENT = "Yoriki-Channel/1"
# 署名の時刻の許容（Slackの推奨は5分）
MAX_CLOCK_SKEW = 300
# sectionの本文の上限（Slackは3000文字）
MAX_SECTION = 2900
# ボタンの文字の上限（Slackは75文字）
MAX_BUTTON = 70
SELECT_PREFIX = "yoriki_select_"
SUBMIT_ACTION = "yoriki_submit"
# 受け付ける返信の種類（ファイルを付けた返信は文だけを使う。添付は後で。design.md §4.6）
REPLY_SUBTYPES = {None, "thread_broadcast", "file_share"}
# Slackが一時的な障害として返すもの
TRANSIENT_ERRORS = {"ratelimited", "internal_error", "fatal_error", "service_unavailable", "request_timeout"}
AUTH_ERRORS = {"invalid_auth", "not_authed", "token_revoked", "token_expired", "account_inactive", "no_permission"}

# Slackのアプリの設定の例（接続の画面に出す）。requestUrlは接続ごとに変わる
SECRETS = ("bot_token", "signing_secret")


def escape(text: str) -> str:
    """mrkdwnの制御文字（&・<・>）をエスケープする。モデル・利用者の文がメンションやリンクにならないように"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": _truncate(text, MAX_SECTION)}}


def _context(text: str) -> dict[str, Any]:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": _truncate(text, MAX_SECTION)}]}


def _link(link: str) -> str:
    return f"<{link}|{_('Yorikiで開く')}>"


class SlackAdapter(ChannelAdapter):
    # テストで差し替える
    transport: ClassVar[httpx.BaseTransport | None] = None
    # 署名の時刻の確かめに使う（テストで差し替える）
    clock: ClassVar = staticmethod(time.time)

    # --- 接続とチャネルの確かめ ---

    def check_connection(self, secrets: dict[str, str]) -> ConnectionInfo:
        if not secrets.get("bot_token", "").startswith("xoxb-"):
            raise PluginConfigError(_("Bot User OAuth Token（xoxb- で始まるもの）を入力してください"))
        if not secrets.get("signing_secret", "").strip():
            raise PluginConfigError(_("Signing Secret を入力してください"))
        data = self._check_call("auth.test", secrets, params={})
        return ConnectionInfo(
            workspace_id=data.get("team_id", ""),
            workspace_name=data.get("team", ""),
            bot_user_id=data.get("user_id", ""),
        )

    def check_channel(self, secrets: dict[str, str], channel_id: str) -> ChannelInfo:
        data = self._check_call("conversations.info", secrets, params={"channel": channel_id})
        channel = data.get("channel") or {}
        if channel.get("is_archived"):
            raise PluginConfigError(_("アーカイブされたチャネルです"))
        if channel.get("is_private") or channel.get("is_ext_shared") or not channel.get("is_channel", True):
            # 確かめたのは、アプリが参加した公開チャネルだけ（非公開・Slack Connect・DMは後で。design.md §4.6）
            raise PluginConfigError(
                _("公開チャネルを指定してください（非公開チャネル・共有チャネルにはまだ対応していません）")
            )
        if not channel.get("is_member"):
            raise PluginConfigError(_("botがチャネルに参加していません。チャネルでアプリを追加（/invite）してください"))
        return ChannelInfo(channel_id=channel.get("id", channel_id), name="#" + channel.get("name", channel_id))

    def _check_call(self, method: str, secrets: dict[str, str], params: dict[str, str]) -> dict[str, Any]:
        outcome, data = self._call(method, secrets, params=params, idempotent=True)
        if outcome.outcome == AttemptOutcome.ACCEPTED:
            return data
        if outcome.outcome == AttemptOutcome.RETRYABLE:
            raise PluginTemporaryError(outcome.error)
        raise PluginConfigError(outcome.error)

    # --- 送信 ---

    def post(
        self, secrets: dict[str, str], channel_id: str, message: ThreadMessage, *, thread_ref: str = ""
    ) -> Outcome:
        body: dict[str, Any] = {
            "channel": channel_id,
            "text": self._fallback(message),
            "blocks": self.blocks(message),
            "unfurl_links": False,
            "unfurl_media": False,
        }
        if thread_ref:
            body["thread_ts"] = thread_ref
        outcome, data = self._call("chat.postMessage", secrets, body=body, idempotent=False)
        if outcome.outcome == AttemptOutcome.ACCEPTED:
            outcome.message_ref = str(data.get("ts", ""))
        return outcome

    def update(self, secrets: dict[str, str], channel_id: str, message_ref: str, message: ThreadMessage) -> Outcome:
        body = {
            "channel": channel_id,
            "ts": message_ref,
            "text": self._fallback(message),
            "blocks": self.blocks(message),
        }
        outcome, _data = self._call("chat.update", secrets, body=body, idempotent=True)
        if outcome.outcome == AttemptOutcome.ACCEPTED:
            outcome.message_ref = message_ref
        return outcome

    def notify_user(
        self, secrets: dict[str, str], channel_id: str, thread_ref: str, user_id: str, text: str
    ) -> Outcome:
        body = {"channel": channel_id, "user": user_id, "text": text, "thread_ts": thread_ref}
        outcome, _data = self._call("chat.postEphemeral", secrets, body=body, idempotent=False)
        return outcome

    def user_name(self, secrets: dict[str, str], user_id: str) -> str:
        outcome, data = self._call("users.info", secrets, params={"user": user_id}, idempotent=True)
        if outcome.outcome != AttemptOutcome.ACCEPTED:
            return ""
        user = data.get("user") or {}
        profile = user.get("profile") or {}
        return profile.get("display_name") or profile.get("real_name") or user.get("name") or ""

    def _call(
        self,
        method: str,
        secrets: dict[str, str],
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        idempotent: bool,
    ) -> tuple[Outcome, dict[str, Any]]:
        """Web APIを呼ぶ。`idempotent` でない呼出し（投稿）は、送った後の失敗を「届いたか分からない」にする"""
        headers = {"Authorization": f"Bearer {secrets.get('bot_token', '')}", "User-Agent": USER_AGENT}
        after_send = AttemptOutcome.RETRYABLE if idempotent else AttemptOutcome.UNKNOWN
        try:
            with httpx.Client(timeout=TIMEOUT, trust_env=False, transport=self.transport) as client:
                if body is not None:
                    response = client.post(API + method, json=body, headers=headers)
                else:
                    response = client.get(API + method, params=params or {}, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            error = _("Slackにつながりません（%(type)s）") % {"type": type(exc).__name__}
            return Outcome(AttemptOutcome.RETRYABLE, error=error, not_sent=True), {}
        except httpx.HTTPError as exc:
            error = _("Slackの応答を受け取れません（%(type)s）") % {"type": type(exc).__name__}
            return Outcome(after_send, error=error), {}
        return _interpret(response, after_send)

    # --- 見せ方 ---

    @staticmethod
    def _fallback(message: ThreadMessage) -> str:
        """通知・読み上げに使う文"""
        text = message.title or message.text
        if message.question is not None:
            text = _("確認をお願いします：%(question)s") % {"question": message.question.questions[0]["question"]}
        return _truncate(text, 3000)

    def blocks(self, message: ThreadMessage) -> list[dict[str, Any]]:
        if message.question is not None:
            return self._question_blocks(message.question, message.link)
        blocks = []
        head = f"*{escape(message.title)}*" if message.title else ""
        body = escape(message.text)
        blocks.append(_section("\n".join(v for v in (head, body) if v) or " "))
        if message.link:
            blocks.append(_context(_link(message.link)))
        return blocks

    def _question_blocks(self, view: QuestionView, link: str) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        open_ = view.answer is None and not view.closed
        for i, question in enumerate(view.questions):
            blocks.append(_question_section(view, i, question))
            if open_:
                chosen = view.selections[i] if i < len(view.selections) else []
                blocks.append({"type": "actions", "elements": _option_buttons(view.clarification, i, question, chosen)})
        if open_:
            if not view.confirms_on_select:
                blocks.append({"type": "actions", "elements": [_submit_button(view.clarification)]})
                note = _("選択肢を選んで「この内容で回答」を押すか、このスレッドに返信して答えてください。")
            else:
                note = _("ボタンを押すと回答が決まります。このスレッドに返信して答えることもできます。")
            blocks.append(_context(note + (" " + _link(link) if link else "")))
        elif view.answer is not None:
            if view.answer.text:
                blocks.append(_section(escape(view.answer.text)))
            via = _("Web") if view.answer.via == "web" else _("Slack")
            blocks.append(
                _context(_("%(name)sさんが%(via)sで回答しました。") % {"name": escape(view.answer.name), "via": via})
            )
        else:
            blocks.append(_context(escape(view.closed)))
        return blocks

    # --- 受信 ---

    def verify(self, secrets: dict[str, str], headers: dict[str, str], body: bytes) -> bool:
        secret = secrets.get("signing_secret", "")
        timestamp = headers.get("x-slack-request-timestamp", "")
        signature = headers.get("x-slack-signature", "")
        if not secret or not timestamp.isdigit() or not signature:
            return False
        if abs(self.clock() - int(timestamp)) > MAX_CLOCK_SKEW:
            return False
        base = b"v0:" + timestamp.encode() + b":" + body
        expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def parse(self, headers: dict[str, str], body: bytes) -> InboundRequest:
        try:
            if headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
                payload = parse_qs(body.decode(), strict_parsing=True).get("payload", [""])[0]
                return self._parse_interaction(json.loads(payload))
            data = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise PluginPermanentError(_("Slackの要求を読めません")) from exc
        if not isinstance(data, dict):
            raise PluginPermanentError(_("Slackの要求を読めません"))
        if data.get("type") == "url_verification":
            return InboundRequest(response={"challenge": str(data.get("challenge", ""))})
        if data.get("type") != "event_callback":
            return InboundRequest()
        event = data.get("event") or {}
        if not isinstance(event, dict) or event.get("type") != "message":
            return InboundRequest()
        thread_ts, ts = event.get("thread_ts"), event.get("ts")
        if not thread_ts or thread_ts == ts or event.get("subtype") not in REPLY_SUBTYPES:
            # スレッドの外の発言・スレッドの親・編集・削除・参加等は取り込まない
            return InboundRequest()
        return InboundRequest(
            events=[
                InboundEvent(
                    kind=InboundKind.REPLY,
                    dedup_key=f"event:{data.get('event_id', '')}",
                    workspace_id=str(data.get("team_id") or event.get("team") or ""),
                    channel_id=str(event.get("channel", "")),
                    thread_ref=str(thread_ts),
                    message_ref=str(ts or ""),
                    user_id=str(event.get("user", "")),
                    is_bot=bool(event.get("bot_id")),
                    text=str(event.get("text", "")),
                )
            ]
        )

    @staticmethod
    def _parse_interaction(data: Any) -> InboundRequest:
        if not isinstance(data, dict) or data.get("type") != "block_actions":
            return InboundRequest()
        actions = data.get("actions") or []
        message = data.get("message") or {}
        container = data.get("container") or {}
        user = data.get("user") or {}
        events = []
        for action in actions:
            action_id = str(action.get("action_id", ""))
            value = str(action.get("value", ""))
            if action_id == SUBMIT_ACTION:
                kind, clarification, question, option = InboundKind.SUBMIT, value, -1, -1
            elif action_id.startswith(SELECT_PREFIX):
                parts = value.split(":")
                if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
                    continue
                kind, clarification, question, option = InboundKind.SELECT, parts[0], int(parts[1]), int(parts[2])
            else:
                continue  # Yorikiのボタンでないもの
            events.append(
                InboundEvent(
                    kind=kind,
                    dedup_key=f"action:{action.get('action_ts', '')}:{user.get('id', '')}:{action_id}",
                    workspace_id=str((data.get("team") or {}).get("id", "")),
                    channel_id=str((data.get("channel") or {}).get("id", container.get("channel_id", ""))),
                    thread_ref=str(message.get("thread_ts", "")),
                    message_ref=str(container.get("message_ts") or message.get("ts", "")),
                    user_id=str(user.get("id", "")),
                    user_name=str(user.get("name") or user.get("username") or ""),
                    clarification=clarification,
                    question=question,
                    option=option,
                )
            )
        return InboundRequest(events=events)


def _interpret(response: httpx.Response, after_send: AttemptOutcome) -> tuple[Outcome, dict[str, Any]]:
    """Web APIの応答を、受付確認・一時的な失敗・失敗・届いたか分からない、に分ける"""
    status = response.status_code
    if status == 429:
        error = _("Slackの呼出しの頻度の上限に達しました")
        return Outcome(
            AttemptOutcome.RETRYABLE, http_status=status, error=error, retry_after=_retry_after(response)
        ), {}
    if status != 200:
        if status >= 500:
            error, outcome = _("Slackが一時的に受け付けません（HTTP %(status)s）") % {"status": status}, after_send
        else:
            error, outcome = (
                _("Slackが受け付けませんでした（HTTP %(status)s）") % {"status": status},
                AttemptOutcome.FAILED,
            )
        return Outcome(outcome, http_status=status, error=error), {}
    try:
        data = response.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return Outcome(after_send, http_status=status, error=_("Slackの応答を読めません")), {}
    if data.get("ok"):
        return Outcome(AttemptOutcome.ACCEPTED, http_status=status), data
    error = str(data.get("error", "unknown"))
    if error in TRANSIENT_ERRORS:
        outcome = AttemptOutcome.RETRYABLE if error == "ratelimited" else after_send
        text = _("Slackの一時的な障害です（%(error)s）") % {"error": error}
        return Outcome(outcome, http_status=status, error=text), {}
    return Outcome(AttemptOutcome.FAILED, http_status=status, error=error_text(error, data)), data


ERROR_TEXTS = {
    "channel_not_found": gettext_lazy("チャネルが見つかりません。channel IDと、botが参加しているかを確かめてください"),
    "not_in_channel": gettext_lazy("botがチャネルに参加していません"),
    "is_archived": gettext_lazy("アーカイブされたチャネルです"),
    "message_not_found": gettext_lazy("書き換えるメッセージが見つかりません（削除された等）"),
}


def error_text(error: str, data: dict[str, Any]) -> str:
    """Slackのエラーを、利用者に見せる文にする"""
    if error in AUTH_ERRORS:
        return _("Slackの資格情報が正しくありません（%(error)s）") % {"error": error}
    if error == "missing_scope":
        return _("Slackのアプリに必要な権限（scope）がありません（%(needed)s）") % {"needed": data.get("needed", "")}
    if error in ERROR_TEXTS:
        return str(ERROR_TEXTS[error])
    return _("Slackが受け付けませんでした（%(error)s）") % {"error": error}


def _question_section(view: QuestionView, index: int, question: dict[str, Any]) -> dict[str, Any]:
    lines = [f"*{escape(question.get('header', ''))}*", escape(question["question"])]
    lines.extend(
        f"• {escape(o['label'])}：{escape(o['description'])}" for o in question["options"] if o.get("description")
    )
    if view.answer is not None:
        selected = view.answer.selected[index] if index < len(view.answer.selected) else []
        if selected:
            lines.append("→ " + escape("、".join(selected)))
    return _section("\n".join(lines))


def _option_buttons(clarification: str, index: int, question: dict[str, Any], chosen: list[str]) -> list[dict]:
    buttons = []
    for j, option in enumerate(question["options"]):
        selected = option["label"] in chosen
        button: dict[str, Any] = {
            "type": "button",
            "text": {"type": "plain_text", "text": _truncate(("✓ " if selected else "") + option["label"], MAX_BUTTON)},
            "action_id": f"{SELECT_PREFIX}{index}_{j}",
            "value": f"{clarification}:{index}:{j}",
        }
        if selected:
            button["style"] = "primary"
        buttons.append(button)
    return buttons


def _submit_button(clarification: str) -> dict[str, Any]:
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": _("この内容で回答")},
        "style": "primary",
        "action_id": SUBMIT_ACTION,
        "value": clarification,
    }
