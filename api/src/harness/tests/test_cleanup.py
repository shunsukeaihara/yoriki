"""実行の残骸の清掃のテスト（design.md §8）"""

import io
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from django.conf import settings
from django.core.management import call_command
from django.utils import timezone

from artifact.storage import StorageError, get_storage
from conversation.factories import ConversationFactory
from harness.cleanup import cleanup_objects, cleanup_worker
from harness.sandbox import ATTEMPT_LABEL, DATABASE_LABEL, attempt_dir_name, attempts_root, database_label
from run.factories import RunFactory
from run.models import Checkpoint, ExecutionAttempt, Run, Subagent
from runtime.launcher import SandboxUnavailable

pytestmark = pytest.mark.django_db


class FakeLauncher:
    def __init__(self, containers: list[dict]):
        self.items = containers
        self.removed: list[str] = []

    def containers(self) -> list[dict]:
        return [c for c in self.items if c["Id"] not in self.removed]

    def remove(self, container_id: str) -> None:
        self.removed.append(container_id)


def no_docker():
    raise SandboxUnavailable("cannot connect to Docker")


def make_run() -> Run:
    return RunFactory.create(conversation=ConversationFactory.create())


def attempt(run: Run, *, alive: bool, expired_minutes: int = 0, subagent: Subagent | None = None) -> ExecutionAttempt:
    """`alive` ならleaseの切れていないAttempt。`expired_minutes` はleaseが切れてからの分（終わっていない）"""
    now = timezone.now()
    lease = now + timedelta(minutes=5) if alive else now - timedelta(minutes=expired_minutes)
    ended = None if alive or expired_minutes else now
    number = ExecutionAttempt.objects.filter(run=run, subagent=subagent).count() + 1
    return ExecutionAttempt.objects.create(run=run, subagent=subagent, number=number, lease_expires=lease, ended=ended)


def make_subagent(run: Run, **kwargs) -> Subagent:
    return Subagent.objects.create(
        run=run, tool_call_id="call_1", description="集計", prompt="集計して", model_version=run.model_version, **kwargs
    )


def put(key: str) -> str:
    get_storage().put_immutable(key, io.BytesIO(b"x"))
    return key


def remaining_keys() -> set[str]:
    storage = get_storage()
    return set(storage.list_keys("snapshots/runs/")) | set(storage.list_keys("subagents/"))


def run_cleanup(dry_run: bool = False, launcher: FakeLauncher | None = None):
    factory: Any = (lambda: launcher) if launcher else no_docker
    return cleanup_worker(dry_run=dry_run, launcher_factory=factory)


@pytest.fixture(autouse=True)
def fixture_workspaces(settings, tmp_path):
    settings.YORIKI_WORKSPACES_DIR = str(tmp_path / "workspaces")


def test_stale_sandboxes_are_removed():
    run = make_run()
    live, ended = attempt(run, alive=True), attempt(run, alive=False)
    database = database_label()
    launcher = FakeLauncher(
        [
            {"Id": "live", "Labels": {ATTEMPT_LABEL: str(live.pk), DATABASE_LABEL: database}},
            {"Id": "ended", "Labels": {ATTEMPT_LABEL: str(ended.pk), DATABASE_LABEL: database}},
            {"Id": "other-db", "Labels": {ATTEMPT_LABEL: str(ended.pk), DATABASE_LABEL: "other"}},
        ]
    )
    assert run_cleanup(dry_run=True, launcher=launcher).sandboxes == 1
    assert launcher.removed == []
    result = run_cleanup(launcher=launcher)
    assert (result.sandboxes, result.failed) == (1, 0)
    assert launcher.removed == ["ended"]


def test_sandbox_that_cannot_be_removed_is_counted_as_failed():
    run = make_run()
    ended = attempt(run, alive=False)
    launcher = FakeLauncher(
        [{"Id": "ended", "Labels": {ATTEMPT_LABEL: str(ended.pk), DATABASE_LABEL: database_label()}}]
    )
    launcher.remove = lambda container_id: None  # type: ignore[method-assign]
    result = run_cleanup(launcher=launcher)
    assert (result.sandboxes, result.failed) == (0, 1)


def test_sandboxes_are_skipped_without_docker():
    assert run_cleanup().sandboxes == 0


