"""agentセッションの物理削除のテスト（design.md §8）"""

import io
from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from auditlog.models import LogEntry
from django.core.management import CommandError, call_command
from django.utils import timezone

from artifact.factories import ArtifactFactory
from artifact.models import Artifact, BlobObject
from artifact.storage import StorageError, get_storage
from conversation.factories import ConversationFactory, ConversationMessageFactory
from conversation.models import Conversation, ConversationMessage, ConversationPurpose, MessageRole
from delivery.factories import DestinationFactory
from delivery.models import Delivery, DeliveryStatus, DestinationRevision
from resources.factories import ResourceVersionFactory
from usage.models import UsageLedger, UsagePurpose

from .factories import RunFactory
from .models import Checkpoint, ExecutionAttempt, Run, RunEvent, RunEventKind, RunStatus
from .purge import purge_deleted_sessions

pytestmark = pytest.mark.django_db


def _deleted(*, days_ago: float = 40, purpose=ConversationPurpose.CHAT) -> Conversation:
    """`days_ago` 日前に論理削除した会話"""
    return ConversationFactory.create(
        deleted=True, purpose=purpose, workspace_discarded=timezone.now() - timedelta(days=days_ago)
    )


def _run(conversation: Conversation, *, days_ago: float = 45, status=RunStatus.COMPLETED) -> Run:
    """`days_ago` 日前に作って終わったRun（既定は先月より前）"""
    run = RunFactory.create(conversation=conversation, status=status)
    created = timezone.now() - timedelta(days=days_ago)
    Run.objects.filter(pk=run.pk).update(created=created, finished=created + timedelta(minutes=5))
    run.refresh_from_db()
    return run


def _revision() -> DestinationRevision:
    revision = DestinationFactory.create().current_revision
    assert revision is not None
    return revision


def _exists(blob: BlobObject) -> bool:
    location = blob.location()
    return get_storage(location.backend).exists(location.key)


def test_a_deleted_session_is_purged_after_the_days():
    conversation = _deleted()
    run = _run(conversation)
    artifact = ArtifactFactory.create(conversation=conversation, run=run)
    image = BlobObject.put(io.BytesIO(b"png"))
    RunEvent.record(run, RunEventKind.TOOL_RESULT, {"content": [{"type": "image", "blob": image.uid, "size": 3}]})
    ledger = UsageLedger.objects.create(
        owner=run.owner,
        run=run,
        budget=run.budget,
        model_version=run.model_version,
        purpose=UsagePurpose.STEP,
        input_tokens=10,
        output_tokens=5,
        cost=Decimal("0.01"),
    )
    blob, image_location = artifact.blob, image.location()
    assert _exists(blob)

    result = purge_deleted_sessions(days=30)

    assert (result.sessions[ConversationPurpose.CHAT], result.deferred, result.files) == (1, 0, 2)
    assert result.size == blob.size + image.size
    assert not Conversation.objects.filter(pk=conversation.pk).exists()
    assert not Run.objects.filter(pk=run.pk).exists()
    assert not Artifact.objects.filter(pk=artifact.pk).exists()
    # ファイルの実体も消す
    assert not BlobObject.objects.filter(pk__in=[blob.pk, image.pk]).exists()
    assert not get_storage(image_location.backend).exists(image_location.key)
    # 利用量の記録は、月の利用額を変えないよう残す
    ledger.refresh_from_db()
    assert ledger.run is None
    # 監査ログに物理削除が残る
    assert LogEntry.objects.filter(
        content_type__model="conversation", object_id=conversation.pk, action=LogEntry.Action.DELETE
    ).exists()


