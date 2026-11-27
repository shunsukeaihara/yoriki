"""agentセッションの物理削除（design.md §8）。管理コマンド `purge_deleted_sessions` から呼ぶ（消すと戻せないので、
Q2のScheduleでは定期に動かさない）。

- 対象：論理削除した会話・修正チャット・タスクの実行のうち、削除の操作（`Conversation.workspace_discarded`。削除で
  必ずその日時になり、その後は変わらない）から指定した日数を過ぎたもの
- 回すもの：終わっていないRun・今月に作ったRun（予算の月の実行回数を減らさない）・配信待ち等の配信・消し残した
  snapshotとサブエージェントへのコピーがあるもの。次に流したときにまた調べる
- 消すもの：会話と、その中のRunの記録・添付と成果物・配信・外部スレッドの対応とRunに結び付いた返信の受付記録。
  利用量の記録・入口の受付・起動した会話のカード・Skillの変更履歴は、行を残してRunへの参照を空にする（外部キーの
  SET_NULL）。会話の削除はauditlogに残る
- ファイルの実体：消したagentセッションのArtifactとTool結果の画像のBlobObjectのうち、ほかから参照されていないものに、
  同じtransactionで削除の予約の印を付け、commitの後に保存先から消して行を消す。消せなければ印が残り、次に流したときに
  消し直す
"""

import logging
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from django.db import transaction
from django.db.models import Exists, OuterRef, Q, QuerySet
from django.utils import timezone

from artifact.models import Artifact, BlobObject
from artifact.storage import StorageError, get_storage
from conversation.models import Conversation, ConversationMessage, ConversationPurpose
from delivery.models import SENDABLE_STATUSES, ChannelMessageReceipt, Delivery, DeliveryStatus
from resources.models import ResourceVersion
from runtime.models import PythonWheel
from usage.models import month_start

from .models import TERMINAL_STATUSES, Checkpoint, Run, RunEvent, Subagent, ToolInvocation

logger = logging.getLogger(__name__)

# 利用者が削除できるagentセッションの用途（Skillの評価の会話は消す操作がないので対象にしない）
PURGEABLE_PURPOSES = (ConversationPurpose.CHAT, ConversationPurpose.TASK_FIX, ConversationPurpose.TASK)
# まだ送るかもしれない配信（論理削除の取消がWorkerでまだ反映されていない）
UNSETTLED_DELIVERIES = (*SENDABLE_STATUSES, DeliveryStatus.SENDING)


@dataclass
class PurgeResult:
    """`sessions` は消した（dry-runでは消せる）agentセッションの用途ごとの数、`deferred` は日数を過ぎたが今回は
    回したものの数、`files`・`size` は保存先から消した（dry-runでは消す）ファイルの数と大きさ、`remaining` は削除を
    予約したまま今回も消せなかったファイルの数"""

    sessions: Counter[int] = field(default_factory=Counter)
    deferred: int = 0
    files: int = 0
    size: int = 0
    remaining: int = 0


def purge_deleted_sessions(*, days: int, dry_run: bool = False) -> PurgeResult:
    if days < 1:
        raise ValueError("days must be 1 or more")
    cutoff = timezone.now() - timedelta(days=days)
    result = PurgeResult()
    targets = list(_targets(cutoff).values_list("pk", "purpose"))
    if dry_run:
        purgeable = [(pk, purpose) for pk, purpose in targets if not _blocked(pk, leftovers=True)]
        result.sessions.update(purpose for _pk, purpose in purgeable)
        result.deferred = len(targets) - len(purgeable)
        blobs = _orphans(_session_blobs([pk for pk, _purpose in purgeable]), [pk for pk, _purpose in purgeable])
        reserved = BlobObject.objects.filter(deletion_reserved__isnull=False)
        result.files = len(blobs) + reserved.count()
        result.size = sum(size for _pk, size in blobs) + sum(reserved.values_list("size", flat=True))
        result.remaining = reserved.count()
        return result
    for pk, purpose in targets:
        _discard_leftovers(pk)
        if _purge(pk, cutoff):
            result.sessions[purpose] += 1
        else:
            result.deferred += 1
    result.files, result.size, result.remaining = _delete_reserved()
    if result.sessions or result.files or result.remaining:
        logger.info(
            "purged %d sessions (%d deferred), %d files (%d bytes), %d files remaining",
            result.sessions.total(),
            result.deferred,
            result.files,
            result.size,
            result.remaining,
        )
    return result


def _targets(cutoff: datetime) -> QuerySet[Conversation]:
    return Conversation.objects.filter(
        deleted=True, purpose__in=PURGEABLE_PURPOSES, workspace_discarded__lte=cutoff
    ).order_by("pk")


def _blocked(conversation_id: int, *, leftovers: bool) -> bool:
    """今回は消さないか。`leftovers` なら、消し残したsnapshot・サブエージェントへのコピーがあるものも回す"""
    runs = Run.objects.filter(conversation_id=conversation_id)
    if runs.exclude(status__in=TERMINAL_STATUSES).exists() or runs.filter(created__gte=month_start()).exists():
        return True
    if Delivery.objects.filter(run__conversation_id=conversation_id, status__in=UNSETTLED_DELIVERIES).exists():
        return True
    return leftovers and (
        Checkpoint.objects.filter(run__conversation_id=conversation_id).exists()
        or Subagent.objects.filter(run__conversation_id=conversation_id).exclude(copy_key="").exists()
    )


