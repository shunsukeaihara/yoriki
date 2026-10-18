import hashlib
from collections.abc import Iterator
from contextlib import contextmanager

from django.db import OperationalError, connection, transaction
from psycopg.errors import LockNotAvailable

from .exceptions import Conflict


def _lock_key(name: str) -> int:
    # advisory lockのキーはbigint。名前のハッシュから作る
    return int.from_bytes(hashlib.blake2b(name.encode(), digest_size=8).digest(), "big", signed=True)


@contextmanager
def lock(name: str, timeout: float = 10) -> Iterator[None]:
    """PostgreSQLのadvisory lockで、同じ名前の処理を直列化する。

    transactionの終わりに自動で解放されるlock（`pg_advisory_xact_lock`）を使う。
    `timeout` 秒で取れなければ `Conflict`（409）を送出する。
    RunやDeliveryの実行権の獲得には使わない（claim / leaseで行う。backend-conventions.md §4.3）。
    """
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('lock_timeout', %s, true)", [f"{int(timeout * 1000)}ms"])
            try:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_lock_key(name)])
            except OperationalError as e:
                if isinstance(e.__cause__, LockNotAvailable):
                    raise Conflict() from e
                raise
            cursor.execute("SELECT set_config('lock_timeout', '0', true)")
        yield
