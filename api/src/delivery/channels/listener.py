"""channel-listener（design.md §10、deployment.md §3.1）。常時接続で受けるAdapter（capability listen。Discordの
Gateway）を、接続ごとに1つのlistenerだけが動かす。

- 担当：接続ごとにDBのリース（`listener_owner`・`listener_until`）を取ったlistenerだけがつなぐ。担当は定期的に延長し、
  止まったら期限の後にほかのlistenerが引き継ぐ。何台で動かしてもよい。
- 受けた出来事は、受け口と同じ store_event() で ChannelMessageReceipt に記録し、照合と適用はqclusterで行う。
- 新しいセッションでつないだときは、確認待ちのRunのスレッドを、最後に記録したメッセージの後から読み直して記録する
  （引継ぎ・切断の間の返信を拾う。同じメッセージは重複のキーで1回だけ）。
- 接続の停止・削除・変更（資格情報等。`updated` が変わる）・Pluginの停止で、その接続の受信をやめる（変更なら
  つなぎ直す）。
"""

import asyncio
import logging
import os
import secrets as random_secrets
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta

from asgiref.sync import sync_to_async
from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone

from plugin import builtin, registry
from plugin.errors import PluginConfigError
from run.models import RunStatus

from ..models import ChannelConnection, ChannelMessageReceipt, ExternalThreadBinding, ReceiptKind, ThreadStatus
from .base import ChannelAdapter, InboundEvent
from .inbound import store_event
from .outbound import channel_adapter

logger = logging.getLogger(__name__)

# 担当の期限と、確かめる間隔（期限の間に少なくとも2回延長する）
LEASE = timedelta(seconds=60)
TICK = 15.0
# 資格情報・設定の誤りで止まったときに、つなぎ直すまで待つ秒数（直したら接続が変わって、すぐにつなぎ直す）
CONFIG_ERROR_WAIT = 300.0
# そのほかの失敗で、つなぎ直すまで待つ秒数
FAILURE_WAIT = 15.0


def owner_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{random_secrets.token_hex(4)}"


def listening_connections() -> list[ChannelConnection]:
    """常時接続で受ける、使える接続"""
    return [
        c
        for c in ChannelConnection.objects.alive().filter(suspended=False)
        if registry.is_available(c.extension) and registry.has_capability(c.extension, builtin.LISTEN)
    ]


def claim(connection_id: int, owner: str, now: datetime | None = None) -> bool:
    """担当を取るか延長する。ほかのlistenerが期限の中で持っていれば取れない"""
    now = now or timezone.now()
    return bool(
        ChannelConnection.objects.filter(pk=connection_id)
        .filter(Q(listener_until__isnull=True) | Q(listener_until__lt=now) | Q(listener_owner=owner))
        .update(listener_owner=owner, listener_until=now + LEASE)
    )


def release(connection_id: int, owner: str) -> None:
    ChannelConnection.objects.filter(pk=connection_id, listener_owner=owner).update(
        listener_owner="", listener_until=None
    )


def record(connection_id: int, owner: str, *, connected: bool = False, error: str = "") -> None:
    """受信の状態（つないだ日時・エラー）を記録する。担当のときだけ"""
    values: dict = {"listener_error": error[:500]}
    if connected:
        values["listener_connected"] = timezone.now()
    ChannelConnection.objects.filter(pk=connection_id, listener_owner=owner).update(**values)


def after_ref(connection: ChannelConnection, thread_ref: str) -> str:
    """スレッドで最後に記録した返信の参照（なければスレッドの親）。参照の最後の部分（メッセージのID）の数で比べる"""
    refs = ChannelMessageReceipt.objects.filter(
        connection=connection, thread_ref=thread_ref, kind=ReceiptKind.REPLY
    ).values_list("message_ref", flat=True)
    best, best_id = thread_ref, -1
    for ref in refs:
        last = ref.rpartition("/")[2]
        if last.isdigit() and int(last) > best_id:
            best, best_id = ref, int(last)
    return best


