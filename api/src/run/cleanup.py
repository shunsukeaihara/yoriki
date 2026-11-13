"""ワークスペース（snapshot）の清掃（design.md §8）。Q2のSchedule（run.tasks.cleanup_workspaces）と管理コマンド
（cleanup_workspaces）から呼ぶ。

- 保存期限：会話・修正チャットで、動いているRun・確認待ちのRunがなく、最後のRunが終わってから保存期限（基盤の設定）を
  過ぎたものは、アーカイブと同じくワークスペースを消すと記録して（`Conversation.workspace_discarded`）、snapshotを消す。
  次の依頼は空のワークスペースで始まり、前のファイルがないことをモデルに伝える
- 消し残し：消すと決めたのに保存先から消せずに行が残ったsnapshot（アーカイブ・削除・保存期限の後のものと、答える人が
  いないRunのもの）を消し直す
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Exists, F, Max, OuterRef, Q
from django.utils import timezone

from conversation.models import Conversation
from runtime.models import PlatformSettings

from .models import TERMINAL_STATUSES, Checkpoint, Run, RunLaunch, RunTrigger

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CleanupResult:
    """`expired` は保存期限でワークスペースを消した会話の数、`leftover` は消し直しの対象だったsnapshotの数、
    `remaining` は消すと決めたのに今回も消せずに残ったsnapshotの数（dry-runでは今残っている数）"""

    expired: int
    leftover: int
    remaining: int


def cleanup_workspaces(*, dry_run: bool = False) -> CleanupResult:
    now = timezone.now()
    leftover = _leftover().count()
    days = PlatformSettings.load().workspace_retention_days
    cutoff = now - timedelta(days=days) if days is not None else None
    expirable = list(_expired(cutoff).values_list("pk", flat=True)) if cutoff is not None else []
    if dry_run:
        return CleanupResult(expired=len(expirable), leftover=leftover, remaining=leftover)
    _discard_leftover()
    expired = 0
    for conversation_id in expirable:
        if cutoff is not None and _expire(conversation_id, cutoff):
            # 印をcommitした後に消す（消せなければ行が残り、次の清掃で消し直す）
            Checkpoint.discard_closed(conversation_id)
            expired += 1
    result = CleanupResult(expired=expired, leftover=leftover, remaining=_leftover().count())
    if result.expired or result.leftover:
        logger.info(
            "workspace cleanup: %d expired, %d leftover snapshots, %d remaining",
            result.expired,
            result.leftover,
            result.remaining,
        )
    return result


def _expired(cutoff: datetime):
    """保存期限を過ぎた会話。戻せるsnapshotがあり、全部のRunが終わっていて、最後のRunの終了が `cutoff` 以前"""
    restorable = Checkpoint.objects.filter(run__conversation=OuterRef("pk")).filter(
        Q(run__conversation__workspace_discarded__isnull=True)
        | Q(run__created__gt=F("run__conversation__workspace_discarded"))
    )
    live = Run.objects.filter(conversation=OuterRef("pk")).exclude(status__in=TERMINAL_STATUSES)
    return (
        Conversation.objects.annotate(last_finished=Max("runs__finished"))
        .filter(Exists(restorable), ~Exists(live), last_finished__lte=cutoff)
        .order_by("pk")
    )


@transaction.atomic
def _expire(conversation_id: int, cutoff: datetime) -> bool:
    # Runは会話のロックの中で作るので、ロックしてから確かめ直せば、その間に始まったRunのsnapshotを消さない
    conversation = Conversation.objects.select_for_update().get(pk=conversation_id)
    runs = Run.objects.filter(conversation=conversation)
    if runs.exclude(status__in=TERMINAL_STATUSES).exists():
        return False
    last_finished = runs.aggregate(last=Max("finished"))["last"]
    if last_finished is None or last_finished > cutoff:
        return False
    conversation.workspace_discarded = timezone.now()
    conversation.save(update_fields=["workspace_discarded"])
    return True


def _closed() -> Q:
    """ワークスペースを消すと決めた時点より前に始めたRunのsnapshot"""
    return Q(run__created__lte=F("run__conversation__workspace_discarded"))


def _unattended() -> Q:
    """答える人がいないRun（Run.is_interactive の逆）が終わった後に残ったsnapshot"""
    interactive = Q(run__trigger=RunTrigger.CONVERSATION) | Q(run__trigger=RunTrigger.TASK, run__launch=RunLaunch.FIX)
    return Q(run__status__in=TERMINAL_STATUSES) & ~interactive


def _leftover():
    """消すと決めたのに行が残っているsnapshot。止まるのを待っているもの（アーカイブした会話の動いているRun）は除く"""
    live = Run.objects.filter(
        conversation=OuterRef("run__conversation"), created__lte=OuterRef("run__conversation__workspace_discarded")
    ).exclude(status__in=TERMINAL_STATUSES)
    return Checkpoint.objects.filter((_closed() & ~Exists(live)) | _unattended())


def _discard_leftover() -> None:
    closed = Checkpoint.objects.filter(_closed()).values_list("run__conversation_id", flat=True).distinct()
    for conversation_id in list(closed):
        Checkpoint.discard_closed(conversation_id)
    unattended = Checkpoint.objects.filter(_unattended()).values_list("run_id", flat=True).distinct()
    for run in Run.objects.filter(pk__in=list(unattended)):
        Checkpoint.discard_through(run)
