"""Runの実行の通知（Outbox → Q2）。

通知はat-least-onceで、重複はRunのclaimで吸収する（design.md §5.2、§6.3）。Q2の結果テーブルは履歴として使わない。
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django_q.tasks import async_task

from .models import Run, RunDispatch


def enqueue_run(run: Run) -> RunDispatch:
    """Runの状態の更新と同じtransactionの中で呼ぶ。commitした後にQ2へ送る"""
    dispatch = RunDispatch.objects.create(run=run)
    transaction.on_commit(lambda: send_dispatch(dispatch.pk))
    return dispatch


def send_dispatch(dispatch_id: int) -> None:
    dispatch = RunDispatch.objects.filter(pk=dispatch_id, dispatched__isnull=True).first()
    if dispatch is None:
        return
    async_task("run.tasks.execute_run", dispatch.run_id, task_name=f"run-{dispatch.run_id}")
    RunDispatch.objects.filter(pk=dispatch.pk).update(dispatched=timezone.now())


def dispatch_pending(older_than_seconds: int = 30) -> int:
    """送れずに残った通知を送り直す（commitの後にプロセスが落ちた場合）。定期的に呼ぶ"""
    threshold = timezone.now() - timedelta(seconds=older_than_seconds)
    ids = list(
        RunDispatch.objects.filter(dispatched__isnull=True, created__lt=threshold).values_list("pk", flat=True)[:100]
    )
    for pk in ids:
        send_dispatch(pk)
    return len(ids)