def test_sessions_within_the_days_and_not_deleted_are_kept():
    recent = _deleted(days_ago=10)
    _run(recent)
    alive = ConversationFactory.create(workspace_discarded=timezone.now() - timedelta(days=40))
    _run(alive)
    # Skillの評価の会話は、利用者が消す操作がないので対象にしない
    evaluation = _deleted(purpose=ConversationPurpose.EVALUATION)

    result = purge_deleted_sessions(days=30)

    assert (result.sessions.total(), result.deferred) == (0, 0)
    assert Conversation.objects.filter(pk__in=[recent.pk, alive.pk, evaluation.pk]).count() == 3


def test_task_runs_and_fix_chats_are_purged():
    task_run = _run(_deleted(purpose=ConversationPurpose.TASK))
    # チャットから起動したカードは残し、削除されたことだけを出す
    chat = ConversationFactory.create()
    card = ConversationMessageFactory.create(conversation=chat, role=MessageRole.SYSTEM, body="", task_run=task_run)

    result = purge_deleted_sessions(days=30)

    assert result.sessions[ConversationPurpose.TASK] == 1
    card.refresh_from_db()
    assert card.task_run is None
    assert card.purged_task_run["status"] == RunStatus.COMPLETED
    assert ConversationMessage.objects.filter(pk=card.pk).exists()


def test_launch_card_of_a_purged_run_shows_that_it_was_deleted(api_client):
    task_run = _run(_deleted(purpose=ConversationPurpose.TASK))
    chat = ConversationFactory.create()
    ConversationMessageFactory.create(conversation=chat, role=MessageRole.SYSTEM, body="", task_run=task_run)
    purge_deleted_sessions(days=30)

    api_client.force_login(chat.owner)
    [message] = api_client.get(f"/api/app/v1/conversations/{chat.uid}/messages/").json()["results"]
    card = message["task_run"]
    assert (card["id"], card["deleted"], card["response"], card["status"]) == ("", True, "", RunStatus.COMPLETED)


@pytest.mark.parametrize("case", ["live", "this_month", "delivery"])
def test_sessions_that_are_not_settled_are_deferred(case):
    conversation = _deleted()
    if case == "live":
        _run(conversation, status=RunStatus.CANCELLING)
    elif case == "this_month":
        # 予算の月の実行回数を減らさない
        _run(conversation, days_ago=0)
    else:
        run = _run(conversation)
        Delivery.objects.create(
            run=run,
            run_event=RunEvent.record(run, RunEventKind.STATUS, {"status": RunStatus.COMPLETED}),
            event_type="run.completed",
            destination_revision=_revision(),
            payload={},
            status=DeliveryStatus.RETRY_WAIT,
        )

    dry = purge_deleted_sessions(days=30, dry_run=True)
    result = purge_deleted_sessions(days=30)

    assert (dry.sessions.total(), dry.deferred) == (0, 1)
    assert (result.sessions.total(), result.deferred) == (0, 1)
    assert Conversation.objects.filter(pk=conversation.pk).exists()


def test_settled_deliveries_are_deleted_with_the_run():
    run = _run(_deleted(purpose=ConversationPurpose.TASK))
    delivery = Delivery.objects.create(
        run=run,
        run_event=RunEvent.record(run, RunEventKind.STATUS, {"status": RunStatus.COMPLETED}),
        event_type="run.completed",
        destination_revision=_revision(),
        payload={},
        status=DeliveryStatus.CANCELLED,
    )
    assert purge_deleted_sessions(days=30).sessions.total() == 1
    assert not Delivery.objects.filter(pk=delivery.pk).exists()


def test_leftover_snapshots_are_deleted_first_or_the_session_is_deferred():
    conversation = _deleted()
    run = _run(conversation)
    attempt = ExecutionAttempt.objects.create(run=run, number=1, lease_expires=timezone.now(), ended=timezone.now())
    Checkpoint.objects.create(
        run=run, attempt=attempt, sequence=1, storage="test-main", key="snapshots/x.tar", size=1, sha256="0" * 64
    )
    with mock.patch("run.models.get_storage") as storage:
        storage.return_value.delete.side_effect = StorageError("down")
        result = purge_deleted_sessions(days=30)
    assert (result.sessions.total(), result.deferred) == (0, 1)
    assert Conversation.objects.filter(pk=conversation.pk).exists()

    # 消せれば、そのまま物理削除する
    result = purge_deleted_sessions(days=30)
    assert result.sessions.total() == 1


