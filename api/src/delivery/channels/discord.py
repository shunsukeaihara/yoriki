"""DiscordのChannel Plugin（design.md §4.6・§4.7）。

- 接続：Bot（アプリ）1つとサーバー（guild）1つ。資格情報は `bot_token`・`public_key`（ボタンの署名を確かめる鍵）・
  `guild_id`。
- 送信：REST（v10）。親メッセージをチャネルへ投稿し、そのメッセージから公開スレッドを作る（スレッドのIDは親メッセージの
  IDと同じ）。スレッドの中のメッセージの参照は `<スレッドのID>/<メッセージのID>` にする（書き換えにスレッドが
  要るため）。
- ボタン：Interactions Endpoint URL（今の受け口）で受け、Ed25519で確かめて、メッセージを変えない遅延の応答
  （DEFERRED_UPDATE_MESSAGE）を返す。
- 返信：Gateway（intent GUILDS・GUILD_MESSAGES・MESSAGE_CONTENT）で受ける（capability listen。channel-listenerが呼ぶ）。
- 必要な権限：View Channels・Send Messages・Create Public Threads・Send Messages in Threads・Read Message History。

投稿（POST）は、送った後に応答が失われたら「届いたか分からない」にする（Slackと同じ。design.md §4.7）。
"""

import asyncio
import json
import logging
import random
import re
import time
from typing import Any, ClassVar

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
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
    ListenSink,
    QuestionView,
    ThreadMessage,
)

logger = logging.getLogger(__name__)

API = "https://discord.com/api/v10/"
GATEWAY = "wss://gateway.discord.gg"
GATEWAY_QUERY = "/?v=10&encoding=json"
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
USER_AGENT = "DiscordBot (https://github.com/shunsukeaihara/yoriki, 1)"
MAX_CLOCK_SKEW = 300
# メッセージの本文の上限（Discordは2000文字）
MAX_CONTENT = 2000
# ボタンの文字の上限（Discordは80文字）
MAX_BUTTON = 80
# スレッドの名前の上限（Discordは100文字）
MAX_THREAD_NAME = 100
# スレッドが自動でアーカイブされるまで（分。最長の1週間）。アーカイブされても投稿すれば戻る
AUTO_ARCHIVE = 10080
SELECT_PREFIX = "yoriki:s:"
SUBMIT_PREFIX = "yoriki:submit:"

# intent：GUILDS・GUILD_MESSAGES・MESSAGE_CONTENT（特権。Developer Portalで有効にする）
INTENTS = (1 << 0) | (1 << 9) | (1 << 15)
# アプリの設定（/applications/@me の flags）でMESSAGE_CONTENTが有効か
APP_MESSAGE_CONTENT = (1 << 18) | (1 << 19)

# 権限（permissionsのビット）
ADMINISTRATOR = 1 << 3
VIEW_CHANNEL = 1 << 10
SEND_MESSAGES = 1 << 11
READ_MESSAGE_HISTORY = 1 << 16
CREATE_PUBLIC_THREADS = 1 << 35
SEND_MESSAGES_IN_THREADS = 1 << 38
REQUIRED_PERMISSIONS = {
    VIEW_CHANNEL: gettext_lazy("チャネルを見る"),
    SEND_MESSAGES: gettext_lazy("メッセージを送信"),
    READ_MESSAGE_HISTORY: gettext_lazy("メッセージ履歴を読む"),
    CREATE_PUBLIC_THREADS: gettext_lazy("公開スレッドを作成"),
    SEND_MESSAGES_IN_THREADS: gettext_lazy("スレッドでメッセージを送信"),
}

# チャネルの種類
GUILD_TEXT = 0
THREAD_TYPES = {10, 11, 12}
# 取り込むメッセージの種類（普通の発言と、返信の形の発言）
REPLY_MESSAGE_TYPES = {0, 19}

# interactionの種類と応答
PING = 1
MESSAGE_COMPONENT = 3
PONG = 1
CHANNEL_MESSAGE = 4
DEFERRED_UPDATE_MESSAGE = 6
EPHEMERAL = 1 << 6

# Discordのエラーコード
UNKNOWN_CHANNEL = 10003
UNKNOWN_MESSAGE = 10008
THREAD_ALREADY_CREATED = 160004

