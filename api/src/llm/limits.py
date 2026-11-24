"""LLMの接続の同時実行数と1分あたりの要求数の上限（design.md §4.8、harness.md §10.8）。

上限は接続（LLMConnection）ごとに持ち、全Workerを通して数える。枠（LLMConnectionLease）は接続の行をロックして数えて
から作る。呼出しが終わった枠も、1分あたりの要求数を数えるため、始めてから60秒は行を残す。Workerが落ちて返せなかった
枠は、期限を過ぎたら同時実行数に数えない。
"""

import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta

from django.db import DatabaseError, transaction
from django.db import connection as db_connection
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import LLMConnection, LLMConnectionLease
from .providers.base import ProviderError

logger = logging.getLogger(__name__)

# 枠が空くのを確かめ直す間隔（秒）
POLL_SECONDS = 1
# 枠が空くのを待つ長さの上限（秒）
MAX_WAIT_SECONDS = 10 * 60
# 枠の期限（秒）。呼出しの間は `beating()` で延ばす
HOLD_SECONDS = 120
# 1分あたりの要求数を数える時間
RATE_WINDOW = timedelta(seconds=60)


class ConnectionBusy(ProviderError):
    """枠が空かなかった。モデルの障害と同じに扱うが、十分に待った後なので、間をおいた再試行はしない"""


def _try_acquire(connection: LLMConnection, holder: str) -> LLMConnectionLease | None:
    with transaction.atomic():
        # 上限を変えたら次の呼出しから効くよう、今の値を読む
        locked = LLMConnection.objects.select_for_update().get(pk=connection.pk)
        now = timezone.now()
        leases = LLMConnectionLease.objects.filter(connection=locked)
        leases.filter(expires__lte=now, created__lte=now - RATE_WINDOW).delete()
        if leases.filter(expires__gt=now).count() >= locked.max_concurrency:
            return None
        limit = locked.requests_per_minute
        if limit is not None and leases.filter(created__gt=now - RATE_WINDOW).count() >= limit:
            return None
        return LLMConnectionLease.objects.create(
            connection=locked, holder=holder, created=now, expires=now + timedelta(seconds=HOLD_SECONDS)
        )


@contextmanager
def connection_slot(
    connection: LLMConnection,
    holder: str,
    *,
    sleep: Callable[[float], None] = time.sleep,
    on_wait: Callable[[], None] = lambda: None,
) -> Iterator[Callable[[], None]]:
    """接続の枠を1つ取ってから中を実行する。MAX_WAIT_SECONDS 待っても空かなければ ConnectionBusy。

    返す関数を呼ぶと枠の期限を延ばす。`on_wait` は待つ間に呼び、例外を投げれば待つのをやめる。呼び出し側の
    transactionの外で使う（枠を他のWorkerに見せるため）
    """
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while (lease := _try_acquire(connection, holder)) is None:
        if time.monotonic() >= deadline:
            raise ConnectionBusy(_("モデルの接続が混み合っていて、呼び出せませんでした。"), retryable=True)
        on_wait()
        sleep(POLL_SECONDS)

    own = LLMConnectionLease.objects.filter(pk=lease.pk)

    def renew() -> None:
        # 期限を過ぎていても、送った要求は続いているので数え直す
        own.update(expires=timezone.now() + timedelta(seconds=HOLD_SECONDS))

    try:
        yield renew
    finally:
        own.update(expires=timezone.now())


@contextmanager
def beating(beat: Callable[[], None], *, interval: float) -> Iterator[Callable[[], None]]:
    """中を実行する間、`beat` を別のスレッドで `interval` 秒ごとに呼ぶ。プロバイダの応答を待つ間は、次のイベントが
    届くまで呼び出し側に制御が戻らず、leaseと枠の期限を延ばせないため（スレッドは自分のDB接続を使い、終わりに閉じる）。

    `beat` の例外（leaseを失った等）は、返す関数を呼んだときに送出する。DBの一時的なエラーは次の回でやり直す
    """
    stop = threading.Event()
    failures: list[Exception] = []

    def loop() -> None:
        try:
            while not stop.wait(interval):
                try:
                    beat()
                except DatabaseError:
                    logger.warning("could not extend the lease", exc_info=True)
                    db_connection.close()
                except Exception as exc:
                    failures.append(exc)
                    return
        finally:
            db_connection.close()

    def check() -> None:
        if failures:
            raise failures[0]

    thread = threading.Thread(target=loop, name="llm-beat", daemon=True)
    thread.start()
    try:
        yield check
    finally:
        stop.set()
        thread.join()
