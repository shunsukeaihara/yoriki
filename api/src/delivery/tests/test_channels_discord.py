"""対話の出口（Discord。design.md §4.6・§4.7・§10）。親メッセージからのスレッド、ボタン（Interactions Endpoint
URL。Ed25519）、Gatewayでの返信の受信、つなぎ直した後の読み直し、channel-listenerの担当、接続とチャネルの確かめ"""

import asyncio
import json
import time
from datetime import timedelta
from typing import Any
from unittest import mock

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.utils import timezone

from delivery.channels import inbound, listener
from delivery.channels.discord import (
    ADMINISTRATOR,
    CREATE_PUBLIC_THREADS,
    INTENTS,
    READ_MESSAGE_HISTORY,
    SEND_MESSAGES,
    SEND_MESSAGES_IN_THREADS,
    VIEW_CHANNEL,
    DiscordAdapter,
    channel_permissions,
    escape,
)
from delivery.factories import ChannelConnectionFactory, channel_destination
from delivery.models import (
    ChannelConnection,
    ChannelEvent,
    ChannelMessageReceipt,
    Delivery,
    DeliveryStatus,
    ExternalThreadBinding,
    ReceiptResult,
    ThreadStatus,
)
from delivery.tasks import send_delivery
from harness.tests.fakes import FakeAdapter, calls, reply
from harness.tests.test_connectors import execute
from llm.factories import ModelProfileFactory
from plugin import builtin
from plugin.errors import PluginConfigError
from run.models import ClarificationRequest, ClarificationStatus, RunLaunch, RunStatus
from task.dispatch import start_task_run
from task.factories import TaskDefinitionFactory
from task.models import TaskScope
from user.factories import UserFactory, grant
from utils.permissions import AppPermission

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("default_runtime")]

GUILD = "900"
CHANNEL = "901"
BOT = "500"
APP = "500"
PRIVATE_KEY = Ed25519PrivateKey.generate()
PUBLIC_KEY = PRIVATE_KEY.public_key().public_bytes_raw().hex()
SECRETS = {"bot_token": "bot-token", "public_key": PUBLIC_KEY, "guild_id": GUILD}
QUESTION = {
    "header": "取引先",
    "question": "どちらの取引先として扱いますか？",
    "options": [{"label": "A社"}, {"label": "B社", "description": "子会社として扱う"}],
}
ALL_NEEDED = VIEW_CHANNEL | SEND_MESSAGES | READ_MESSAGE_HISTORY | CREATE_PUBLIC_THREADS | SEND_MESSAGES_IN_THREADS


class FakeDiscord:
    """DiscordのRESTの代わり。呼ばれた (method, path, body) を残し、決めた応答を返す"""

    def __init__(self):
        self.calls: list[tuple[str, str, Any]] = []
        self.responses: dict[tuple[str, str], list[httpx.Response | Exception]] = {}
        self.next_id = 7000
        self.threads: set[str] = set()
        # GET channels/<thread>/messages で返すメッセージ
        self.history: dict[str, list[dict[str, Any]]] = {}
        self.app_flags = 1 << 18
        self.channel_type = 0
        self.role_permissions = ALL_NEEDED

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v10/")
        body = json.loads(request.content) if request.content else dict(request.url.params)
        method = request.method
        self.calls.append((method, path, body))
        queued = self.responses.get((method, path))
        if queued:
            response = queued.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        fixed = self._fixed().get(path)
        if fixed is not None:
            return httpx.Response(200, json=fixed)
        return self._channels(method, path.split("/"), body)

    def _fixed(self) -> dict[str, Any]:
        roles = [{"id": GUILD, "permissions": "0"}, {"id": "77", "permissions": str(self.role_permissions)}]
        return {
            "users/@me": {"id": BOT, "username": "yoriki", "bot": True},
            "applications/@me": {"id": APP, "verify_key": PUBLIC_KEY, "flags": self.app_flags},
            f"guilds/{GUILD}": {"id": GUILD, "name": "開発", "owner_id": "1", "roles": roles},
            f"guilds/{GUILD}/members/{BOT}": {"roles": ["77"], "user": {"id": BOT}},
        }

    def _channels(self, method: str, parts: list[str], body: Any) -> httpx.Response:
        if method == "GET" and len(parts) == 2:
            channel = {"id": parts[1], "type": self.channel_type, "name": "作業", "guild_id": GUILD}
            if parts[1] in self.threads:
                channel = {"id": parts[1], "type": 11, "parent_id": CHANNEL, "guild_id": GUILD}
            return httpx.Response(200, json=channel)
        if method == "POST" and len(parts) == 5 and parts[4] == "threads":
            self.threads.add(parts[3])
            return httpx.Response(201, json={"id": parts[3], "type": 11, "parent_id": parts[1]})
        if method == "POST" and len(parts) == 3 and parts[2] == "messages":
            if parts[1] != CHANNEL and parts[1] not in self.threads:
                return httpx.Response(404, json={"code": 10003, "message": "Unknown Channel"})
            self.next_id += 1
            return httpx.Response(200, json={"id": str(self.next_id), "channel_id": parts[1]})
        if method == "GET" and len(parts) == 3 and parts[2] == "messages":
            after = int(body.get("after", "0"))
            return httpx.Response(200, json=[m for m in self.history.get(parts[1], []) if int(m["id"]) > after])
        return httpx.Response(200, json={})

    def requests(self, method: str, prefix: str = "") -> list[tuple[str, Any]]:
        return [(p, b) for m, p, b in self.calls if m == method and p.startswith(prefix)]


