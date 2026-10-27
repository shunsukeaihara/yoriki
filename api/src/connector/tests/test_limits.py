"""接続の同時実行数の上限（design.md §4.9.5）"""

from datetime import timedelta

import pytest
from django.utils import timezone

from connector.errors import ConnectorError, ConnectorErrorKind
from connector.factories import DatabaseConnectionFactory
from connector.limits import connection_slot
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