# Gatewayのclose codeのうち、つなぎ直しても直らないもの（資格情報・設定の誤り）
FATAL_CLOSE = {
    4004: gettext_lazy("Bot tokenが正しくありません"),
    4010: gettext_lazy("shardの指定が正しくありません"),
    4011: gettext_lazy("shardが必要です（Botの参加するサーバーが多すぎます）"),
    4012: gettext_lazy("Gatewayの版が正しくありません"),
    4013: gettext_lazy("intentの指定が正しくありません"),
    4014: gettext_lazy("Message Content Intent が有効になっていません。Developer Portalの「Bot」で有効にしてください"),
}
# 新しいセッションで始め直すもの
RESET_CLOSE = {4007, 4009}

SECRETS = ("bot_token", "public_key", "guild_id")
_SNOWFLAKE = re.compile(r"^\d{1,20}$")
_MARKDOWN = re.compile(r"([\\*_~`|>\[\]()#<@:-])")


def escape(text: str) -> str:
    """Markdownとメンションの記号をエスケープする（モデル・利用者の文がメンション・リンク・書式にならないように）"""
    return _MARKDOWN.sub(r"\\\1", text)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _split_ref(message_ref: str) -> tuple[str, str]:
    """メッセージの参照（`<スレッド>/<メッセージ>` か `<メッセージ>`）を、(スレッド, メッセージ) にする"""
    channel, _sep, message = message_ref.rpartition("/")
    return channel, message


