"""Sandboxの障害と、Attemptをまたいだ再開の知らせのテスト（harness.md §4.3・§11.1）。Dockerは使わない"""

import functools
import io
import time

import pytest

from artifact.storage import LocalStorage
from conversation.dispatch import MessageMode, post_message
from conversation.factories import ConversationFactory
from harness.runner import Runner
from harness.sandbox import WORKSPACE_LOST, Job, SandboxSession
from harness.tools import shell as shell_tool
from harness.tools.base import ToolContext
from harness.tools.shell import _job_text
from llm.providers.base import ToolCall
from run.errors import LeaseLost
from run.models import Checkpoint, ExecutionAttempt, Run, RunEndReason, RunEvent, RunEventKind, RunStatus
from run.tasks import claim_run
from runtime.launcher import Exec, SandboxUnavailable

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


def test_out_of_memory_is_told_to_the_model(conversation, monkeypatch):
    """メモリの上限を超えてSandboxごと止まったら（gVisor）、次のAttemptでモデルに知らせる"""
    monkeypatch.setattr("harness.runner.enqueue_run", lambda _run: None)
    run = post(conversation)
    oom = functools.partial(FakeSession, error=SandboxUnavailable("lost"), oom=True)
    run = execute(run, FakeAdapter([calls(("c1", "shell", SHELL))]), oom)
    assert run.status == RunStatus.QUEUED
    assert ExecutionAttempt.objects.get(run=run).failure == "sandbox: out of memory: lost"

    adapter = FakeAdapter([reply("小さく分けて処理します")])
    run = execute(run, adapter)
    assert run.status == RunStatus.COMPLETED
    assert run.runtime_version is not None
    memory_mb = run.runtime_version.memory_mb
    texts = [m.text for m in adapter.requests[0].messages]
    assert any(f"more memory than its limit ({memory_mb} MB)" in t for t in texts)

    # 繰り返して上限に達したら、メモリの超過と分かる理由で失敗にする
    run = post(conversation, "もう一度")
    for number in range(3):
        run = execute(run, FakeAdapter([calls((f"d{number}", "shell", SHELL))]), oom)
    assert run.status == RunStatus.FAILED
    assert "メモリの上限" in run.error


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
        tool_calls = [
            {"id": "c0", "name": "read", "arguments": {"path": "/workspace/input/a.pdf"}},
            {"id": "c1", "name": "shell", "arguments": SHELL},
            {"id": "c2", "name": "write", "arguments": {}},
        ]
        RunEvent.record(locked, RunEventKind.ASSISTANT_MESSAGE, {"text": "", "tool_calls": tool_calls})
        RunEvent.record(
            locked,
            RunEventKind.TOOL_RESULT,
            {
                "call_id": "c0",
                "name": "read",
                "content": [{"type": "text", "text": "…"}],
                "meta": {"spilled": ["/workspace/work/.yoriki/outputs/i0.txt"]},
            },
        )
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
    assert "/workspace/work/.yoriki/outputs/i0.txt" in reset
    assert any("job_1 (`sleep 100`)" in t for t in texts)

    # 次のAttemptでは同じ知らせを繰り返さない
    Run.objects.filter(pk=run.pk).update(status=RunStatus.QUEUED, finished=None, end_reason=None)
    adapter = FakeAdapter([reply("確かめます")])
    execute(run, adapter)
    texts = [m.text for m in adapter.requests[0].messages]
    assert sum("previous attempt was interrupted. The files" in t for t in texts) == 1
    assert sum("job_1" in t for t in texts) == 1


def test_snapshot_on_another_worker_is_reported(conversation, settings, tmp_path):
    """このWorkerから読めない保存先（設定にない保存先、別のWorkerのローカルディスク）のsnapshotは戻せない。
    空のWorkspaceで続けて、そのことを伝える"""
    settings.YORIKI_WORKSPACES_DIR = str(tmp_path)
    first = post(conversation)
    attempt = claim_run(first.pk)
    assert attempt is not None
    Checkpoint.objects.create(
        run=first, attempt=attempt, sequence=1, storage="elsewhere", key="runs/x.tar", size=1, sha256="0" * 64
    )
    session = SandboxSession(first, attempt)

    class Sandbox:
        def restore(self, src):
            raise AssertionError("must not restore")

    session._restore(Sandbox())  # type: ignore[arg-type]
    assert session.restored is None
    assert session.take_notice() == WORKSPACE_LOST
    assert session.take_notice() == ""


def test_snapshot_is_read_from_the_recorded_backend(conversation, settings, tmp_path):
    """既定の保存先を変えた後も、以前の保存先に置いたsnapshotから戻す（design.md §7.10）"""
    settings.YORIKI_WORKSPACES_DIR = str(tmp_path)
    settings.YORIKI_STORAGE["BACKENDS"]["old"] = {
        "BACKEND": "artifact.storage.LocalStorage",
        "OPTIONS": {"root": str(tmp_path / "old")},
    }
    stored = LocalStorage("old", root=str(tmp_path / "old")).put_immutable("snapshots/x.tar", io.BytesIO(b"tar"))
    first = post(conversation)
    attempt = claim_run(first.pk)
    assert attempt is not None
    checkpoint = Checkpoint.objects.create(
        run=first, attempt=attempt, sequence=1, storage="old", key=stored.key, size=stored.size, sha256=stored.sha256
    )
    restored: list[bytes] = []

    class Sandbox:
        def restore(self, src):
            restored.append(src.read())

    session = SandboxSession(first, attempt)
    session._restore(Sandbox())  # type: ignore[arg-type]
    assert restored == [b"tar"]
    assert session.restored == checkpoint
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