@pytest.fixture(name="discord")
def fixture_discord():
    fake = FakeDiscord()
    with mock.patch.object(DiscordAdapter, "transport", httpx.MockTransport(fake)):
        yield fake


@pytest.fixture(name="manager")
def fixture_manager(db):
    return grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)


@pytest.fixture(autouse=True)
def fixture_no_q():
    with mock.patch("delivery.outbox.async_task"), mock.patch("delivery.channels.inbound.async_task"):
        yield


def _connection(**kwargs) -> ChannelConnection:
    return ChannelConnectionFactory.create(
        extension=builtin.DISCORD,
        name="Discord",
        secrets=json.dumps(SECRETS),
        workspace_id=GUILD,
        workspace_name="開発",
        bot_user_id=BOT,
        **kwargs,
    )


def _task(connection: ChannelConnection):
    return TaskDefinitionFactory.create(
        scope=TaskScope.COMPANY,
        revision__interaction_destination=channel_destination(
            connection, revision__channel_id=CHANNEL, revision__channel_name="#作業"
        ),
        revision__model_profile=ModelProfileFactory.create(version__supports_tools=True),
    )


def _send_all(run):
    for delivery in Delivery.objects.filter(run=run, status=DeliveryStatus.PENDING).order_by("pk"):
        send_delivery(delivery.pk)


def _process():
    for pk in ChannelMessageReceipt.objects.filter(result=ReceiptResult.RECEIVED).values_list("pk", flat=True):
        inbound.process_receipt(pk)


def _waiting_run(manager, connection, questions=(QUESTION,)):
    run = start_task_run(_task(connection), inputs={}, actor=manager, launch=RunLaunch.MANUAL)
    run = execute(run, FakeAdapter([calls(("q1", "ask_user", {"questions": list(questions)}))]))
    assert run.status == RunStatus.WAITING_USER, run.error
    _send_all(run)
    return run


def _signed(body: bytes, *, key=PRIVATE_KEY, timestamp: int | None = None) -> dict[str, str]:
    stamp = str(timestamp or int(time.time()))
    return {
        "content_type": "application/json",
        "HTTP_X_SIGNATURE_ED25519": key.sign(stamp.encode() + body).hex(),
        "HTTP_X_SIGNATURE_TIMESTAMP": stamp,
    }


def _press(client, connection, thread: ExternalThreadBinding, message_ref: str, custom_id: str, *, user="42", id_="1"):
    payload = {
        "id": id_,
        "type": 3,
        "application_id": APP,
        "token": f"token-{id_}",
        "guild_id": GUILD,
        "channel_id": thread.thread_ref,
        "channel": {"id": thread.thread_ref, "type": 11, "parent_id": CHANNEL},
        "member": {"nick": "佐藤", "user": {"id": user, "username": "sato"}},
        "message": {"id": message_ref.rpartition("/")[2], "channel_id": thread.thread_ref},
        "data": {"custom_id": custom_id, "component_type": 2},
    }
    body = json.dumps(payload).encode()
    return client.post(f"/api/hooks/channels/discord/{connection.uid}/", data=body, **_signed(body))