class DiscordAdapter(ChannelAdapter):
    # テストで差し替える
    transport: ClassVar[httpx.BaseTransport | None] = None
    clock: ClassVar = staticmethod(time.time)
    # Gatewayの接続（テストで差し替える。websockets.asyncio.client.connect と同じ形）
    connect: ClassVar = None

    # --- 接続とチャネルの確かめ ---

    def check_connection(self, secrets: dict[str, str]) -> ConnectionInfo:
        if not secrets.get("bot_token", "").strip():
            raise PluginConfigError(_("Bot tokenを入力してください"))
        public_key = secrets.get("public_key", "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", public_key):
            raise PluginConfigError(_("Public Key（64文字の16進数）を入力してください"))
        guild_id = secrets.get("guild_id", "").strip()
        if not _SNOWFLAKE.match(guild_id):
            raise PluginConfigError(_("サーバーのID（数字）を入力してください"))
        me = self._check_call("GET", "users/@me", secrets)
        app = self._check_call("GET", "applications/@me", secrets)
        if str(app.get("verify_key", "")).lower() != public_key:
            raise PluginConfigError(_("Public Keyが、このBotのアプリのものと違います"))
        if not int(app.get("flags") or 0) & APP_MESSAGE_CONTENT:
            raise PluginConfigError(
                _("Message Content Intent が有効になっていません。Developer Portalの「Bot」で有効にしてください")
            )
        guild = self._check_call("GET", f"guilds/{guild_id}", secrets, missing=_("Botがこのサーバーに参加していません"))
        return ConnectionInfo(
            workspace_id=str(guild.get("id", guild_id)),
            workspace_name=str(guild.get("name", "")),
            bot_user_id=str(me.get("id", "")),
        )

    def check_channel(self, secrets: dict[str, str], channel_id: str) -> ChannelInfo:
        if not _SNOWFLAKE.match(channel_id):
            raise PluginConfigError(_("チャネルのID（数字）を入力してください"))
        guild_id = secrets.get("guild_id", "")
        channel = self._check_call(
            "GET", f"channels/{channel_id}", secrets, missing=_("チャネルが見つかりません（Botから見えません）")
        )
        if str(channel.get("guild_id", "")) != guild_id:
            raise PluginConfigError(_("接続のサーバーのチャネルではありません"))
        if channel.get("type") != GUILD_TEXT:
            raise PluginConfigError(
                _("テキストチャネルを指定してください（フォーラム・お知らせ・スレッド等にはまだ対応していません）")
            )
        me = self._check_call("GET", "users/@me", secrets)
        guild = self._check_call("GET", f"guilds/{guild_id}", secrets)
        member = self._check_call("GET", f"guilds/{guild_id}/members/{me.get('id', '')}", secrets)
        granted = channel_permissions(guild, member, channel, str(me.get("id", "")))
        missing = [str(name) for bit, name in REQUIRED_PERMISSIONS.items() if not granted & bit]
        if missing:
            raise PluginConfigError(
                _("Botにこのチャネルの権限がありません：%(missing)s") % {"missing": "、".join(missing)}
            )
        return ChannelInfo(channel_id=str(channel.get("id", channel_id)), name="#" + str(channel.get("name", "")))

    def _check_call(self, method: str, path: str, secrets: dict[str, str], *, missing: str = "") -> dict[str, Any]:
        outcome, data = self._call(method, path, secrets, idempotent=True)
        if outcome.outcome == AttemptOutcome.ACCEPTED:
            return data
        if outcome.outcome == AttemptOutcome.RETRYABLE:
            raise PluginTemporaryError(outcome.error)
        if missing and outcome.http_status in (403, 404):
            raise PluginConfigError(missing)
        raise PluginConfigError(outcome.error)

    # --- 送信 ---

    def post(
        self, secrets: dict[str, str], channel_id: str, message: ThreadMessage, *, thread_ref: str = ""
    ) -> Outcome:
        body = self.message_body(message)
        if not thread_ref:
            outcome, data = self._call("POST", f"channels/{channel_id}/messages", secrets, body=body)
            if outcome.outcome != AttemptOutcome.ACCEPTED:
                return outcome
            outcome.message_ref = str(data.get("id", ""))
            # スレッドを作れなくても、親メッセージは送れている。スレッドは次の投稿のときに作り直す
            self._create_thread(secrets, channel_id, outcome.message_ref, message.title or message.text)
            return outcome
        outcome, data = self._call("POST", f"channels/{thread_ref}/messages", secrets, body=body)
        if outcome.outcome == AttemptOutcome.FAILED and _code(data) == UNKNOWN_CHANNEL:
            # 親メッセージだけがあって、スレッドがまだない
            created = self._create_thread(secrets, channel_id, thread_ref, "Yoriki")
            if created.outcome != AttemptOutcome.ACCEPTED:
                return created
            outcome, data = self._call("POST", f"channels/{thread_ref}/messages", secrets, body=body)
        if outcome.outcome == AttemptOutcome.ACCEPTED:
            outcome.message_ref = f"{thread_ref}/{data.get('id', '')}"
        return outcome

    def _create_thread(self, secrets: dict[str, str], channel_id: str, message_id: str, name: str) -> Outcome:
        body = {"name": _truncate(name.strip() or "Yoriki", MAX_THREAD_NAME), "auto_archive_duration": AUTO_ARCHIVE}
        # 同じメッセージからスレッドは1つしか作れないので、送り直してもよい
        outcome, data = self._call(
            "POST", f"channels/{channel_id}/messages/{message_id}/threads", secrets, body=body, idempotent=True
        )
        if outcome.outcome == AttemptOutcome.FAILED and _code(data) == THREAD_ALREADY_CREATED:
            return Outcome(AttemptOutcome.ACCEPTED, http_status=outcome.http_status)
        if outcome.outcome != AttemptOutcome.ACCEPTED:
            logger.warning("cannot create the thread from %s: %s", message_id, outcome.error)
            outcome.error = _("スレッドを作れません：%(error)s") % {"error": outcome.error}
        return outcome

    def update(self, secrets: dict[str, str], channel_id: str, message_ref: str, message: ThreadMessage) -> Outcome:
        thread, message_id = _split_ref(message_ref)
        outcome, _data = self._call(
            "PATCH",
            f"channels/{thread or channel_id}/messages/{message_id}",
            secrets,
            body=self.message_body(message),
            idempotent=True,
        )
        if outcome.outcome == AttemptOutcome.ACCEPTED:
            outcome.message_ref = message_ref
        return outcome

    def notify_user(
        self,
        secrets: dict[str, str],
        channel_id: str,
        thread_ref: str,
        user_id: str,
        text: str,
        *,
        reply_token: str = "",
    ) -> Outcome:
        application, _sep, token = reply_token.partition("/")
        if application and token:
            # ボタンを押した本人だけに見える応答（interactionのtokenは15分有効）
            outcome, _data = self._call(
                "POST",
                f"webhooks/{application}/{token}",
                secrets,
                body={"content": _truncate(text, MAX_CONTENT), "flags": EPHEMERAL, "allowed_mentions": {"parse": []}},
                authorized=False,
            )
            if outcome.outcome == AttemptOutcome.ACCEPTED:
                return outcome
        body = {
            "content": _truncate(f"<@{user_id}> {escape(text)}", MAX_CONTENT),
            "allowed_mentions": {"parse": [], "users": [user_id]},
        }
        outcome, _data = self._call("POST", f"channels/{thread_ref}/messages", secrets, body=body)
        return outcome

    def _call(
        self,
        method: str,
        path: str,
        secrets: dict[str, str],
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        idempotent: bool = False,
        authorized: bool = True,
    ) -> tuple[Outcome, Any]:
        """RESTを呼ぶ。`idempotent` でない呼出し（投稿）は、送った後の失敗を「届いたか分からない」にする"""
        headers = {"User-Agent": USER_AGENT}
        if authorized:
            headers["Authorization"] = f"Bot {secrets.get('bot_token', '')}"
        after_send = AttemptOutcome.RETRYABLE if idempotent or method == "GET" else AttemptOutcome.UNKNOWN
        try:
            with httpx.Client(timeout=TIMEOUT, trust_env=False, transport=self.transport) as client:
                response = client.request(method, API + path, json=body, params=params, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            error = _("Discordにつながりません（%(type)s）") % {"type": type(exc).__name__}
            return Outcome(AttemptOutcome.RETRYABLE, error=error, not_sent=True), {}
        except httpx.HTTPError as exc:
            error = _("Discordの応答を受け取れません（%(type)s）") % {"type": type(exc).__name__}
            return Outcome(after_send, error=error), {}
        return _interpret(response, after_send)

    # --- 見せ方 ---

    def message_body(self, message: ThreadMessage) -> dict[str, Any]:
        """投稿・書き換えの本文。メンションは誰にも届かないようにする"""
        if message.question is not None:
            content, components = self._question(message.question, message.link)
        else:
            head = f"**{escape(message.title)}**" if message.title else ""
            content = "\n".join(v for v in (head, escape(message.text)) if v) or "\u200b"
            components = [_row([_link_button(message.link)])] if message.link else []
        return {
            "content": _truncate(content, MAX_CONTENT),
            "components": components,
            "allowed_mentions": {"parse": []},
        }

    def _question(self, view: QuestionView, link: str) -> tuple[str, list[dict[str, Any]]]:
        lines: list[str] = []
        rows: list[dict[str, Any]] = []
        open_ = view.answer is None and not view.closed
        for i, question in enumerate(view.questions):
            if lines:
                lines.append("")
            lines.append(f"**{escape(question.get('header', ''))}**")
            lines.append(escape(question["question"]))
            lines.extend(
                f"• {escape(o['label'])}：{escape(o['description'])}"
                for o in question["options"]
                if o.get("description")
            )
            if view.answer is not None:
                selected = view.answer.selected[i] if i < len(view.answer.selected) else []
                if selected:
                    lines.append("→ " + escape("、".join(selected)))
            if open_:
                chosen = view.selections[i] if i < len(view.selections) else []
                rows.append(_row(_option_buttons(view.clarification, i, question, chosen)))
        lines.append("")
        footer, last = _question_footer(view, link)
        lines.extend(footer)
        if last:
            rows.append(_row(last))
        return "\n".join(lines), rows

    # --- 受信（ボタン。Interactions Endpoint URL） ---

    def verify(self, secrets: dict[str, str], headers: dict[str, str], body: bytes) -> bool:
        signature = headers.get("x-signature-ed25519", "")
        timestamp = headers.get("x-signature-timestamp", "")
        try:
            key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(secrets.get("public_key", "")))
            if not timestamp.isdigit() or abs(self.clock() - int(timestamp)) > MAX_CLOCK_SKEW:
                return False
            key.verify(bytes.fromhex(signature), timestamp.encode() + body)
        except (ValueError, InvalidSignature):
            return False
        return True

    def parse(self, headers: dict[str, str], body: bytes) -> InboundRequest:
        try:
            data = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise PluginPermanentError(_("Discordの要求を読めません")) from exc
        if not isinstance(data, dict):
            raise PluginPermanentError(_("Discordの要求を読めません"))
        if data.get("type") == PING:
            return InboundRequest(response={"type": PONG})
        if data.get("type") != MESSAGE_COMPONENT:
            return InboundRequest(
                response={
                    "type": CHANNEL_MESSAGE,
                    "data": {"content": _("この操作には対応していません。"), "flags": EPHEMERAL},
                }
            )
        component = data.get("data") or {}
        custom_id = str(component.get("custom_id", ""))
        acknowledged = InboundRequest(response={"type": DEFERRED_UPDATE_MESSAGE})
        if custom_id.startswith(SUBMIT_PREFIX):
            kind, clarification, question, option = InboundKind.SUBMIT, custom_id[len(SUBMIT_PREFIX) :], -1, -1
        elif custom_id.startswith(SELECT_PREFIX):
            parts = custom_id[len(SELECT_PREFIX) :].split(":")
            if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
                return acknowledged
            kind, clarification, question, option = InboundKind.SELECT, parts[0], int(parts[1]), int(parts[2])
        else:
            return acknowledged  # Yorikiのボタンでないもの
        channel = data.get("channel") or {}
        thread = str(data.get("channel_id") or channel.get("id", ""))
        member = data.get("member") or {}
        user = member.get("user") or data.get("user") or {}
        message = data.get("message") or {}
        acknowledged.events.append(
            InboundEvent(
                kind=kind,
                dedup_key=f"interaction:{data.get('id', '')}",
                workspace_id=str(data.get("guild_id", "")),
                channel_id=str(channel.get("parent_id") or ""),
                thread_ref=thread,
                message_ref=f"{thread}/{message.get('id', '')}",
                user_id=str(user.get("id", "")),
                user_name=_member_name(member, user),
                is_bot=bool(user.get("bot")),
                clarification=clarification,
                question=question,
                option=option,
                reply_token=f"{data.get('application_id', '')}/{data.get('token', '')}",
            )
        )
        return acknowledged

    # --- 受信（返信。Gateway） ---

    def fetch_replies(
        self, secrets: dict[str, str], channel_id: str, thread_ref: str, after_ref: str
    ) -> list[InboundEvent]:
        after = _split_ref(after_ref)[1] or thread_ref
        guild_id = secrets.get("guild_id", "")
        found: list[dict[str, Any]] = []
        while True:  # 続きのページがなくなるまで読む（取りこぼさない）
            outcome, data = self._call(
                "GET", f"channels/{thread_ref}/messages", secrets, params={"after": after, "limit": "100"}
            )
            if outcome.outcome != AttemptOutcome.ACCEPTED:
                if outcome.outcome == AttemptOutcome.RETRYABLE:
                    raise PluginTemporaryError(outcome.error)
                raise PluginPermanentError(outcome.error)
            if not isinstance(data, list) or not data:
                break
            found.extend(data)
            after = max((str(m.get("id", "0")) for m in data), key=int)
            if len(data) < 100:
                break
        found.sort(key=lambda m: int(m.get("id", 0)))
        events = []
        for message in found:
            message.setdefault("guild_id", guild_id)
            event = reply_event(message, channel_id)
            if event is not None:
                events.append(event)
        return events

    async def listen(self, secrets: dict[str, str], sink: ListenSink) -> None:
        await _Gateway(self, secrets, sink).run()

    def channel_parent(self, secrets: dict[str, str], channel_id: str) -> str | None:
        """スレッドなら親のチャネルのID、スレッドでなければ空。分からなければ None"""
        outcome, data = self._call("GET", f"channels/{channel_id}", secrets)
        if outcome.outcome != AttemptOutcome.ACCEPTED or not isinstance(data, dict):
            return None
        return str(data.get("parent_id") or "") if data.get("type") in THREAD_TYPES else ""