def backfill(connection: ChannelConnection, adapter: ChannelAdapter, secrets: dict[str, str]) -> int:
    """確認待ちのRunのスレッドの返信を読み直して記録する。記録した数"""
    stored = 0
    threads = ExternalThreadBinding.objects.filter(
        connection=connection, status=ThreadStatus.CREATED, run__status=RunStatus.WAITING_USER
    )
    for thread in threads:
        try:
            events = adapter.fetch_replies(
                secrets, thread.channel_id, thread.thread_ref, after_ref(connection, thread.thread_ref)
            )
        except Exception:
            logger.warning("listener: cannot read the thread %s again", thread.thread_ref, exc_info=True)
            continue
        stored += sum(store_event(connection, event) for event in events)
    return stored


@dataclass
class _Sink:
    """`listen` が受けたものを記録する（Adapterへ渡す。ListenSink）"""

    connection: ChannelConnection
    adapter: ChannelAdapter
    secrets: dict[str, str]
    owner: str

    async def events(self, events: list[InboundEvent]) -> None:
        for event in events:
            await sync_to_async(store_event)(self.connection, event)

    async def connected(self, resumed: bool) -> None:
        await sync_to_async(record)(self.connection.pk, self.owner, connected=True)
        if not resumed:
            count = await sync_to_async(backfill)(self.connection, self.adapter, self.secrets)
            if count:
                logger.info("listener: stored %s replies of %s on reconnect", count, self.connection.uid)


class ChannelListener:
    def __init__(self, owner: str | None = None):
        self.owner = owner or owner_name()
        # 接続のpk → (動かしているtask, 始めたときの接続の updated)
        self.running: dict[int, tuple[asyncio.Task, datetime]] = {}

    async def run(self) -> None:
        logger.info("channel listener %s started", self.owner)
        try:
            while True:
                await self.tick()
                await asyncio.sleep(TICK)
        finally:
            await self.stop()

    async def tick(self) -> None:
        await sync_to_async(close_old_connections)()
        connections = {c.pk: c for c in await sync_to_async(listening_connections)()}
        for pk, (task, updated) in list(self.running.items()):
            current = connections.get(pk)
            if current is None or current.updated != updated or task.done():
                await self._stop_one(pk)
        for pk, connection in connections.items():
            if not await sync_to_async(claim)(pk, self.owner):
                if pk in self.running:
                    logger.warning("listener: lost the lease of %s", connection.uid)
                    await self._stop_one(pk, release_lease=False)
                continue
            if pk not in self.running:
                task = asyncio.create_task(self._listen(connection), name=f"channel-listener-{connection.uid}")
                self.running[pk] = (task, connection.updated)

    async def stop(self) -> None:
        for pk in list(self.running):
            await self._stop_one(pk)

    async def _stop_one(self, pk: int, *, release_lease: bool = True) -> None:
        task, _updated = self.running.pop(pk)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("listener: the task of %s failed", pk, exc_info=True)
        if release_lease:
            await sync_to_async(release)(pk, self.owner)

    async def _listen(self, connection: ChannelConnection) -> None:
        logger.info("listener: listening to %s (%s)", connection.uid, connection.extension)
        while True:
            try:
                adapter = await sync_to_async(channel_adapter)(connection.extension)
                secrets = await sync_to_async(connection.secret_values)()
                await adapter.listen(secrets, _Sink(connection, adapter, secrets, self.owner))
                wait = FAILURE_WAIT
            except asyncio.CancelledError:
                raise
            except PluginConfigError as exc:
                logger.warning("listener: %s cannot connect: %s", connection.uid, exc)
                await sync_to_async(record)(connection.pk, self.owner, error=str(exc))
                wait = CONFIG_ERROR_WAIT
            except Exception as exc:
                logger.warning("listener: %s stopped", connection.uid, exc_info=True)
                await sync_to_async(record)(connection.pk, self.owner, error=f"{type(exc).__name__}: {exc}")
                wait = FAILURE_WAIT
            await asyncio.sleep(wait)
