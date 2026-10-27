"""接続の同時実行数の上限（design.md §4.9.5、harness.md §10.2）。

上限は接続（DatabaseConnection）ごとに持ち、同じ接続の上のConnectorを合わせて、全Workerとapiを通して数える。
枠（ConnectionLease）は接続の行をロックして数えてから作り、操作が終われば消す。Workerが落ちて消せなかった枠は、
期限を過ぎたら数えない。操作の間は、呼び出し側が期限を延ばす（`connection_slot()` が返す関数）。
"""

import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import contextmanager
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from .errors import ConnectorError, ConnectorErrorKind
from .models import ConnectionLease, DatabaseConnection

# 枠が空くのを確かめ直す間隔（秒）
POLL_SECONDS = 0.5
# 枠の期限の余裕（秒）。接続と実行時間の上限に足す
LEASE_MARGIN_SECONDS = 60
# 操作の間に枠の期限を延ばす間隔（秒）
RENEW_SECONDS = 15


def _try_acquire(connection: DatabaseConnection, holder: str, hold_seconds: int) -> ConnectionLease | None:
    with transaction.atomic():
        locked = DatabaseConnection.objects.select_for_update().get(pk=connection.pk)
        now = timezone.now()
        ConnectionLease.objects.filter(connection=locked, expires__lte=now).delete()
        if ConnectionLease.objects.filter(connection=locked).count() >= locked.max_concurrency:
            return None
        return ConnectionLease.objects.create(
            connection=locked, holder=holder, expires=now + timedelta(seconds=hold_seconds)
        )


@contextmanager
def connection_slot(
    connection: DatabaseConnection,
    holder: str,
    *,
    hold_seconds: int,
    wait_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
    on_wait: Callable[[], None] = lambda: None,
) -> Iterator[Callable[[], None]]:
    """接続の枠を1つ取ってから中を実行する。`wait_seconds` 待っても空かなければ ConnectorError（BUSY）。

    `hold_seconds` は中の操作が続きうる長さ（枠の期限は、これに余裕を足したもの）。返す関数を呼ぶと、期限を
    今からその長さだけ延ばす（DBの実行時間の上限は文ごとに効くので、長い操作の間は呼び続ける）。`on_wait` は
    待つ間に呼ぶ（Runnerのleaseを延ばす）。呼び出し側のtransactionの外で使う（枠を他のWorkerに見せるため）
    """
    hold = hold_seconds + LEASE_MARGIN_SECONDS
    deadline = time.monotonic() + wait_seconds
    while (lease := _try_acquire(connection, holder, hold)) is None:
        if time.monotonic() >= deadline:
            raise ConnectorError(
                ConnectorErrorKind.BUSY,
                _("接続先の同時実行数の上限に達しています。しばらく待ってから実行してください"),
            )
        on_wait()
        sleep(POLL_SECONDS)

    def renew() -> None:
        now = timezone.now()
        renewed = ConnectionLease.objects.filter(pk=lease.pk, expires__gt=now).update(
            expires=now + timedelta(seconds=hold)
        )
        if not renewed:
            # 期限までに延ばせず、枠を失った（別の操作が取っているかもしれない）。この操作の結果は使わない
            raise ConnectorError(
                ConnectorErrorKind.BUSY, _("接続先の同時実行の枠を保てませんでした。もう一度実行してください")
            )

    try:
        yield renew
    finally:
        ConnectionLease.objects.filter(pk=lease.pk).delete()


def run_holding[T](
    operation: Callable[[], T],
    renew: Callable[[], None],
    *,
    on_beat: Callable[[], None] = lambda: None,
    interval: float = RENEW_SECONDS,
) -> T:
    """接続先への操作を別のスレッドで行い、終わるのを待つ間、枠の期限を延ばす（`connection_slot()` の `renew`）。
    DBの実行時間の上限は文ごとに効くので、複数の文を実行する操作は期限より長く続きうる。`on_beat` も同じ間隔で
    呼ぶ（RunnerのAttemptのleaseを延ばす）。別のスレッドはDjangoのDBに触らない（接続先のドライバだけを使う）"""
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="connector") as pool:
        future = pool.submit(operation)
        while True:
            try:
                return future.result(timeout=interval)
            except FutureTimeout:
                on_beat()
                renew()