def reply_event(message: dict[str, Any], parent_id: str) -> InboundEvent | None:
    """スレッドのメッセージ（GatewayのMESSAGE_CREATE、RESTのメッセージ）を返信の出来事にする。取り込まないものは None"""
    if message.get("type") not in REPLY_MESSAGE_TYPES:
        return None
    author = message.get("author") or {}
    thread = str(message.get("channel_id", ""))
    return InboundEvent(
        kind=InboundKind.REPLY,
        dedup_key=f"message:{message.get('id', '')}",
        workspace_id=str(message.get("guild_id", "")),
        channel_id=parent_id,
        thread_ref=thread,
        message_ref=f"{thread}/{message.get('id', '')}",
        user_id=str(author.get("id", "")),
        user_name=_member_name(message.get("member") or {}, author),
        is_bot=bool(author.get("bot") or message.get("webhook_id")),
        text=str(message.get("content", "")),
    )


class _Gateway:
    """Gatewayの1つの接続（https://docs.discord.com/developers/events/gateway）。つなぎ直し・RESUMEはここで行い、
    資格情報・設定の誤り（つなぎ直しても直らないclose code）だけを PluginConfigError で返す"""

    def __init__(self, adapter: DiscordAdapter, secrets: dict[str, str], sink: ListenSink):
        self.adapter, self.secrets, self.sink = adapter, secrets, sink
        self.guild_id = secrets.get("guild_id", "")
        self.session_id = ""
        self.resume_url = ""
        self.sequence: int | None = None
        # チャネルのID → スレッドなら親のチャネルのID、スレッドでなければ空
        self.parents: dict[str, str] = {}
        self.failures = 0
        # 最後の心拍に応答があったか
        self.acked = True

    async def run(self) -> None:
        while True:
            try:
                await self._connect_once()
            except PluginConfigError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.info("discord gateway %s: reconnecting after %s", self.guild_id, exc)
            self.failures += 1
            await asyncio.sleep(min(60.0, 2.0 ** min(self.failures, 6)) * random.uniform(0.5, 1.0))

    async def _connect_once(self) -> None:
        connect = type(self.adapter).connect or _default_connect()
        resuming = bool(self.session_id and self.sequence is not None)
        url = (self.resume_url if resuming and self.resume_url else GATEWAY) + GATEWAY_QUERY
        async with connect(url, max_size=2**23, proxy=None, open_timeout=20) as ws:
            try:
                await self._session(ws)
            except _connection_closed():
                pass
            self._closed(ws)

    async def _session(self, ws: Any) -> None:
        """HELLOを受けてIDENTIFY（新しいセッション）かRESUMEを送り、閉じるまで受ける"""
        hello = json.loads(await ws.recv())
        interval = float((hello.get("d") or {}).get("heartbeat_interval", 41250)) / 1000
        if self.session_id and self.sequence is not None:
            token = self.secrets.get("bot_token", "")
            resume = {"token": token, "session_id": self.session_id, "seq": self.sequence}
            await ws.send(json.dumps({"op": 6, "d": resume}))
        else:
            await ws.send(json.dumps({"op": 2, "d": self._identify()}))
        heartbeat = asyncio.create_task(self._heartbeat(ws, interval))
        try:
            async for raw in ws:
                await self._handle(ws, json.loads(raw))
        finally:
            heartbeat.cancel()

    def _identify(self) -> dict[str, Any]:
        return {
            "token": self.secrets.get("bot_token", ""),
            "intents": INTENTS,
            "properties": {"os": "linux", "browser": "yoriki", "device": "yoriki"},
        }

    def _closed(self, ws: Any) -> None:
        """接続が閉じた理由で、続けるか（RESUMEか新しいセッションか）、やめるかを決める"""
        code = getattr(ws, "close_code", None)
        if code in FATAL_CLOSE:
            raise PluginConfigError(str(FATAL_CLOSE[code]))
        if code in RESET_CLOSE:
            self._reset()

    def _reset(self) -> None:
        self.session_id, self.resume_url, self.sequence = "", "", None

    async def _heartbeat(self, ws: Any, interval: float) -> None:
        self.acked = True
        await asyncio.sleep(interval * random.random())
        while True:
            if not self.acked:
                # 応答のない接続。閉じてRESUMEする（1000以外のcodeで閉じるとセッションは残る）
                await ws.close(code=4000)
                return
            self.acked = False
            await ws.send(json.dumps({"op": 1, "d": self.sequence}))
            await asyncio.sleep(interval)

    async def _handle(self, ws: Any, payload: dict[str, Any]) -> None:
        op = payload.get("op")
        if payload.get("s") is not None:
            self.sequence = payload["s"]
        if op == 11:
            self.acked = True
        elif op == 1:
            await ws.send(json.dumps({"op": 1, "d": self.sequence}))
        elif op == 7:
            await ws.close(code=4000)  # つなぎ直してRESUMEする
        elif op == 9:
            if not payload.get("d"):
                self._reset()
            await asyncio.sleep(random.uniform(1, 5))
            await ws.close(code=4000)
        elif op == 0:
            await self._dispatch(str(payload.get("t", "")), payload.get("d") or {})

    async def _dispatch(self, event: str, data: dict[str, Any]) -> None:
        if event == "READY":
            self.session_id = str(data.get("session_id", ""))
            self.resume_url = str(data.get("resume_gateway_url", "")).rstrip("/")
            self.failures = 0
            await self.sink.connected(False)
        elif event == "RESUMED":
            self.failures = 0
            await self.sink.connected(True)
        elif event == "GUILD_CREATE" and str(data.get("id", "")) == self.guild_id:
            for channel in [*(data.get("channels") or []), *(data.get("threads") or [])]:
                self._remember(channel)
        elif event in ("CHANNEL_CREATE", "CHANNEL_UPDATE", "THREAD_CREATE", "THREAD_UPDATE"):
            self._remember(data)
        elif event == "THREAD_LIST_SYNC":
            for channel in data.get("threads") or []:
                self._remember(channel)
        elif event == "MESSAGE_CREATE" and str(data.get("guild_id", "")) == self.guild_id:
            await self._message(data)

    def _remember(self, channel: dict[str, Any]) -> None:
        if channel.get("id"):
            parent = str(channel.get("parent_id") or "") if channel.get("type") in THREAD_TYPES else ""
            self.parents[str(channel["id"])] = parent

    async def _message(self, message: dict[str, Any]) -> None:
        if message.get("type") not in REPLY_MESSAGE_TYPES:
            return
        channel = str(message.get("channel_id", ""))
        if channel not in self.parents:
            parent = await asyncio.to_thread(self.adapter.channel_parent, self.secrets, channel)
            if parent is None:
                logger.warning("discord gateway %s: cannot read the channel %s", self.guild_id, channel)
                return
            self.parents[channel] = parent
        if not self.parents[channel]:
            return  # スレッドの外の発言
        event = reply_event(message, self.parents[channel])
        if event is not None:
            await self.sink.events([event])