def _message(thread_ref: str, message_id: str, text: str, *, author="42", bot=False) -> dict[str, Any]:
    return {
        "id": message_id,
        "type": 0,
        "guild_id": GUILD,
        "channel_id": thread_ref,
        "content": text,
        "author": {"id": author, "username": "sato", "global_name": "佐藤", "bot": bot},
    }


# --- 送信と、ボタンでの回答 ---


def test_question_is_posted_in_a_thread_and_answered_by_a_button(client, manager, discord, settings):
    settings.YORIKI_PUBLIC_URL = "https://yoriki.example.com"
    connection = _connection()
    run = _waiting_run(manager, connection)

    # 親メッセージを投稿し、そのメッセージから公開スレッドを作って、スレッドへ質問を投稿する
    (parent_path, parent), (question_path, posted) = discord.requests("POST", "channels/")[0::2]
    assert parent_path == f"channels/{CHANNEL}/messages"
    thread = ExternalThreadBinding.objects.get(run=run)
    assert thread.status == ThreadStatus.CREATED
    assert (f"channels/{CHANNEL}/messages/{thread.thread_ref}/threads") in [p for p, _b in discord.requests("POST")]
    assert question_path == f"channels/{thread.thread_ref}/messages"
    assert parent["allowed_mentions"] == {"parse": []}
    assert "どちらの取引先" in posted["content"] and "子会社として扱う" in posted["content"]
    buttons = [c for row in posted["components"] for c in row["components"]]
    clarification = ClarificationRequest.objects.get(run=run)
    assert f"yoriki:s:{clarification.uid}:0:1" in [b.get("custom_id") for b in buttons]
    assert any(b.get("url", "").endswith(f"/runs/{run.uid}") for b in buttons)
    question = Delivery.objects.get(run=run, event_type=ChannelEvent.QUESTION)
    assert question.external_ref.startswith(f"{thread.thread_ref}/")

    # ボタン：受け口は記録して「受付」（メッセージを変えない遅延の応答）を返す
    response = _press(client, connection, thread, question.external_ref, f"yoriki:s:{clarification.uid}:0:1")
    assert response.status_code == 200
    assert response.json() == {"type": 6}
    receipt = ChannelMessageReceipt.objects.get()
    assert receipt.action["reply_token"] == f"{APP}/token-1"
    _process()
    receipt.refresh_from_db()
    assert receipt.result == ReceiptResult.APPLIED, receipt.reason
    assert "reply_token" not in receipt.action  # 処理の後に残さない
    clarification.refresh_from_db()
    assert clarification.status == ClarificationStatus.ANSWERED
    assert clarification.answers == [{"selected": ["B社"], "text": ""}]
    assert clarification.answered_external == {"provider": "discord", "user_id": "42", "name": "佐藤"}

    # 質問のメッセージを、回答の形に書き換える（スレッドの中のメッセージ）
    _send_all(run)
    path, update = discord.requests("PATCH")[-1]
    assert path == f"channels/{thread.thread_ref}/messages/{question.external_ref.rpartition('/')[2]}"
    assert "佐藤さんがDiscordで回答しました" in update["content"]
    assert not [c for row in update["components"] for c in row["components"] if c.get("custom_id")]

    # 完了をスレッドへ報告する
    run = execute(run, FakeAdapter([reply("B社として処理しました。")]))
    assert run.status == RunStatus.COMPLETED, run.error
    _send_all(run)
    path, report = discord.requests("POST", "channels/")[-1]
    assert path == f"channels/{thread.thread_ref}/messages"
    assert "完了しました" in report["content"] and "B社として処理しました" in report["content"]


def test_refused_button_is_answered_only_to_the_person(client, manager, discord):
    connection = _connection()
    options = [{"label": "課税"}, {"label": "非課税"}]
    second = {"header": "税区分", "question": "どれですか？", "options": options, "multi_select": True}
    run = _waiting_run(manager, connection, questions=(QUESTION, second))
    thread = ExternalThreadBinding.objects.get(run=run)
    clarification = ClarificationRequest.objects.get(run=run)
    question = Delivery.objects.get(run=run, event_type=ChannelEvent.QUESTION)
    # 何も選ばずに「この内容で回答」を押すと、押した本人だけに見える応答で知らせる
    _press(client, connection, thread, question.external_ref, f"yoriki:submit:{clarification.uid}")
    _process()
    path, body = discord.requests("POST", "webhooks/")[-1]
    assert path == f"webhooks/{APP}/token-1"
    assert body["flags"] == 64
    assert ChannelMessageReceipt.objects.get().result == ReceiptResult.REJECTED