def test_files_referenced_elsewhere_are_kept():
    conversation = _deleted()
    run = _run(conversation)
    # 修正チャット・チャットから起動した実行が引き継いだ入力と、Skillの資料に登録したファイル
    shared = ArtifactFactory.create(conversation=conversation, run=run)
    ArtifactFactory.create(blob=shared.blob)
    registered = ArtifactFactory.create(conversation=conversation, run=run)
    ResourceVersionFactory.create(blob=registered.blob)

    dry = purge_deleted_sessions(days=30, dry_run=True)
    result = purge_deleted_sessions(days=30)

    assert (dry.sessions.total(), dry.files) == (1, 0)
    assert (result.sessions.total(), result.files) == (1, 0)
    assert _exists(shared.blob)
    assert _exists(registered.blob)
    assert BlobObject.objects.filter(pk__in=[shared.blob_id, registered.blob_id], deletion_reserved=None).count() == 2


def test_files_shared_by_two_purged_sessions_are_deleted():
    first, second = _deleted(), _deleted()
    artifact = ArtifactFactory.create(conversation=first, run=_run(first))
    ArtifactFactory.create(conversation=second, run=_run(second), blob=artifact.blob)

    dry = purge_deleted_sessions(days=30, dry_run=True)
    result = purge_deleted_sessions(days=30)

    assert (dry.sessions.total(), dry.files) == (2, 1)
    assert (result.sessions.total(), result.files) == (2, 1)
    assert not BlobObject.objects.filter(pk=artifact.blob_id).exists()


def test_files_that_cannot_be_deleted_are_retried():
    conversation = _deleted()
    artifact = ArtifactFactory.create(conversation=conversation, run=_run(conversation))
    with mock.patch("run.purge.get_storage") as storage:
        storage.return_value.delete.side_effect = StorageError("down")
        result = purge_deleted_sessions(days=30)
    assert (result.sessions.total(), result.files, result.remaining) == (1, 0, 1)
    blob = BlobObject.objects.get(pk=artifact.blob_id)
    assert blob.deletion_reserved is not None
    assert _exists(blob)

    # 次に流したときに消し直す（新しく消すagentセッションがなくても）
    dry = purge_deleted_sessions(days=30, dry_run=True)
    assert (dry.files, dry.remaining) == (1, 1)
    result = purge_deleted_sessions(days=30)
    assert (result.sessions.total(), result.files, result.remaining) == (0, 1, 0)
    assert not BlobObject.objects.filter(pk=blob.pk).exists()


def test_dry_run_changes_nothing():
    conversation = _deleted()
    artifact = ArtifactFactory.create(conversation=conversation, run=_run(conversation))

    result = purge_deleted_sessions(days=30, dry_run=True)

    assert (result.sessions[ConversationPurpose.CHAT], result.files, result.size) == (1, 1, artifact.blob.size)
    assert Conversation.objects.filter(pk=conversation.pk).exists()
    assert _exists(artifact.blob)


def test_command(capsys):
    conversation = _deleted()
    _run(conversation)
    with pytest.raises(CommandError):
        call_command("purge_deleted_sessions", "--days", "0")

    call_command("purge_deleted_sessions", "--days", "30", "--dry-run")
    assert "会話: 1件が対象" in capsys.readouterr().out
    assert Conversation.objects.filter(pk=conversation.pk).exists()

    call_command("purge_deleted_sessions", "--days", "30")
    assert "会話: 1件を消しました" in capsys.readouterr().out
    assert not Conversation.objects.filter(pk=conversation.pk).exists()
