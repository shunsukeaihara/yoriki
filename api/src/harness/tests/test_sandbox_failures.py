"""Sandboxの障害と、Attemptをまたいだ再開の知らせのテスト（harness.md §4.3・§11.1）。Dockerは使わない"""

import functools
import time

import pytest

from conversation.dispatch import MessageMode, post_message
from conversation.factories import ConversationFactory
from harness.runner import Runner
from harness.sandbox import WORKSPACE_LOST, SandboxSession
from run.errors import LeaseLost
from run.models import Checkpoint, ExecutionAttempt, Run, RunEndReason, RunEvent, RunEventKind, RunStatus
from run.tasks import claim_run
from runtime.launcher import SandboxUnavailable
from runtime.storage import LocalSnapshotStorage

from .fakes import FakeAdapter, FakeSession, calls, reply

pytestmark = [pytest.mark.usefixtures("default_runtime"), pytest.mark.django_db]


@pytest.fixture(name="conversation")
def fixture_conversation(app_user):
    return ConversationFactory.create(owner=app_user)


def post(conversation, body="作業して") -> Run:
    post_message(conversation, body=body, client_message_id="", mode=MessageMode.AUTO, actor=conversation.owner)
    return Run.objects.filter(conversation=conversation).latest("pk")


def execute(run: Run, adapter: FakeAdapter, session_factory=FakeSession) -> Run:
    attempt = claim_run(run.pk)
    assert attempt is not None
    Runner(attempt, adapter_factory=adapter, sleep=lambda _s: None, session_factory=session_factory).run()
    run.refresh_from_db()
    return run


SHELL = {"description": "作る", "command": "echo 1 > a.txt"}


def test_lost_sandbox_is_retried_in_a_new_attempt(conversation, monkeypatch):
    enqueued: list[Run] = []
    monkeypatch.setattr("harness.runner.enqueue_run", enqueued.append)
    run = post(conversation)
    lost = functools.partial(FakeSession, error=SandboxUnavailable("lost"))
    run = execute(run, FakeAdapter([calls(("c1", "shell", SHELL))]), lost)

    assert run.status == RunStatus.QUEUED
    assert enqueued == [run]
    assert ExecutionAttempt.objects.get(run=run).failure == "sandbox: lost"

    # やり直しも続けて失われたら、上限で失敗にする
    for _attempt in range(2):
        run = execute(run, FakeAdapter([calls((f"c{_attempt + 2}", "shell", SHELL))]), lost)
    assert run.status == RunStatus.FAILED
    assert run.end_reason == RunEndReason.SANDBOX


def test_refused_sandbox_fails_the_run(conversation):
    run = post(conversation)
    refused = functools.partial(FakeSession, error=SandboxUnavailable("no image", retryable=False))
    run = execute(run, FakeAdapter([calls(("c1", "shell", SHELL))]), refused)
    assert run.status == RunStatus.FAILED
    assert run.end_reason == RunEndReason.SANDBOX


def test_interrupted_attempt_tells_the_model_what_was_not_kept(conversation):
    run = post(conversation)
    attempt = claim_run(run.pk)
    assert attempt is not None
    with run.fenced(attempt) as locked:
        # 前のAttemptでの確認待ちのCheckpoint（sequence 3まで反映）と、その後にファイルを変えたTool呼出し
        RunEvent.record(locked, RunEventKind.ASSISTANT_MESSAGE, {"text": "", "tool_calls": []})
        Checkpoint.objects.create(
            run=locked,
            attempt=attempt,
            sequence=3,
            storage="local:x",
            key="runs/x.tar",
            size=1,
            sha256="0" * 64,
            stopped_jobs=[{"job_id": "job_1", "command": "sleep 100"}],
        )
        tool_calls = [{"id": "c1", "name": "shell", "arguments": SHELL}, {"id": "c2", "name": "write", "arguments": {}}]
        RunEvent.record(locked, RunEventKind.ASSISTANT_MESSAGE, {"text": "", "tool_calls": tool_calls})
        RunEvent.record(
            locked,
            RunEventKind.TOOL_RESULT,
            {"call_id": "c1", "name": "shell", "content": [{"type": "text", "text": "Exit code: 0"}]},
        )
    attempt.end(failure="lease_expired")
    Run.objects.filter(pk=run.pk).update(status=RunStatus.QUEUED)

    adapter = FakeAdapter([reply("確かめます")])
    run = execute(run, adapter)
    texts = [m.text for m in adapter.requests[0].messages]
    reset = next(t for t in texts if "previous attempt was interrupted. The files" in t)
    # 結果のないまま閉じた write も含めて、反映されていない呼出しを挙げる
    assert "shell (c1)" in reset and "write (c2)" in reset
    assert any("job_1 (`sleep 100`)" in t for t in texts)

    # 次のAttemptでは同じ知らせを繰り返さない
    Run.objects.filter(pk=run.pk).update(status=RunStatus.QUEUED, finished=None, end_reason=None)
    adapter = FakeAdapter([reply("確かめます")])
    execute(run, adapter)
    texts = [m.text for m in adapter.requests[0].messages]
    assert sum("previous attempt was interrupted. The files" in t for t in texts) == 1
    assert sum("job_1" in t for t in texts) == 1


class _Storage(LocalSnapshotStorage):
    """どのkeyも見つからない、このWorkerの保存先"""

    def __init__(self):
        super().__init__("/nonexistent")

    @property
    def storage_id(self) -> str:
        return "local:here"

    def stat(self, key):
        return None


def test_snapshot_on_another_worker_is_reported(conversation, settings, tmp_path):
    """別のWorkerのローカルディスクに保存したsnapshotは戻せない。空のWorkspaceで続けて、そのことを伝える"""
    settings.YORIKI_WORKSPACES_DIR = str(tmp_path)
    first = post(conversation)
    attempt = claim_run(first.pk)
    assert attempt is not None
    Checkpoint.objects.create(
        run=first, attempt=attempt, sequence=1, storage="local:elsewhere", key="runs/x.tar", size=1, sha256="0" * 64
    )
    session = SandboxSession(first, attempt, storage_factory=_Storage)

    class Sandbox:
        def restore(self, src):
            raise AssertionError("must not restore")

    session._restore(Sandbox())  # type: ignore[arg-type]
    assert session.restored is None
    assert session.take_notice() == WORKSPACE_LOST
    assert session.take_notice() == ""


def test_long_sandbox_operations_keep_the_lease(conversation, monkeypatch, settings, tmp_path):
    """Workspaceの取り出し等の間も、leaseを延ばし続ける（Attemptのleaseより長くかかってもよい）"""
    settings.YORIKI_WORKSPACES_DIR = str(tmp_path)
    monkeypatch.setattr("harness.sandbox.HEARTBEAT_INTERVAL", 0.05)
    run = post(conversation)
    attempt = claim_run(run.pk)
    assert attempt is not None
    beats: list[int] = []
    session = SandboxSession(run, attempt, heartbeat=lambda: beats.append(1))

    def slow() -> str:
        time.sleep(0.3)
        return "done"

    assert session._keep_lease(slow) == "done"
    assert len(beats) >= 3

    def lost():
        raise LeaseLost("x")

    session = SandboxSession(run, attempt, heartbeat=lost)
    with pytest.raises(LeaseLost):
        session._keep_lease(lambda: time.sleep(0.3))
