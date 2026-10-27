"""接続の同時実行数の上限（design.md §4.9.5、harness.md §10.2）。

上限は接続（DatabaseConnection）ごとに持ち、同じ接続の上のConnectorを合わせて、全Workerとapiを通して数える。
枠（ConnectionLease）は接続の行をロックして数えてから作り、操作が終われば消す。Workerが落ちて消せなかった枠は、
期限を過ぎたら数えない。操作の間は、呼び出し側が期限を延ばす（`connection_slot()` が返す関数）。
"""

import time
from collections.abc import Callable, Iterator
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
        ConnectionLease.objects.filter(pk=lease.pk).update(expires=timezone.now() + timedelta(seconds=hold))

    try:
        yield renew
    finally:
        ConnectionLease.objects.filter(pk=lease.pk).delete()