def test_thread_is_created_again_when_only_the_parent_was_posted(manager, discord):
    connection = _connection()
    # スレッドの作成に失敗しても、親メッセージは送れている（スレッドは次の投稿のときに作る）
    discord.responses[("POST", f"channels/{CHANNEL}/messages/7001/threads")] = [
        httpx.Response(500, json={"message": "error"}),
    ]
    run = _waiting_run(manager, connection)
    thread = ExternalThreadBinding.objects.get(run=run)
    assert thread.status == ThreadStatus.CREATED and thread.thread_ref == "7001"
    question = Delivery.objects.get(run=run, event_type=ChannelEvent.QUESTION)
    assert question.status == DeliveryStatus.DELIVERED, question.last_error
    posts = [p for p, _b in discord.requests("POST")]
    assert posts.count(f"channels/{CHANNEL}/messages/7001/threads") >= 2
    assert posts[-1] == "channels/7001/messages"


# --- 受け口の認証 ---


def test_interactions_must_be_signed_and_ping_is_answered(client, discord):
    connection = _connection()
    url = f"/api/hooks/channels/discord/{connection.uid}/"
    body = json.dumps({"type": 1, "id": "p"}).encode()
    response = client.post(url, data=body, **_signed(body))
    assert response.status_code == 200 and response.json() == {"type": 1}
    other = Ed25519PrivateKey.generate()
    assert client.post(url, data=body, **_signed(body, key=other)).status_code == 401
    old = int(time.time()) - 3600
    assert client.post(url, data=body, **_signed(body, timestamp=old)).status_code == 401
    # Slackの受け口では受けない
    assert client.post(f"/api/hooks/channels/slack/{connection.uid}/", data=body, **_signed(body)).status_code == 404
    assert not ChannelMessageReceipt.objects.exists()


# --- Gatewayでの返信 ---


class FakeSocket:
    """GatewayのWebSocketの代わり。決めたpayloadを順に返し、送られたものを残す。最後に閉じる"""

    def __init__(self, payloads: list[dict[str, Any]], close_code: int = 1000):
        self.payloads = [json.dumps(p) for p in payloads]
        self.sent: list[dict[str, Any]] = []
        self.close_code: int | None = None
        self._final = close_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def recv(self):
        return self.payloads.pop(0)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.payloads:
            self.close_code = self._final
            raise StopAsyncIteration
        return self.payloads.pop(0)

    async def send(self, data: str):
        self.sent.append(json.loads(data))

    async def close(self, code: int = 1000):
        self.close_code = code


def _gateway_payloads(thread_ref: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"op": 10, "d": {"heartbeat_interval": 60000}},
        {"op": 0, "s": 1, "t": "READY", "d": {"session_id": "S1", "resume_gateway_url": "wss://resume.example"}},
        {
            "op": 0,
            "s": 2,
            "t": "GUILD_CREATE",
            "d": {
                "id": GUILD,
                "channels": [{"id": CHANNEL, "type": 0, "parent_id": "category"}],
                "threads": [{"id": thread_ref, "type": 11, "parent_id": CHANNEL}],
            },
        },
        *[{"op": 0, "s": 3 + i, "t": "MESSAGE_CREATE", "d": m} for i, m in enumerate(messages)],
    ]


def _run_gateway(socket: FakeSocket, sink) -> None:
    adapter = DiscordAdapter()
    with mock.patch.object(DiscordAdapter, "connect", lambda *a, **k: socket):

        async def once():
            from delivery.channels.discord import _Gateway  # noqa: PLC0415

            gateway = _Gateway(adapter, SECRETS, sink)
            await gateway._connect_once()
            return gateway

        asyncio.run(once())


