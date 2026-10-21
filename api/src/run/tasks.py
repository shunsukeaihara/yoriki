"""Django Q2から呼ぶ関数。引数はIDだけにする（backend-conventions.md §8.4）"""

import logging
import socket
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from .dispatch import dispatch_pending, enqueue_run
from .models import (
    MAX_ATTEMPTS,
    ClarificationRequest,
    ClarificationStatus,
    ExecutionAttempt,
    Run,
    RunEndReason,
    RunStatus,
)

logger = logging.getLogger(__name__)


def execute_run(run_id: int) -> None:
    """同じRun IDが再配送されても安全にする。claimできなければ何もしない"""
    # harnessはrunのModelを使うため、循環importを避けて関数内でimportする
    from harness.runner import Runner  # noqa: PLC0415

    attempt = claim_run(run_id)
    if attempt is None:
        return
    Runner(attempt).run()


@transaction.atomic
def claim_run(run_id: int) -> ExecutionAttempt | None:
    worker = socket.gethostname()
    if _running_here(worker) >= settings.YORIKI_RUNS_PER_QCLUSTER:
        # workerを軽い処理のために残す。Runは順番待ちのままにし、Reconcilerが送り直す（deployment.md §3.2）
        return None
    run = Run.objects.select_for_update(skip_locked=True).filter(pk=run_id, status=RunStatus.QUEUED).first()
    if run is None:
        return None
    if run.attempts.count() >= MAX_ATTEMPTS * 10:
        # 確認待ちからの再開もAttemptを作る。異常に多いRunは止める
        run.set_status(RunStatus.RUNNING)
        run.set_status(RunStatus.FAILED, end_reason=RunEndReason.ATTEMPTS, error=_("再開の回数が多すぎます。"))
        return None
    run.set_status(RunStatus.RUNNING)
    return ExecutionAttempt.start(run, worker=worker)


def _running_here(worker: str) -> int:
    return ExecutionAttempt.objects.filter(worker=worker, ended__isnull=True, lease_expires__gt=timezone.now()).count()


def reconcile_runs() -> None:
    """leaseの切れたRunを復旧し、送れずに残った通知を送り直す。Q2のScheduleで1分ごとに呼ぶ"""
    now = timezone.now()
    stale = ExecutionAttempt.objects.filter(ended__isnull=True, lease_expires__lt=now).values_list("pk", flat=True)
    for attempt_id in list(stale[:100]):
        _recover(attempt_id)
    _expire_clarifications(now)
    _redispatch_waiting(now)
    dispatch_pending()


@transaction.atomic
def _recover(attempt_id: int) -> None:
    attempt = ExecutionAttempt.objects.select_for_update().get(pk=attempt_id)
    if attempt.ended is not None:
        return
    run = Run.locked(attempt.run_id)
    attempt.end(failure="lease_expired")
    if run.status == RunStatus.CANCELLING:
        run.set_status(RunStatus.CANCELLED, comment=_("停止しました。"), end_reason=RunEndReason.CANCELLED)
    elif run.status == RunStatus.RUNNING:
        failures = run.attempts.exclude(failure="").exclude(failure="superseded").count()
        if failures >= MAX_ATTEMPTS:
            run.set_status(
                RunStatus.FAILED, end_reason=RunEndReason.ATTEMPTS, error=_("実行が繰り返し中断されました。")
            )
            return
        logger.warning("run %s: lease expired, retrying", run.uid)
        run.set_status(RunStatus.RETRY_WAIT, comment=_("実行が中断されたので、やり直します。"))
        run.set_status(RunStatus.QUEUED)
        enqueue_run(run)


def _redispatch_waiting(now) -> None:
    """順番待ちのまま残ったRun（どのqclusterも上限で受け取れなかった等）の実行を、もう一度送る"""
    threshold = now - REDISPATCH_AFTER
    waiting = Run.objects.filter(status=RunStatus.QUEUED, updated__lt=threshold).exclude(
        dispatches__dispatched__isnull=True
    )
    for run in waiting.exclude(dispatches__created__gte=threshold)[:100]:
        with transaction.atomic():
            enqueue_run(run)


def _expire_clarifications(now) -> None:
    expired = ClarificationRequest.objects.filter(status=ClarificationStatus.OPEN, expires__lt=now).values_list(
        "pk", flat=True
    )
    for pk in list(expired[:100]):
        with transaction.atomic():
            clarification = ClarificationRequest.objects.select_for_update().get(pk=pk)
            if clarification.status != ClarificationStatus.OPEN:
                continue
            clarification.status = ClarificationStatus.EXPIRED
            clarification.save(update_fields=["status"])
            run = Run.locked(clarification.run_id)
            if run.status == RunStatus.WAITING_USER:
                run.set_status(RunStatus.FAILED, end_reason=RunEndReason.EXPIRED, error=_("回答の期限が過ぎました。"))


# 回答期限の既定（design.md §5.3。タスクの設定で変えられるようにする）
CLARIFICATION_TTL = timedelta(days=7)
# 順番待ちのRunの実行を送り直すまでの時間
REDISPATCH_AFTER = timedelta(minutes=1)
