"""テストの補助"""

import time

from django.db import connection


def wait_for_lock_waiter(timeout: float = 5) -> None:
    """この接続が持つロックを、他の接続が待ち始めるまで待つ（競合のテストで、要求がロックを待つ間に変更を挟む）"""
    deadline = time.monotonic() + timeout
    with connection.cursor() as cursor:
        while True:
            cursor.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE pg_backend_pid() = ANY(pg_blocking_pids(pid)))"
            )
            row = cursor.fetchone()
            if row and row[0]:
                return
            if time.monotonic() > deadline:
                raise AssertionError("no connection waited for the lock")
            time.sleep(0.01)