def _discard_leftovers(conversation_id: int) -> None:
    """消し残したsnapshotとサブエージェントへのコピーを消し直す。消せなければ残り、このagentセッションは回す"""
    from harness.subagents import discard_copy  # noqa: PLC0415  harnessはrunのModelを使うため

    Checkpoint.discard_closed(conversation_id)
    for subagent in Subagent.objects.filter(run__conversation_id=conversation_id).exclude(copy_key=""):
        discard_copy(subagent)


@transaction.atomic
def _purge(conversation_id: int, cutoff: datetime) -> bool:
    # 削除の操作・Runの作成と同じく、会話の行をロックしてから確かめ直す
    conversation = (
        Conversation.objects.select_for_update()
        .filter(pk=conversation_id, deleted=True, workspace_discarded__lte=cutoff)
        .first()
    )
    if conversation is None or _blocked(conversation_id, leftovers=True):
        return False
    blob_ids = {pk for pk, _size in _session_blobs([conversation_id])}
    runs = Run.objects.filter(conversation=conversation)
    _keep_launch_cards(conversation)
    # PROTECTで参照しているものを先に消す（配信 → Runのイベント、Tool呼出し → Attempt）
    Delivery.objects.filter(run__in=runs).delete()
    ChannelMessageReceipt.objects.filter(Q(run__in=runs) | Q(clarification__run__in=runs)).delete()
    ToolInvocation.objects.filter(run__in=runs).delete()
    runs.delete()
    conversation.delete()
    BlobObject.objects.filter(pk__in=blob_ids).exclude(_referenced()).update(deletion_reserved=timezone.now())
    return True


def _keep_launch_cards(conversation: Conversation) -> None:
    """チャットから起動した会話のカードに、削除されたことを出すための内容を写す（Runへの参照は空になる）"""
    messages = ConversationMessage.objects.filter(task_run__conversation=conversation).select_related("task_run__task")
    for message in messages:
        run = message.task_run
        assert run is not None
        fixed = ((run.manifest or {}).get("fixed") or {}).get("task") or {}
        message.purged_task_run = {
            "task": run.task.uid if run.task else "",
            "task_name": fixed.get("name") or (run.task.name if run.task else ""),
            "status": run.status,
            "created": run.created.isoformat(),
            "finished": run.finished.isoformat() if run.finished else None,
        }
        message.save(update_fields=["purged_task_run"])


def _session_blobs(conversation_ids: list[int]) -> set[tuple[int, int]]:
    """agentセッションのArtifactとTool結果の画像のBlobObject（id、大きさ）"""
    if not conversation_ids:
        return set()
    artifacts = Artifact.objects.filter(
        Q(conversation_id__in=conversation_ids) | Q(run__conversation_id__in=conversation_ids)
    )
    blobs = set(artifacts.values_list("blob_id", "blob__size"))
    events = RunEvent.objects.filter(run__conversation_id__in=conversation_ids).values_list("data", flat=True)
    uids = set(_image_uids(events.iterator()))
    if uids:
        blobs |= set(BlobObject.objects.filter(uid__in=uids).values_list("pk", "size"))
    return blobs


def _image_uids(values: Iterable[Any]) -> Iterator[str]:
    """RunEventの内容の中の画像のBlobObjectの参照（harness/context.py の parts_to_json() の形）"""
    for value in values:
        if isinstance(value, dict):
            if value.get("type") == "image" and isinstance(value.get("blob"), str):
                yield value["blob"]
            yield from _image_uids(value.values())
        elif isinstance(value, list):
            yield from _image_uids(value)


def _referenced() -> Q:
    """ほかから参照されているBlobObject（ほかのArtifact、Skillの資料の版、wheel）"""
    return Q(
        Exists(Artifact.objects.filter(blob=OuterRef("pk")))
        | Exists(ResourceVersion.objects.filter(blob=OuterRef("pk")))
        | Exists(PythonWheel.objects.filter(blob=OuterRef("pk")))
    )


def _orphans(blobs: set[tuple[int, int]], conversation_ids: list[int]) -> list[tuple[int, int]]:
    """dry-run用。`conversation_ids` のagentセッションを消したときに、ほかから参照されなくなるもの"""
    if not blobs:
        return []
    others = Artifact.objects.filter(blob=OuterRef("pk")).exclude(
        Q(conversation_id__in=conversation_ids) | Q(run__conversation_id__in=conversation_ids)
    )
    referenced = (
        Exists(others)
        | Exists(ResourceVersion.objects.filter(blob=OuterRef("pk")))
        | Exists(PythonWheel.objects.filter(blob=OuterRef("pk")))
    )
    sizes = dict(blobs)
    return [
        (pk, sizes[pk])
        for pk in BlobObject.objects.filter(pk__in=sizes).exclude(referenced).values_list("pk", flat=True)
    ]


def _delete_reserved() -> tuple[int, int, int]:
    """削除を予約したBlobObjectを保存先から消し、消せたものの行を消す。（消した数、大きさ、消せずに残った数）"""
    removed = size = failed = 0
    for blob in BlobObject.objects.filter(deletion_reserved__isnull=False).prefetch_related("locations"):
        if BlobObject.objects.filter(pk=blob.pk).filter(_referenced()).exists():
            # 予約の後に参照されたものは消さない（参照のないBlobObjectを新しく参照する操作はないので、ふつうは起きない）
            BlobObject.objects.filter(pk=blob.pk).update(deletion_reserved=None)
            continue
        try:
            for location in blob.locations.all():
                get_storage(location.backend).delete(location.key)
        except (StorageError, OSError):
            logger.warning("cannot delete the object of blob %s", blob.uid, exc_info=True)
            failed += 1
            continue
        blob.delete()
        removed += 1
        size += blob.size
    return removed, size, failed