@pytest.mark.django_db(transaction=True)
def test_gateway_reply_answers_the_question(manager, discord):
    connection = _connection()
    run = _waiting_run(manager, connection)
    thread = ExternalThreadBinding.objects.get(run=run)
    messages = [
        _message(CHANNEL, "8000", "スレッドの外の発言"),
        _message(thread.thread_ref, "8001", "bot自身", author=BOT, bot=True),
        _message(thread.thread_ref, "8002", "B社でお願いします"),
    ]
    socket = FakeSocket(_gateway_payloads(thread.thread_ref, messages))
    owner = "test-owner"
    assert listener.claim(connection.pk, owner)
    adapter = DiscordAdapter()
    _run_gateway(socket, listener._Sink(connection, adapter, SECRETS, owner))

    identify = socket.sent[0]
    assert identify["op"] == 2 and identify["d"]["intents"] == INTENTS
    # スレッドの外の発言は記録しない。botの投稿は記録して、取り込まない
    receipts = list(ChannelMessageReceipt.objects.order_by("pk"))
    assert [r.dedup_key for r in receipts] == ["message:8001", "message:8002"]
    assert receipts[1].channel_id == CHANNEL and receipts[1].thread_ref == thread.thread_ref
    _process()
    clarification = ClarificationRequest.objects.get(run=run)
    assert clarification.status == ClarificationStatus.ANSWERED
    assert clarification.comment == "B社でお願いします"
    assert clarification.answered_external["name"] == "佐藤"
    assert ChannelMessageReceipt.objects.get(dedup_key="message:8001").result == ReceiptResult.IGNORED
    connection.refresh_from_db()
    assert connection.listener_connected is not None and connection.listener_error == ""


def test_gateway_resumes_and_stops_on_fatal_close(discord):
    adapter = DiscordAdapter()
    from delivery.channels.discord import _Gateway  # noqa: PLC0415

    sink = mock.AsyncMock()
    gateway = _Gateway(adapter, SECRETS, sink)
    gateway.session_id, gateway.sequence, gateway.resume_url = "S1", 5, "wss://resume.example"
    socket = FakeSocket([{"op": 10, "d": {"heartbeat_interval": 60000}}, {"op": 0, "s": 6, "t": "RESUMED", "d": {}}])
    with mock.patch.object(DiscordAdapter, "connect", lambda url, **k: socket):
        asyncio.run(gateway._connect_once())
    assert socket.sent[0] == {"op": 6, "d": {"token": "bot-token", "session_id": "S1", "seq": 5}}
    sink.connected.assert_awaited_with(True)

    socket = FakeSocket([{"op": 10, "d": {"heartbeat_interval": 60000}}], close_code=4014)
    with (
        mock.patch.object(DiscordAdapter, "connect", lambda url, **k: socket),
        pytest.raises(PluginConfigError, match="Message Content Intent"),
    ):
        asyncio.run(gateway._connect_once())


def test_new_session_reads_the_waiting_threads_again(manager, discord):
    connection = _connection()
    run = _waiting_run(manager, connection)
    thread = ExternalThreadBinding.objects.get(run=run)
    discord.history[thread.thread_ref] = [
        _message(thread.thread_ref, "8005", "後の返信"),
        _message(thread.thread_ref, "8003", "記録済み"),
    ]
    ChannelMessageReceipt.objects.create(
        connection=connection, kind="reply", dedup_key="message:8003", thread_ref=thread.thread_ref,
        message_ref=f"{thread.thread_ref}/8003", result=ReceiptResult.APPLIED,
    )  # fmt: skip
    stored = listener.backfill(connection, DiscordAdapter(), SECRETS)
    assert stored == 1
    _path, params = discord.requests("GET", f"channels/{thread.thread_ref}/messages")[-1]
    assert params["after"] == "8003"
    _process()
    assert ClarificationRequest.objects.get(run=run).comment == "後の返信"


# --- channel-listenerの担当 ---


def test_lease_is_held_by_one_listener():
    connection = _connection()
    assert listener.claim(connection.pk, "a")
    assert not listener.claim(connection.pk, "b")
    assert listener.claim(connection.pk, "a")  # 延長
    later = timezone.now() + listener.LEASE + timedelta(seconds=1)
    assert listener.claim(connection.pk, "b", now=later)  # 期限が切れたら引き継ぐ
    listener.release(connection.pk, "a")  # もう担当ではない
    assert ChannelConnection.objects.get(pk=connection.pk).listener_owner == "b"


