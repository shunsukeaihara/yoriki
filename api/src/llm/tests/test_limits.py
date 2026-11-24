"""LLMの接続の同時実行数と1分あたりの要求数の上限（llm/limits.py）"""

import threading
import time
from datetime import timedelta

import pytest
from django.db import DatabaseError, connection
from django.utils import timezone

from llm import limits
from llm.factories import LLMConnectionFactory
from llm.limits import ConnectionBusy, beating, connection_slot
from llm.models import LLMConnection, LLMConnectionLease


@pytest.fixture(autouse=True)
def fixture_no_wait(monkeypatch):
    monkeypatch.setattr(limits, "MAX_WAIT_SECONDS", 0)


def lease(target, *, started=0, expires=60) -> LLMConnectionLease:
    now = timezone.now()
    return LLMConnectionLease.objects.create(
        connection=target,
        holder="other",
        created=now - timedelta(seconds=started),
        expires=now + timedelta(seconds=expires),
    )


@pytest.mark.django_db
def test_concurrency_is_counted_per_connection():
    target, other = LLMConnectionFactory.create(max_concurrency=1), LLMConnectionFactory.create(max_concurrency=1)
    with connection_slot(target, "a"), connection_slot(other, "b"):
        with pytest.raises(ConnectionBusy) as info, connection_slot(target, "c"):
            raise AssertionError("not reached")
        assert info.value.retryable
    with connection_slot(target, "d"):
        pass


@pytest.mark.django_db
def test_slot_is_returned_when_the_call_fails():
    target = LLMConnectionFactory.create(max_concurrency=1)
    with pytest.raises(RuntimeError), connection_slot(target, "a"):
        raise RuntimeError("failed")
    with connection_slot(target, "b"):
        pass


@pytest.mark.django_db
def test_expired_slot_is_not_counted_and_old_rows_are_removed():
    target = LLMConnectionFactory.create(max_concurrency=1)
    lease(target, started=300, expires=-1)
    with connection_slot(target, "a"):
        assert list(LLMConnectionLease.objects.values_list("holder", flat=True)) == ["a"]


@pytest.mark.django_db
def test_requests_per_minute_counts_finished_calls():
    target = LLMConnectionFactory.create(requests_per_minute=2)
    for holder in ("a", "b"):
        with connection_slot(target, holder):
            pass
    with pytest.raises(ConnectionBusy), connection_slot(target, "c"):
        pass
    LLMConnectionLease.objects.filter(holder="a").update(created=timezone.now() - timedelta(seconds=61))
    with connection_slot(target, "c"):
        pass


@pytest.mark.django_db
def test_changed_limit_takes_effect_on_the_next_call():
    target = LLMConnectionFactory.create(max_concurrency=1)
    lease(target)
    LLMConnection.objects.filter(pk=target.pk).update(max_concurrency=2)
    with connection_slot(target, "a"):
        pass


@pytest.mark.django_db
def test_waits_for_a_slot(monkeypatch):
    monkeypatch.setattr(limits, "MAX_WAIT_SECONDS", 10)
    target = LLMConnectionFactory.create(max_concurrency=1)
    held = lease(target)
    waits: list[str] = []

    def release(_seconds: float) -> None:
        LLMConnectionLease.objects.filter(pk=held.pk).update(expires=timezone.now())

    with connection_slot(
        target,
        "a",
        sleep=release,
        on_wait=lambda: waits.append("wait"),
    ):
        assert waits == ["wait"]


@pytest.mark.django_db
def test_waiting_can_be_stopped(monkeypatch):
    monkeypatch.setattr(limits, "MAX_WAIT_SECONDS", 10)
    target = LLMConnectionFactory.create(max_concurrency=1)
    lease(target)

    def stop() -> None:
        raise RuntimeError("stop")

    with pytest.raises(RuntimeError), connection_slot(target, "a", on_wait=stop):
        pass
    assert LLMConnectionLease.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_acquisition_does_not_exceed_the_limit():
    target = LLMConnectionFactory.create(max_concurrency=2)
    barrier, results = threading.Barrier(6), []

    def acquire() -> None:
        try:
            barrier.wait()
            results.append(limits._try_acquire(target, "t") is not None)
        finally:
            connection.close()

    threads = [threading.Thread(target=acquire) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [False] * 4 + [True] * 2


def test_beating_retries_database_errors_and_reports_the_others():
    beats: list[int] = []

    def beat() -> None:
        beats.append(1)
        if len(beats) == 1:
            raise DatabaseError("temporary")
        if len(beats) == 3:
            raise RuntimeError("lost")

    with beating(beat, interval=0.01) as check:
        check()
        deadline = time.monotonic() + 5
        while len(beats) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)
        with pytest.raises(RuntimeError, match="lost"):
            check()
    assert len(beats) == 3
