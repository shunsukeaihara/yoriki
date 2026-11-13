"""ワークスペースの清掃のテスト（design.md §8）"""

from datetime import timedelta
from unittest import mock

import pytest
from django.core.management import call_command
from django.utils import timezone

from artifact.storage import StorageError
from conversation.factories import ConversationFactory
from runtime.models import PlatformSettings

from .cleanup import cleanup_workspaces
from .factories import RunFactory
from .models import Checkpoint, ExecutionAttempt, Run, RunLaunch, RunStatus, RunTrigger

pytestmark = pytest.mark.django_db


@pytest.fixture(name="storage")
def fixture_storage():
    """snapshotの保存先の代わり。消したkeyを記録する"""
    with mock.patch("run.models.get_storage") as get_storage:
        yield get_storage.return_value


def make_run(conversation=None, *, days_ago: float | None = 40, status=RunStatus.COMPLETED, **kwargs) -> Run:
    """`days_ago` 日前に終わったRun（Noneなら終わっていない）"""
    run = RunFactory.create(conversation=conversation or ConversationFactory.create(), status=status, **kwargs)
    if days_ago is not None:
        finished = timezone.now() - timedelta(days=days_ago)
        Run.objects.filter(pk=run.pk).update(created=finished - timedelta(minutes=5), finished=finished)
    return run


def make_checkpoint(run: Run, key: str = "") -> Checkpoint:
    attempt = ExecutionAttempt.objects.create(
        run=run, number=run.attempts.count() + 1, lease_expires=timezone.now(), ended=timezone.now()
    )
    return Checkpoint.objects.create(
        run=run,
        attempt=attempt,
        sequence=1,
        storage="local",
        key=key or f"snapshots/{run.uid}.tar",
        size=1,
        sha256="0" * 64,
    )


def test_expired_workspace_is_discarded(storage):
    run = make_run(days_ago=31)
    checkpoint = make_checkpoint(run)
    result = cleanup_workspaces()
    assert (result.expired, result.remaining) == (1, 0)
    assert not Checkpoint.objects.filter(pk=checkpoint.pk).exists()
    storage.delete.assert_called_once_with(checkpoint.key)
    run.conversation.refresh_from_db()
    # アーカイブと同じ印を付ける（次の依頼は空から始め、前のファイルがないことをモデルに伝える）。一覧からは外さない
    assert run.conversation.workspace_discarded is not None
    assert run.conversation.archived is None and not run.conversation.deleted


def test_counts_from_the_last_finished_run(storage):
    # Sandboxを使わないやり取りが続いている会話は、古いsnapshotでも残す
    old = make_run(days_ago=60)
    checkpoint = make_checkpoint(old)
    make_run(old.conversation, days_ago=29)
    assert cleanup_workspaces().expired == 0
    assert Checkpoint.objects.filter(pk=checkpoint.pk).exists()
    old.conversation.refresh_from_db()
    assert old.conversation.workspace_discarded is None


@pytest.mark.parametrize("status", [RunStatus.WAITING_USER, RunStatus.RUNNING, RunStatus.QUEUED])
def test_live_or_waiting_conversation_is_kept(status, storage):
    old = make_run(days_ago=60)
    make_checkpoint(old)
    make_run(old.conversation, days_ago=None, status=status)
    assert cleanup_workspaces().expired == 0
    assert Checkpoint.objects.count() == 1


def test_retention_follows_the_setting(storage):
    make_checkpoint(make_run(days_ago=10))
    assert cleanup_workspaces().expired == 0
    PlatformSettings.objects.update(workspace_retention_days=7)
    assert cleanup_workspaces().expired == 1
    assert not Checkpoint.objects.exists()


def test_empty_retention_never_expires(storage):
    settings = PlatformSettings.load()
    settings.workspace_retention_days = None
    settings.save()
    make_checkpoint(make_run(days_ago=1000))
    assert cleanup_workspaces().expired == 0
    assert Checkpoint.objects.count() == 1