def _connection_closed() -> type[Exception]:
    from websockets.exceptions import ConnectionClosed  # noqa: PLC0415

    return ConnectionClosed


def _default_connect():
    from websockets.asyncio.client import connect  # noqa: PLC0415  channel-listenerだけが使う

    return connect


def _member_name(member: dict[str, Any], user: dict[str, Any]) -> str:
    return str(member.get("nick") or user.get("global_name") or user.get("username") or "")


def _code(data: Any) -> int | None:
    return data.get("code") if isinstance(data, dict) else None


def _interpret(response: httpx.Response, after_send: AttemptOutcome) -> tuple[Outcome, Any]:
    """RESTの応答を、受付確認・一時的な失敗・失敗・届いたか分からない、に分ける"""
    status = response.status_code
    try:
        data = response.json() if response.content else {}
    except ValueError:
        data = {}
    if 200 <= status < 300:
        return Outcome(AttemptOutcome.ACCEPTED, http_status=status), data
    if status == 429:
        retry = _retry_after(response)
        if retry is None and isinstance(data, dict) and data.get("retry_after") is not None:
            retry = int(float(data["retry_after"])) + 1
        error = _("Discordの呼出しの頻度の上限に達しました")
        return Outcome(AttemptOutcome.RETRYABLE, http_status=status, error=error, retry_after=retry), data
    if status >= 500:
        error = _("Discordが一時的に受け付けません（HTTP %(status)s）") % {"status": status}
        return Outcome(after_send, http_status=status, error=error), data
    return Outcome(AttemptOutcome.FAILED, http_status=status, error=error_text(status, data)), data


