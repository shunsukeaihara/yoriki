"""Workerのdrainのテスト（deployment.md §6）。Runnerの区切りでの動きは harness/tests/test_drain.py"""

from datetime import timedelta
from io import StringIO
from threading import Event, Thread
from unittest import mock

import pytest
from django.core.management import CommandError, call_command
from django.db import connection, transaction
from django.utils import timezone

from utils.lock import lock

from .factories import RunFactory
from .models import WORKER_DRAIN_LOCK, ExecutionAttempt, Run, RunDispatch, RunStatus, WorkerDrain
from .tasks import claim_run, reconcile_runs

pytestmark = pytest.mark.django_db


def command(name: str, **options) -> str:
    out = StringIO()
    call_command(name, stdout=out, **options)
    return out.getvalue()


def live_attempt() -> ExecutionAttempt:
    return ExecutionAttempt.start(RunFactory.create(status=RunStatus.RUNNING))


def test_claim_is_refused_until_resume():
    run = RunFactory.create(status=RunStatus.QUEUED)
    command("drain_workers")
    assert claim_run(run.pk) is None
    run.refresh_from_db()
    assert run.status == RunStatus.QUEUED

    command("resume_workers")
    assert claim_run(run.pk) is not None


def test_reconciler_does_not_resend_while_draining():
    run = RunFactory.create(status=RunStatus.QUEUED)
    # 送ったが、どのqclusterも受け取らなかった
    past = timezone.now() - timedelta(minutes=5)
    sent = RunDispatch.objects.create(run=run, dispatched=past)
    RunDispatch.objects.filter(pk=sent.pk).update(created=past)
    Run.objects.filter(pk=run.pk).update(updated=past)
    WorkerDrain.start()
    reconcile_runs()
    assert RunDispatch.objects.filter(run=run).count() == 1

    WorkerDrain.stop()
    reconcile_runs()
    assert RunDispatch.objects.filter(run=run).count() == 2


def test_commands_are_repeatable():
    assert "始めました" in command("drain_workers")
    assert "すでにdrain中" in command("drain_workers")
    assert "解除しました" in command("resume_workers")
    assert "drain中ではありません" in command("resume_workers")


def test_without_wait_only_shows_what_is_running():
    live_attempt()
    assert "Run 1件、サブエージェント 0件" in command("drain_workers")


def test_wait_returns_when_nothing_is_running():
    attempt = live_attempt()
    # Workerが落ちたもの（leaseの切れたもの）は待たない
    lost = live_attempt()
    ExecutionAttempt.objects.filter(pk=lost.pk).update(lease_expires=timezone.now() - timedelta(seconds=1))

    with mock.patch("run.management.commands.drain_workers.time.sleep", side_effect=lambda _s: attempt.end()) as sleep:
        output = command("drain_workers", wait=True)
    assert sleep.call_count == 1
    assert "Run 1件" in output
    assert "動いているものはありません" in output
    assert WorkerDrain.is_active()


def test_wait_times_out_and_keeps_draining():
    live_attempt()
    with pytest.raises(CommandError, match="時間内に終わりませんでした"):
        command("drain_workers", wait=True, timeout=0)
    assert WorkerDrain.is_active()


def test_wait_stops_when_the_drain_is_lifted():
    live_attempt()
    with (
        mock.patch("run.management.commands.drain_workers.time.sleep", side_effect=lambda _s: WorkerDrain.stop()),
        pytest.raises(CommandError, match="解除されました"),
    ):
        command("drain_workers", wait=True)


def test_timeout_needs_wait():
    with pytest.raises(CommandError, match="--wait"):
        command("drain_workers", timeout=10)
    assert not WorkerDrain.is_active()


@pytest.mark.django_db(transaction=True)
def test_start_waits_for_claims_in_progress():
    """claimの途中のものが終わってから印を書く（印を書いた後に数えれば、動き出すものを見落とさない）"""
    claiming, release, started = Event(), Event(), Event()

    def claim():
        try:
            with transaction.atomic(), lock(WORKER_DRAIN_LOCK, shared=True):
                claiming.set()
                release.wait(10)
        finally:
            connection.close()

    def drain():
        try:
            WorkerDrain.start()
            started.set()
        finally:
            connection.close()

    threads = [Thread(target=claim), Thread(target=drain)]
    threads[0].start()
    try:
        assert claiming.wait(10)
        # claimどうしは待たない
        with lock(WORKER_DRAIN_LOCK, timeout=1, shared=True):
            pass
        threads[1].start()
        assert not started.wait(0.5)
    finally:
        release.set()
        for thread in threads:
            if thread.ident is not None:
                thread.join(10)
    assert started.is_set()
    assert WorkerDrain.is_active()