def test_conversation_without_snapshot_is_not_marked(storage):
    run = make_run(days_ago=60)
    assert cleanup_workspaces().expired == 0
    run.conversation.refresh_from_db()
    assert run.conversation.workspace_discarded is None


def test_expired_again_after_a_discard(storage):
    # 一度消した後に再開した会話は、その後のsnapshotだけを見る
    conversation = ConversationFactory.create(workspace_discarded=timezone.now() - timedelta(days=50))
    make_checkpoint(make_run(conversation, days_ago=40))
    assert cleanup_workspaces().expired == 1
    assert cleanup_workspaces().expired == 0
    assert not Checkpoint.objects.exists()


def test_failed_delete_is_retried():
    checkpoint = make_checkpoint(make_run(days_ago=31))
    with mock.patch("run.models.get_storage") as get_storage:
        get_storage.return_value.delete.side_effect = StorageError("down")
        result = cleanup_workspaces()
        # 消せなくても印は付き、行が残る
        assert (result.expired, result.remaining) == (1, 1)
        assert Checkpoint.objects.filter(pk=checkpoint.pk).exists()
        get_storage.return_value.delete.side_effect = None
        result = cleanup_workspaces()
    assert (result.expired, result.leftover, result.remaining) == (0, 1, 0)
    assert not Checkpoint.objects.exists()


def test_leftover_of_an_archived_conversation(storage):
    conversation = ConversationFactory.create(workspace_discarded=timezone.now(), archived=timezone.now())
    make_checkpoint(make_run(conversation, days_ago=1))
    result = cleanup_workspaces()
    assert (result.expired, result.leftover, result.remaining) == (0, 1, 0)
    assert not Checkpoint.objects.exists()


def test_archived_conversation_waits_for_its_runs(storage):
    # 止まるのを待っているRunがあれば、消し残しに数えず、消さない
    conversation = ConversationFactory.create()
    make_checkpoint(make_run(conversation, days_ago=1))
    make_run(conversation, days_ago=None, status=RunStatus.CANCELLING)
    conversation.workspace_discarded = timezone.now()
    conversation.save()
    result = cleanup_workspaces()
    assert (result.leftover, result.remaining) == (0, 0)
    assert Checkpoint.objects.count() == 1


def test_leftover_of_an_unattended_run(storage):
    # 答える人がいないRun（入口・手動実行のタスク）のsnapshotは、期限を待たずに消し直す
    task_run = make_run(days_ago=0.01, trigger=RunTrigger.TASK, launch=RunLaunch.MANUAL)
    make_checkpoint(task_run)
    fix_run = make_run(days_ago=0.01, trigger=RunTrigger.TASK, launch=RunLaunch.FIX)
    kept = make_checkpoint(fix_run)
    result = cleanup_workspaces()
    assert (result.expired, result.leftover, result.remaining) == (0, 1, 0)
    assert list(Checkpoint.objects.all()) == [kept]


def test_dry_run_changes_nothing(storage):
    run = make_run(days_ago=31)
    make_checkpoint(run)
    make_checkpoint(make_run(days_ago=0.01, trigger=RunTrigger.TASK, launch=RunLaunch.MANUAL))
    result = cleanup_workspaces(dry_run=True)
    assert (result.expired, result.leftover) == (1, 1)
    assert Checkpoint.objects.count() == 2
    storage.delete.assert_not_called()
    run.conversation.refresh_from_db()
    assert run.conversation.workspace_discarded is None


def test_command(storage, capsys):
    make_checkpoint(make_run(days_ago=31))
    call_command("cleanup_workspaces", "--dry-run")
    assert "1件が対象" in capsys.readouterr().out
    assert Checkpoint.objects.count() == 1
    call_command("cleanup_workspaces")
    assert "1件のワークスペースを消しました" in capsys.readouterr().out
    assert not Checkpoint.objects.exists()
