"""Django Q2から呼ぶ関数。引数はIDだけにする（backend-conventions.md §8.4）"""

import logging
import socket
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone
from django.utils.translation import gettext as _

from conversation.models import Conversation
from utils.lock import lock

from . import cleanup
from .dispatch import dispatch_pending, enqueue_run, send_subagent
from .models import (
    LIVE_SUBAGENT_STATUSES,
    MAX_ATTEMPTS,
    ClarificationRequest,
    ClarificationStatus,
    ExecutionAttempt,
    Run,
    RunEndReason,
    RunEvent,
    RunEventKind,
    RunStatus,
    Subagent,
    SubagentStatus,
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
    # 同じqclusterのworkerが同時にclaimしても上限を超えないよう、数える前にqclusterごとに直列にする
    with lock(f"run-claim:{worker}"):
        if _running_here(worker) >= settings.YORIKI_RUNS_PER_QCLUSTER:
            # workerを軽い処理のために残す。Runは順番待ちのままにし、Reconcilerが送り直す（deployment.md §3.2）
            return None
        conversation_id = Run.objects.filter(pk=run_id).values_list("conversation_id", flat=True).first()
        if conversation_id is None:
            return None
        # ロックの順序（会話 → Run）を守る。Run.locked() を参照
        list(Conversation.objects.select_for_update().filter(pk=conversation_id).values_list("pk", flat=True))
        run = Run.objects.select_for_update(skip_locked=True).filter(pk=run_id, status=RunStatus.QUEUED).first()
        if run is None:
            return None
        if run.attempts.filter(subagent__isnull=True).count() >= MAX_ATTEMPTS * 10:
            # 確認待ちからの再開もAttemptを作る。異常に多いRunは止める
            run.set_status(RunStatus.RUNNING)
            run.set_status(RunStatus.FAILED, end_reason=RunEndReason.ATTEMPTS, error=_("再開の回数が多すぎます。"))
            return None
        run.set_status(RunStatus.RUNNING)
        return ExecutionAttempt.start(run, worker=worker)


def execute_subagent(subagent_id: int) -> None:
    """同じ子が再配送されても安全にする。claimできなければ何もしない（harness.md §10.11）"""
    from harness.subagent_runner import SubagentRunner  # noqa: PLC0415  harnessはrunのModelを使うため

    attempt = claim_subagent(subagent_id)
    if attempt is None:
        return
    SubagentRunner(attempt).run()


@transaction.atomic
def claim_subagent(subagent_id: int) -> ExecutionAttempt | None:
    """子はRunと同じく、qclusterの実行枠の中でclaimする（deployment.md §3.2）"""
    from harness.subagents import discard_copy_later  # noqa: PLC0415  harnessはrunのModelを使うため

    worker = socket.gethostname()
    with lock(f"run-claim:{worker}"):
        if _running_here(worker) >= settings.YORIKI_RUNS_PER_QCLUSTER:
            return None  # 順番待ちのままにし、Reconcilerが送り直す
        run_id = Subagent.objects.filter(pk=subagent_id).values_list("run_id", flat=True).first()
        if run_id is None:
            return None
        # ロックの順序（会話 → Run → 子）を守る
        run = Run.locked(run_id)
        subagent = (
            Subagent.objects.select_for_update(skip_locked=True)
            .filter(pk=subagent_id, status=SubagentStatus.QUEUED)
            .first()
        )
        if subagent is None:
            return None
        if run.status not in (RunStatus.RUNNING, RunStatus.WAITING_USER, RunStatus.WAITING_BACKGROUND):
            # 親のRunは止まっている・終わった。子は動かさない
            Subagent.objects.filter(pk=subagent.pk).update(
                status=SubagentStatus.CANCELLED, reported=True, finished=timezone.now()
            )
            discard_copy_later(subagent)
            run.settle_cancelling()
            return None
        subagent.status = SubagentStatus.RUNNING
        subagent.started = timezone.now()
        subagent.save(update_fields=["status", "started"])
        return ExecutionAttempt.start_subagent(subagent, worker=worker)


def _running_here(worker: str) -> int:
    return ExecutionAttempt.objects.filter(worker=worker, ended__isnull=True, lease_expires__gt=timezone.now()).count()


def cleanup_workspaces() -> None:
    """保存期限を過ぎたワークスペースと、消し残したsnapshotを清掃する。Q2のScheduleで1日に1回呼ぶ（design.md §8）"""
    cleanup.cleanup_workspaces()


def cleanup_orphans() -> None:
    """終わったAttemptのコンテナ・作業領域と、記録のない保存先の実体を清掃する。Q2のScheduleで1時間ごとに呼ぶ
    （design.md §8）"""
    # harnessはrunのModelを使うため、循環importを避けて関数内でimportする
    from harness.cleanup import cleanup_orphans as run_cleanup  # noqa: PLC0415

    run_cleanup()


def reconcile_runs() -> None:
    """leaseの切れたRunを復旧し、送れずに残った通知を送り直す。Q2のScheduleで1分ごとに呼ぶ"""
    now = timezone.now()
    stale = ExecutionAttempt.objects.filter(ended__isnull=True, lease_expires__lt=now).values_list("pk", flat=True)
    for attempt_id in list(stale[:100]):
        _recover(attempt_id)
    _expire_clarifications(now)
    _redispatch_waiting(now)
    _redispatch_subagents(now)
    _discard_subagent_copies()
    dispatch_pending()
    # Draftの評価の取りこぼした進行（design.md §3.7.1）
    from conversation.evaluation import reconcile_evaluations  # noqa: PLC0415  conversationがrunをimportするため

    reconcile_evaluations()


@transaction.atomic
def _recover(attempt_id: int) -> None:
    # ロックの順序（会話 → Run → Attempt）を守る
    run_id = ExecutionAttempt.objects.filter(pk=attempt_id).values_list("run_id", flat=True).get()
    run = Run.locked(run_id)
    attempt = ExecutionAttempt.objects.select_for_update().get(pk=attempt_id)
    if attempt.ended is not None or attempt.lease_expires >= timezone.now():
        return  # 先に終わった、またはその間にleaseが延びた
    attempt.end(failure="lease_expired")
    if attempt.subagent_id is not None:
        _recover_subagent(run, attempt)
        return
    if run.status == RunStatus.CANCELLING:
        # 動いているサブエージェントがあれば、全部止まってから停止済みにする（design.md §5.3）
        run.settle_cancelling()
    elif run.status in (RunStatus.RUNNING, RunStatus.WAITING_BACKGROUND):
        # 確認待ちなら、回答が来たときに最後の保存から始める（確認を残す。design.md §5.3）
        failures = run.attempts.filter(subagent__isnull=True).exclude(failure="").exclude(failure="superseded").count()
        if failures >= MAX_ATTEMPTS:
            run.set_status(
                RunStatus.FAILED, end_reason=RunEndReason.ATTEMPTS, error=_("実行が繰り返し中断されました。")
            )
            return
        logger.warning("run %s: lease expired, retrying", run.uid)
        run.set_status(RunStatus.RETRY_WAIT, comment=_("実行が中断されたので、やり直します。"))
        run.set_status(RunStatus.QUEUED)
        enqueue_run(run)


def _recover_subagent(run: Run, attempt: ExecutionAttempt) -> None:
    """Workerやコンテナが落ちた子は再試行せず、失敗として親へ伝える（harness.md §10.11）"""
    from harness import subagents  # noqa: PLC0415  harnessはrunのModelを使うため

    subagent = Subagent.objects.select_for_update().get(attempts=attempt)
    ended = subagents.end(subagent, SubagentStatus.FAILED, error="the worker running it was lost")
    subagents.discard_copy_later(subagent)
    logger.warning("subagent %s of run %s: lease expired", subagent.uid, run.uid)
    if run.status == RunStatus.CANCELLING:
        run.settle_cancelling()
    elif ended:
        run.wake_for_background()


def _redispatch_subagents(now) -> None:
    """順番待ちのまま残った子（どのqclusterも上限で受け取れなかった等）の実行を、もう一度送る"""
    waiting = Subagent.objects.filter(status=SubagentStatus.QUEUED, created__lt=now - REDISPATCH_AFTER)
    for pk in list(waiting.values_list("pk", flat=True)[:100]):
        send_subagent(pk)


def _discard_subagent_copies() -> None:
    """終わった子の一時のコピーが残っていれば消す（子のWorkerが消す前に落ちた等）"""
    from harness.subagents import discard_copy  # noqa: PLC0415  harnessはrunのModelを使うため

    live = ExecutionAttempt.objects.filter(
        subagent=OuterRef("pk"), ended__isnull=True, lease_expires__gt=timezone.now()
    )
    left = Subagent.objects.exclude(copy_key="").exclude(status__in=LIVE_SUBAGENT_STATUSES).exclude(Exists(live))
    for subagent in list(left[:100]):
        discard_copy(subagent)


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
            # ロックの順序（会話 → Run → 確認）を守る。回答（accept_answer）と同じ順
            run_id = ClarificationRequest.objects.filter(pk=pk).values_list("run_id", flat=True).get()
            run = Run.locked(run_id)
            clarification = ClarificationRequest.objects.select_for_update().get(pk=pk)
            if clarification.status != ClarificationStatus.OPEN:
                continue
            clarification.status = ClarificationStatus.EXPIRED
            clarification.save(update_fields=["status"])
            if run.status == RunStatus.WAITING_USER:
                # ジョブが動いていても待たない。Workerは失敗になったことに気づいてコンテナを片付ける（design.md §5.3）。
                # Workerは書けなくなるので、失われるものをここで記録し、次のRunの文脈に入れる（harness.md §5.6）
                if run.has_live_attempt():
                    RunEvent.record(run, RunEventKind.CONTEXT, {"text": EXPIRED_WITH_JOBS_NOTICE})
                run.set_status(RunStatus.FAILED, end_reason=RunEndReason.EXPIRED, error=_("回答の期限が過ぎました。"))


EXPIRED_WITH_JOBS_NOTICE = (
    "[The question expired while background jobs were running. The jobs were stopped, and changes to the files since "
    "the last save were lost. Check the current files and run the jobs again if needed.]"
)
# 回答期限の既定（design.md §5.3。タスクの設定で変えられるようにする）
CLARIFICATION_TTL = timedelta(days=7)
# 順番待ちのRunの実行を送り直すまでの時間
REDISPATCH_AFTER = timedelta(minutes=1)