def test_workspaces_of_finished_attempts_are_removed(tmp_path):
    run = make_run()
    live, ended = attempt(run, alive=True), attempt(run, alive=False)
    recently_lost = attempt(run, alive=False, expired_minutes=10)
    subagent = make_subagent(run)
    sub_live = attempt(run, alive=True, subagent=subagent)
    sub_ended = attempt(run, alive=False, subagent=subagent)
    root = attempts_root()
    names = {
        "live": attempt_dir_name(run.uid, None, live.number),
        "ended": attempt_dir_name(run.uid, None, ended.number),
        "recently_lost": attempt_dir_name(run.uid, None, recently_lost.number),
        "sub_live": attempt_dir_name(run.uid, subagent.uid, sub_live.number),
        "sub_ended": attempt_dir_name(run.uid, subagent.uid, sub_ended.number),
        # このDBにないRun（他のDBのものかもしれない）
        "unknown": attempt_dir_name("A" * 22, None, 1),
        "not-ours": "scratch",
    }
    for name in names.values():
        (root / name / "jobs").mkdir(parents=True)
        (root / name / "jobs" / "1.log").write_text("log")
    # symlinkは辿らない
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    (root / attempt_dir_name(run.uid, None, 99)).symlink_to(outside)

    assert run_cleanup(dry_run=True).workspaces == 2
    assert all((root / name).exists() for name in names.values())
    result = run_cleanup()
    assert (result.workspaces, result.failed) == (2, 0)
    left = {path.name for path in root.iterdir()}
    assert left == {names[k] for k in ("live", "recently_lost", "sub_live", "unknown", "not-ours")} | {
        attempt_dir_name(run.uid, None, 99)
    }
    assert (outside / "keep.txt").read_text() == "keep"


def test_workspaces_root_may_be_missing():
    assert not Path(settings.YORIKI_WORKSPACES_DIR).exists()
    assert run_cleanup().workspaces == 0


def test_unrecorded_objects_of_settled_runs_are_removed():
    settled, live, lost = make_run(), make_run(), make_run()
    first = attempt(settled, alive=False)
    attempt(live, alive=True)
    attempt(lost, alive=False, expired_minutes=10)
    recorded = put(f"snapshots/runs/{settled.uid}/1-aaaa.tar")
    Checkpoint.objects.create(
        run=settled, attempt=first, sequence=1, storage="test-main", key=recorded, size=1, sha256="0" * 64
    )
    copied = put(f"subagents/{settled.uid}/copy1.tar")
    make_subagent(settled, copy_storage="test-main", copy_key=copied)
    orphans = {put(f"snapshots/runs/{settled.uid}/2-bbbb.tar"), put(f"subagents/{settled.uid}/copy2.tar")}
    kept = {
        recorded,
        copied,
        # 動いているRun・leaseが切れて間もないRun（確定の途中かもしれない）のもの
        put(f"snapshots/runs/{live.uid}/1-cccc.tar"),
        put(f"snapshots/runs/{lost.uid}/1-dddd.tar"),
        # このDBにないRunのもの
        put(f"snapshots/runs/{'B' * 22}/1-eeee.tar"),
    }
    # 別の保存先に同じkeyの記録があっても、この保存先の記録ではない
    Checkpoint.objects.create(
        run=settled,
        attempt=first,
        sequence=2,
        storage="old-backend",
        key=f"snapshots/runs/{settled.uid}/2-bbbb.tar",
        size=1,
        sha256="0" * 64,
    )

    assert cleanup_objects(dry_run=True).objects == 2
    assert remaining_keys() == kept | orphans
    result = cleanup_objects()
    assert (result.objects, result.failed) == (2, 0)
    assert remaining_keys() == kept


def test_objects_are_skipped_when_the_storage_cannot_list():
    run = make_run()
    attempt(run, alive=False)
    key = put(f"snapshots/runs/{run.uid}/1-aaaa.tar")
    with mock.patch("artifact.storage.LocalStorage.list_keys", side_effect=StorageError("denied")):
        assert cleanup_objects().objects == 0
    assert remaining_keys() == {key}


def test_object_that_cannot_be_deleted_is_counted_as_failed():
    run = make_run()
    attempt(run, alive=False)
    put(f"snapshots/runs/{run.uid}/1-aaaa.tar")
    with mock.patch("artifact.storage.LocalStorage.delete", side_effect=StorageError("denied")):
        result = cleanup_objects()
    assert (result.objects, result.failed) == (0, 1)


def test_worker_command(capsys):
    run = make_run()
    ended = attempt(run, alive=False)
    workspace = attempts_root() / attempt_dir_name(run.uid, None, ended.number)
    workspace.mkdir(parents=True)
    with mock.patch("harness.cleanup.Launcher", side_effect=no_docker):
        call_command("cleanup_worker", "--dry-run")
        assert "作業領域: 1件が対象" in capsys.readouterr().out
        assert workspace.exists()
        call_command("cleanup_worker")
    assert "作業領域: 1件を消しました" in capsys.readouterr().out
    assert not workspace.exists()


def test_objects_command(capsys):
    run = make_run()
    attempt(run, alive=False)
    key = put(f"snapshots/runs/{run.uid}/1-aaaa.tar")
    call_command("cleanup_objects", "--dry-run")
    assert "保存先の実体: 1件が対象" in capsys.readouterr().out
    assert remaining_keys() == {key}
    call_command("cleanup_objects")
    assert "保存先の実体: 1件を消しました" in capsys.readouterr().out
    assert remaining_keys() == set()