ERROR_TEXTS = {
    UNKNOWN_CHANNEL: gettext_lazy("チャネルが見つかりません。channel IDと、Botから見えるかを確かめてください"),
    UNKNOWN_MESSAGE: gettext_lazy("書き換えるメッセージが見つかりません（削除された等）"),
    50001: gettext_lazy("Botがチャネルを見られません"),
    50013: gettext_lazy("Botにチャネルの権限がありません"),
    50083: gettext_lazy("アーカイブされたスレッドです"),
}


def error_text(status: int, data: Any) -> str:
    """Discordのエラーを、利用者に見せる文にする"""
    code = _code(data)
    if status == 401:
        return _("Discordの資格情報が正しくありません")
    if code in ERROR_TEXTS:
        return str(ERROR_TEXTS[code])
    message = data.get("message", "") if isinstance(data, dict) else ""
    return _("Discordが受け付けませんでした（HTTP %(status)s %(message)s）") % {"status": status, "message": message}


def channel_permissions(guild: dict[str, Any], member: dict[str, Any], channel: dict[str, Any], user_id: str) -> int:
    """Botのチャネルでの権限（ロールとチャネルの上書きから計算する。Discordの「Permission Overwrites」の順）"""
    guild_id = str(guild.get("id", ""))
    if str(guild.get("owner_id", "")) == user_id:
        return -1
    roles = {str(r.get("id")): int(r.get("permissions") or 0) for r in guild.get("roles") or []}
    member_roles = [str(r) for r in member.get("roles") or []]
    permissions = roles.get(guild_id, 0)
    for role in member_roles:
        permissions |= roles.get(role, 0)
    if permissions & ADMINISTRATOR:
        return -1
    overwrites = {str(o.get("id")): o for o in channel.get("permission_overwrites") or []}
    everyone = overwrites.get(guild_id)
    if everyone:
        permissions = (permissions & ~int(everyone.get("deny") or 0)) | int(everyone.get("allow") or 0)
    allow = deny = 0
    for role in member_roles:
        overwrite = overwrites.get(role)
        if overwrite:
            allow |= int(overwrite.get("allow") or 0)
            deny |= int(overwrite.get("deny") or 0)
    permissions = (permissions & ~deny) | allow
    own = overwrites.get(user_id)
    if own:
        permissions = (permissions & ~int(own.get("deny") or 0)) | int(own.get("allow") or 0)
    return permissions