@pytest.mark.django_db(transaction=True)
def test_listener_starts_only_listening_connections_and_stops_suspended_ones(discord):
    discord_connection = _connection()
    ChannelConnectionFactory.create()  # Slackは受け口で受ける
    started: list[str] = []

    async def listen(self, secrets, sink):
        started.append(secrets["guild_id"])
        await asyncio.sleep(3600)

    async def scenario():
        worker = listener.ChannelListener(owner="me")
        with mock.patch.object(DiscordAdapter, "listen", listen):
            await worker.tick()
            await asyncio.sleep(0)
            assert list(worker.running) == [discord_connection.pk]
            await asyncio.to_thread(
                lambda: ChannelConnection.objects.filter(pk=discord_connection.pk).update(suspended=True)
            )
            await worker.tick()
            assert worker.running == {}

    asyncio.run(scenario())
    assert started == [GUILD]
    connection = ChannelConnection.objects.get(pk=discord_connection.pk)
    assert connection.listener_owner == "" and connection.listener_until is None


# --- 接続とチャネルの確かめ ---


def test_connection_checks_the_key_the_intent_and_the_guild(discord):
    adapter = DiscordAdapter()
    info = adapter.check_connection(SECRETS)
    assert (info.workspace_id, info.workspace_name, info.bot_user_id) == (GUILD, "開発", BOT)
    with pytest.raises(PluginConfigError, match="Public Key"):
        adapter.check_connection({**SECRETS, "public_key": "ab" * 32})
    discord.app_flags = 0
    with pytest.raises(PluginConfigError, match="Message Content Intent"):
        adapter.check_connection(SECRETS)
    discord.app_flags = 1 << 19
    discord.responses[("GET", f"guilds/{GUILD}")] = [httpx.Response(404, json={"code": 10004})]
    with pytest.raises(PluginConfigError, match="参加していません"):
        adapter.check_connection(SECRETS)


def test_channel_must_be_a_text_channel_with_the_permissions(discord):
    adapter = DiscordAdapter()
    assert adapter.check_channel(SECRETS, CHANNEL).name == "#作業"
    discord.role_permissions = ALL_NEEDED & ~CREATE_PUBLIC_THREADS
    with pytest.raises(PluginConfigError, match="公開スレッドを作成"):
        adapter.check_channel(SECRETS, CHANNEL)
    discord.role_permissions = ADMINISTRATOR
    adapter.check_channel(SECRETS, CHANNEL)
    discord.channel_type = 15  # フォーラム
    with pytest.raises(PluginConfigError, match="テキストチャネル"):
        adapter.check_channel(SECRETS, CHANNEL)


def test_channel_permissions_follow_the_overwrites():
    guild = {"id": GUILD, "owner_id": "1", "roles": [{"id": GUILD, "permissions": str(VIEW_CHANNEL | SEND_MESSAGES)}]}
    member = {"roles": ["77"]}
    channel = {
        "permission_overwrites": [
            {"id": GUILD, "type": 0, "allow": "0", "deny": str(SEND_MESSAGES)},
            {"id": "77", "type": 0, "allow": str(SEND_MESSAGES), "deny": "0"},
            {"id": BOT, "type": 1, "allow": "0", "deny": str(VIEW_CHANNEL)},
        ]
    }
    granted = channel_permissions(guild, member, channel, BOT)
    assert granted & SEND_MESSAGES and not granted & VIEW_CHANNEL


def test_connection_api_accepts_discord_secrets(api_client, discord):
    from django.contrib.auth.models import Permission  # noqa: PLC0415

    user = UserFactory.create()
    user.user_permissions.add(
        *Permission.objects.filter(content_type__app_label="delivery", codename__endswith="_channelconnection")
    )
    api_client.force_authenticate(UserFactory._meta.model.objects.get(pk=user.pk))
    response = api_client.post(
        "/api/v1/channel_connections/",
        {"name": "開発のDiscord", "extension": builtin.DISCORD, **SECRETS},
        format="json",
    )
    assert response.status_code == 201, response.json()
    data = response.json()
    assert data["workspace_id"] == GUILD and data["bot_user_id"] == BOT
    assert data["receive_url"].endswith(f"/api/hooks/channels/discord/{data['id']}/")
    assert data["listener"]["listens"] is True and data["listener"]["active"] is False
    assert "bot_token" not in data and "public_key" not in data
    stored = ChannelConnection.objects.get(uid=data["id"]).secret_values()
    assert stored == SECRETS


def test_text_is_escaped():
    assert escape("@everyone <@1> **x**") == "\\@everyone \\<\\@1\\> \\*\\*x\\*\\*"
