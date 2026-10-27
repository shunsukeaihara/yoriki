"""接続の同時実行数の上限（design.md §4.9.5）"""

import time
from datetime import timedelta

import pytest
from django.utils import timezone

from connector.errors import ConnectorError, ConnectorErrorKind
from connector.factories import DatabaseConnectionFactory
from connector.limits import connection_slot, run_holding
from connector.models import ConnectionLease


@pytest.mark.django_db
def test_slots_are_limited_per_connection():
    connection = DatabaseConnectionFactory.create(max_concurrency=2)
    other = DatabaseConnectionFactory.create(max_concurrency=1)
    with (
        connection_slot(connection, "a", hold_seconds=30, wait_seconds=0),
        connection_slot(connection, "b", hold_seconds=30, wait_seconds=0),
    ):
        assert ConnectionLease.objects.filter(connection=connection).count() == 2
        with pytest.raises(ConnectorError) as raised, connection_slot(connection, "c", hold_seconds=30, wait_seconds=0):
            pass
        assert raised.value.kind == ConnectorErrorKind.BUSY
        # 別の接続の枠は別に数える
        with connection_slot(other, "d", hold_seconds=30, wait_seconds=0):
            pass
    assert not ConnectionLease.objects.exists()


@pytest.mark.django_db
def test_slot_is_released_when_the_operation_fails():
    connection = DatabaseConnectionFactory.create(max_concurrency=1)
    with pytest.raises(RuntimeError), connection_slot(connection, "a", hold_seconds=30, wait_seconds=0):
        raise RuntimeError("failed")
    assert not ConnectionLease.objects.exists()


@pytest.mark.django_db
def test_expired_slot_is_not_counted():
    connection = DatabaseConnectionFactory.create(max_concurrency=1)
    ConnectionLease.objects.create(connection=connection, holder="lost", expires=timezone.now() - timedelta(seconds=1))
    with connection_slot(connection, "a", hold_seconds=30, wait_seconds=0):
        assert list(ConnectionLease.objects.values_list("holder", flat=True)) == ["a"]


@pytest.mark.django_db
def test_waits_for_a_slot_to_be_freed():
    connection = DatabaseConnectionFactory.create(max_concurrency=1)
    held = ConnectionLease.objects.create(
        connection=connection, holder="other", expires=timezone.now() + timedelta(minutes=5)
    )
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        # 待っている間に、他の操作が枠を返す
        waits.append(seconds)
        held.delete()

    beats: list[int] = []
    with connection_slot(
        connection, "a", hold_seconds=30, wait_seconds=10, sleep=sleep, on_wait=lambda: beats.append(1)
    ):
        assert list(ConnectionLease.objects.values_list("holder", flat=True)) == ["a"]
    assert len(waits) == 1 and beats == [1]


@pytest.mark.django_db
def test_slot_can_be_renewed_during_a_long_operation():
    connection = DatabaseConnectionFactory.create(max_concurrency=1)
    with connection_slot(connection, "a", hold_seconds=30, wait_seconds=0) as renew:
        lease = ConnectionLease.objects.get()
        ConnectionLease.objects.filter(pk=lease.pk).update(expires=timezone.now() + timedelta(seconds=1))
        renew()
        lease.refresh_from_db()
        assert lease.expires > timezone.now() + timedelta(seconds=60)


def test_run_holding_renews_while_the_operation_runs():
    renewed: list[int] = []
    beats: list[int] = []

    def operation() -> str:
        time.sleep(0.1)
        return "done"

    result = run_holding(operation, lambda: renewed.append(1), on_beat=lambda: beats.append(1), interval=0.01)
    assert result == "done"
    assert renewed and len(beats) == len(renewed)


@pytest.mark.django_db
def test_lost_slot_cannot_be_renewed():
    connection = DatabaseConnectionFactory.create(max_concurrency=1)
    with (
        pytest.raises(ConnectorError) as raised,
        connection_slot(connection, "a", hold_seconds=30, wait_seconds=0) as renew,
    ):
        # 延ばす前に期限が切れて、別の操作が枠を取った
        ConnectionLease.objects.update(expires=timezone.now() - timedelta(seconds=1))
        with connection_slot(connection, "b", hold_seconds=30, wait_seconds=0):
            renew()
    assert raised.value.kind == ConnectorErrorKind.BUSY
