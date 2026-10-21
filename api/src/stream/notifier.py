"""画面へのイベントの通知（PostgreSQLのLISTEN/NOTIFY。realtime.md §4）。

イベントの正本はDBにあり、通知は「新しいイベントがある」ことを知らせるだけ。送る側はRunEventを保存するのと同じ
transactionで `pg_notify` を実行し（commitした時点で届く）、受ける側はプロセスごとに1本のLISTEN用の接続を持って、
ストリームごとのasyncioのキューへ配る。Redis Pub/Subへ差し替えるときは、このモジュールだけを変える。
"""

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any

import psycopg
from django.db import connection

if TYPE_CHECKING:
    from run.models import RunEvent

logger = logging.getLogger(__name__)

# NOTIFYのpayloadの上限（8000バイト未満）に余裕を持たせる
MAX_PAYLOAD_BYTES = 7000


def run_channel(run_uid: str) -> str:
    return f"run_{run_uid}"


def user_channel(user_uid: str) -> str:
    return f"user_{user_uid}"


def notify(channel: str, payload: dict[str, Any]) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_notify(%s, %s)", [channel, json.dumps(payload, ensure_ascii=False)])


def notify_run_event(event: "RunEvent") -> None:
    """RunEventを記録したtransactionの中で呼ぶ。中身は送らず、ストリームがDBから読む"""
    from run.models import RunEventKind  # noqa: PLC0415  run.modelsがこのモジュールを使うため

    run = event.run
    notify(run_channel(run.uid), {"run": run.uid, "sequence": event.sequence})
    if event.kind == RunEventKind.STATUS:
        # ログインユーザー宛ての通知（会話の一覧の表示等）。Runの状態だけを送る
        owner_uid, conversation_uid = (
            type(run).objects.filter(pk=run.pk).values_list("owner__uid", "conversation__uid").get()
        )
        notify(
            user_channel(owner_uid),
            {
                "type": "run.status",
                "run": run.uid,
                "conversation": conversation_uid,
                "status": event.data.get("status"),
                "sequence": event.sequence,
            },
        )


def notify_delta(run_uid: str, step: int, text: str) -> None:
    """LLM出力の差分。保存しない一時イベントなので、payloadで直接送る（大きければ分ける）"""
    chunk = ""
    for char in text:
        if len((chunk + char).encode()) > MAX_PAYLOAD_BYTES - 200:
            notify(run_channel(run_uid), {"type": "run.delta", "run": run_uid, "step": step, "text": chunk})
            chunk = ""
        chunk += char
    if chunk:
        notify(run_channel(run_uid), {"type": "run.delta", "run": run_uid, "step": step, "text": chunk})


class Listener:
    """プロセスごとに1本のLISTEN用の接続。ストリームはチャネルを購読し、キューで通知を受け取る"""

    def __init__(self):
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._commands: asyncio.Queue[tuple[str, asyncio.Future]] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._connected_once = False

    async def subscribe(self, channel: str) -> asyncio.Queue:
        """LISTENが効いてから返す。返った後に保存されたイベントは、通知で必ず知らされる"""
        queue: asyncio.Queue = asyncio.Queue()
        first = channel not in self._subscribers
        self._subscribers.setdefault(channel, set()).add(queue)
        if first:
            await self._command(f"LISTEN {_quote(channel)}")
        return queue

    async def unsubscribe(self, channel: str, queue: asyncio.Queue) -> None:
        queues = self._subscribers.get(channel)
        if not queues:
            return
        queues.discard(queue)
        if not queues:
            del self._subscribers[channel]
            await self._command(f"UNLISTEN {_quote(channel)}")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _command(self, sql: str) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        future = asyncio.get_running_loop().create_future()
        await self._commands.put((sql, future))
        await future

    async def _run(self) -> None:
        conn: psycopg.AsyncConnection | None = None
        try:
            while True:
                try:
                    if conn is None or conn.closed:
                        reconnecting = conn is not None or self._connected_once
                        conn = await psycopg.AsyncConnection.connect(**_conninfo(), autocommit=True)
                        self._connected_once = True
                        # 接続し直したら、購読中のチャネルをLISTENし直し、その間の通知の取りこぼしを
                        # 購読者にDBから読み直させる
                        for channel in self._subscribers:
                            await conn.execute(f"LISTEN {_quote(channel)}")
                        if reconnecting:
                            for queues in self._subscribers.values():
                                for queue in queues:
                                    queue.put_nowait({"rescan": True})
                    await self._apply_commands(conn)
                    async for notify_ in conn.notifies(timeout=0.2):
                        for queue in list(self._subscribers.get(notify_.channel, ())):
                            queue.put_nowait(json.loads(notify_.payload))
                except (psycopg.OperationalError, OSError):
                    logger.warning("LISTEN connection lost; reconnecting", exc_info=True)
                    conn = None
                    await asyncio.sleep(1)
        finally:
            if conn is not None:
                await conn.close()

    async def _apply_commands(self, conn: psycopg.AsyncConnection) -> None:
        while not self._commands.empty():
            sql, future = self._commands.get_nowait()
            try:
                await conn.execute(sql)
                future.set_result(None)
            except Exception as exc:  # 待っている購読者に失敗を返す
                future.set_exception(exc)


def _quote(channel: str) -> str:
    # チャネル名はRunやユーザーのuid（英数字）から作るが、念のため識別子として引用する
    return '"' + channel.replace('"', '""') + '"'


def _conninfo() -> dict[str, Any]:
    # テストではDjangoが接続先のDB名をテスト用に替えるので、settingsではなく接続の設定を読む
    db = connection.settings_dict
    return {
        "host": db["HOST"],
        "port": db["PORT"],
        "user": db["USER"],
        "password": db["PASSWORD"],
        "dbname": db["NAME"],
    }


_listeners: dict[asyncio.AbstractEventLoop, Listener] = {}


def get_listener() -> Listener:
    """イベントループごとに1つ（Uvicornのワーカーごとに1本の接続になる）"""
    loop = asyncio.get_running_loop()
    if loop not in _listeners:
        _listeners[loop] = Listener()
    return _listeners[loop]