def _question_footer(view: QuestionView, link: str) -> tuple[list[str], list[dict[str, Any]]]:
    """質問の下の注記と、最後の行のボタン（「この内容で回答」・Yorikiで開く）"""
    buttons: list[dict[str, Any]] = []
    if view.answer is None and not view.closed:
        if view.confirms_on_select:
            note = _("ボタンを押すと回答が決まります。このスレッドに返信して答えることもできます。")
        else:
            note = _("選択肢を選んで「この内容で回答」を押すか、このスレッドに返信して答えてください。")
            buttons.append(_submit_button(view.clarification))
        lines = [f"-# {note}"]
    elif view.answer is not None:
        lines = [escape(view.answer.text)] if view.answer.text else []
        via = _("Web") if view.answer.via == "web" else _("Discord")
        name = escape(view.answer.name)
        lines.append("-# " + _("%(name)sさんが%(via)sで回答しました。") % {"name": name, "via": via})
    else:
        lines = [f"-# {escape(view.closed)}"]
    if link:
        buttons.append(_link_button(link))
    return lines, buttons


def _row(components: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": 1, "components": components}


def _link_button(link: str) -> dict[str, Any]:
    return {"type": 2, "style": 5, "label": _("Yorikiで開く"), "url": link}


def _option_buttons(clarification: str, index: int, question: dict[str, Any], chosen: list[str]) -> list[dict]:
    buttons = []
    for j, option in enumerate(question["options"]):
        selected = option["label"] in chosen
        buttons.append(
            {
                "type": 2,
                "style": 1 if selected else 2,
                "label": _truncate(("✓ " if selected else "") + option["label"], MAX_BUTTON),
                "custom_id": f"{SELECT_PREFIX}{clarification}:{index}:{j}",
            }
        )
    return buttons


def _submit_button(clarification: str) -> dict[str, Any]:
    return {"type": 2, "style": 3, "label": _("この内容で回答"), "custom_id": f"{SUBMIT_PREFIX}{clarification}"}