def test_started_sandbox_is_removed_when_the_lease_is_lost(conversation, monkeypatch, settings, tmp_path):
    settings.YORIKI_WORKSPACES_DIR = str(tmp_path)
    monkeypatch.setattr("harness.sandbox.HEARTBEAT_INTERVAL", 0.05)
    run = post(conversation)
    attempt = claim_run(run.pk)
    assert attempt is not None

    def lost():
        raise LeaseLost("x")

    removed: list[str] = []

    def start() -> str:
        time.sleep(0.2)
        return "sandbox"

    session = SandboxSession(run, attempt, heartbeat=lost)
    with pytest.raises(LeaseLost):
        session._keep_lease(start, on_abandon=removed.append)
    assert removed == ["sandbox"]


def test_job_output_is_read_only_up_to_the_measured_end(tmp_path):
    """動いているジョブの出力は読む間も増える。測った位置より先は、次の読み出しで返す"""
    path = tmp_path / "job.log"
    path.write_bytes(b"first\nsecond\n")
    job = Job("job_1", "cmd", "説明", "/workspace/work", Exec("e1"), path, 60)
    assert _job_text(job, 0, 6) == "first\n"
    assert _job_text(job, 6, path.stat().st_size) == "second\n"


class _ExitedSandbox:
    """コマンドは終わったが、出力をまだ受け取っている"""

    def exec_state(self, exec_id):
        return False, 0


def test_job_is_finished_only_after_its_output_is_collected(conversation, monkeypatch, settings, tmp_path):
    settings.YORIKI_WORKSPACES_DIR = str(tmp_path)
    run = post(conversation)
    attempt = claim_run(run.pk)
    assert attempt is not None
    session = SandboxSession(run, attempt)
    session.sandbox = _ExitedSandbox()  # type: ignore[assignment]
    job = Job("job_1", "cmd", "説明", "/workspace/work", Exec("e1"), tmp_path / "job.log", 60)
    assert session.refresh(job) is False
    job.execution.done.set()
    assert session.refresh(job) is True
    assert (job.finished, job.exit_code) == (True, 0)


def test_running_jobs_reserve_their_output_limit(conversation, monkeypatch, settings, tmp_path):
    settings.YORIKI_WORKSPACES_DIR = str(tmp_path)
    monkeypatch.setattr("harness.sandbox.JOB_OUTPUT_LIMIT", 60)
    monkeypatch.setattr("harness.sandbox.ATTEMPT_OUTPUT_LIMIT", 100)
    run = post(conversation)
    attempt = claim_run(run.pk)
    assert attempt is not None
    limits: list[int] = []

    class _Sandbox:
        def start_exec(self, request, output, *, output_limit):
            limits.append(output_limit)
            output.close()
            execution = Exec(f"e{len(limits)}")
            execution.started.set()
            return execution

    session = SandboxSession(run, attempt)
    session.sandbox = _Sandbox()  # type: ignore[assignment]
    for _n in range(3):
        session.start_job("sleep 10", "/workspace/work", "説明")
    # 動いているジョブの上限の合計が、Attemptの上限を超えない
    assert limits == [60, 40, 0]


def test_job_without_a_process_group_is_not_reported_as_stopped(conversation, monkeypatch, settings, tmp_path):
    settings.YORIKI_WORKSPACES_DIR = str(tmp_path)
    monkeypatch.setattr("harness.sandbox.KILL_GRACE", 0.05)
    run = post(conversation)
    attempt = claim_run(run.pk)
    assert attempt is not None

    class _RunningSandbox:
        def exec_state(self, exec_id):
            return True, None

    session = SandboxSession(run, attempt)
    session.sandbox = _RunningSandbox()  # type: ignore[assignment]
    job = Job("job_1", "cmd", "説明", "/workspace/work", Exec("e1"), tmp_path / "job.log", 60)
    assert session.kill(job) is False
    assert not job.finished


def test_checkpoint_is_found_past_requests_without_one(conversation, app_user):
    first = post(conversation)
    attempt = claim_run(first.pk)
    assert attempt is not None
    checkpoint = Checkpoint.objects.create(
        run=first, attempt=attempt, sequence=1, storage="local:x", key="runs/a.tar", size=1, sha256="0" * 64
    )
    previous = first
    for _n in range(3):
        Run.objects.filter(pk=previous.pk).update(status=RunStatus.COMPLETED)
        previous = post(conversation, "次")
    assert previous.pk != first.pk
    assert Checkpoint.latest_for(previous) == checkpoint
    # 別の会話のCheckpointは使わない
    other = post(ConversationFactory.create(owner=app_user))
    assert Checkpoint.latest_for(other) is None


def test_read_offset_is_kept_when_the_output_cannot_be_saved(conversation, settings, tmp_path):
    settings.YORIKI_WORKSPACES_DIR = str(tmp_path)
    run = post(conversation)
    attempt = claim_run(run.pk)
    assert attempt is not None
    path = tmp_path / "job.log"
    path.write_text("x" * 200_000)
    job = Job("job_1", "cmd", "説明", "/workspace/work", Exec("e1"), path, 60)

    class _Session(SandboxSession):
        def put_job_output(self, job, path, start, end):
            return False

    session = _Session(run, attempt)
    context = ToolContext(run, attempt, ToolCall("c1", "shell", {}), invocation="i1", session=session)
    text = shell_tool._output(context, job)
    assert "could not be saved" in text
    assert job.read_offset == 0
