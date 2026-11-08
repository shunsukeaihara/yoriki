"""サブエージェント（harness.md §10.11、design.md §5.3）のテスト。Dockerは使わない"""

import io
import tarfile
from datetime import timedelta
from typing import Any, cast

import pytest
from django.test import override_settings
from django.utils import timezone

from artifact.models import Artifact, ArtifactKind, BlobObject
from artifact.storage import get_storage
from conversation.dispatch import post_message
from conversation.factories import ConversationFactory
from harness.runner import Runner
from harness.sandbox import CollectedOutput, SandboxSession, collect_outputs, output_hashes
from harness.skills import loaded_entries
from harness.subagent_runner import SubagentRunner
from harness.tools.base import ToolContext
from harness.tools.submit_result import execute as submit_result
from llm.factories import LLMConnectionFactory, ModelProfileFactory
from llm.models import ProviderKind
from llm.providers.base import ToolCall
from run.models import (
    Checkpoint,
    ExecutionAttempt,
    Run,
    RunEvent,
    RunEventKind,
    RunStatus,
    Subagent,
    SubagentStatus,
    ToolInvocation,
    ToolStatus,
)
from run.tasks import _discard_subagent_copies, _recover, claim_run, claim_subagent
from skill.factories import SkillFactory, SkillVersionFactory
from usage.models import UsageLedger

from .fakes import FakeAdapter, FakeSession, calls, reply
from .test_waiting import JobSession, task_run

pytestmark = [pytest.mark.usefixtures("default_runtime"), pytest.mark.django_db]

START = ("s1", "subagent", {"description": "表を集計する", "prompt": "input の表を集計して、合計を報告して"})


class SubagentSession(FakeSession):
    """サブエージェントのSandboxSessionの代わり。返すファイル（`outputs`）を決められる"""

    def __init__(self, run, attempt, *, heartbeat=None, subagent=None, outputs=None, **kwargs):
        super().__init__(run, attempt, heartbeat=heartbeat, **kwargs)
        self.subagent = subagent
        self.outputs: list[CollectedOutput] = outputs or []
        self.suspends: list[bool] = []
        self.copies: list[str] = []

    def running_jobs(self, *, refresh: bool = False) -> list:
        return []

    def finished_unreported(self) -> list:
        return []

    def sync_inputs(self) -> None:
        pass

    def serve_script_requests(self, timeout: float = 0, *, limit: int = 8) -> None:
        pass

    def suspend(self, *, snapshot=True):
        self.suspends.append(snapshot)

    def copy_workspace(self, key: str):
        self.copies.append(key)
        stored = get_storage().put_immutable(key, io.BytesIO(_tar({"output/old.txt": b"old"})))
        return stored, get_storage().backend_id, {"old.txt": "x"}

    def collect_returned(self, baseline):
        return get_storage().backend_id, self.outputs


def _tar(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@pytest.fixture(name="conversation")
def fixture_conversation(app_user):
    return ConversationFactory.create(owner=app_user)


def post(conversation, body="作業して") -> Run:
    post_message(conversation, body=body, client_message_id="", actor=conversation.owner)
    return Run.objects.filter(conversation=conversation).latest("pk")


def run_parent(run: Run, adapter: FakeAdapter, session_factory: Any = SubagentSession) -> list[Any]:
    attempt = claim_run(run.pk)
    assert attempt is not None
    sessions: list[Any] = []

    def factory(*args, **kwargs):
        sessions.append(session_factory(*args, **kwargs))
        return sessions[-1]

    Runner(attempt, adapter_factory=adapter, sleep=lambda _s: None, session_factory=factory).run()
    run.refresh_from_db()
    return sessions


def run_child(
    subagent: Subagent, adapter: FakeAdapter, outputs: list[CollectedOutput] | None = None
) -> SubagentSession:
    attempt = claim_subagent(subagent.pk)
    assert attempt is not None
    sessions: list[SubagentSession] = []

    def factory(*args, **kwargs):
        sessions.append(SubagentSession(*args, outputs=outputs, **kwargs))
        return sessions[-1]

    SubagentRunner(attempt, adapter_factory=adapter, sleep=lambda _s: None, session_factory=factory).run()
    subagent.refresh_from_db()
    return sessions[0]


def start_and_wait(conversation) -> tuple[Run, Subagent]:
    """親が子を起動し、話し終えてWorkerを手放すまで"""
    run = post(conversation)
    run_parent(run, FakeAdapter([calls(START), reply("集計を頼みました。")]))
    return run, Subagent.objects.get(run=run)


def statuses(run: Run) -> list[int]:
    return [e.data["status"] for e in RunEvent.objects.filter(run=run, kind=RunEventKind.STATUS).order_by("sequence")]


def test_parent_starts_a_subagent_and_releases_the_worker_while_it_runs(conversation):
    run = post(conversation)
    adapter = FakeAdapter([calls(START), reply("集計を頼みました。")])
    sessions = run_parent(run, adapter)

    subagent = Subagent.objects.get(run=run)
    assert subagent.status == SubagentStatus.QUEUED
    assert subagent.description == "表を集計する"
    assert subagent.model_version_id == run.model_version_id
    assert subagent.copy_key == sessions[0].copies[0]
    assert subagent.baseline == {"old.txt": "x"}
    # 子の最初の発言は子の記録で、親の文脈には入らない
    first = RunEvent.objects.get(run=run, subagent=subagent)
    assert first.kind == RunEventKind.USER_MESSAGE
    assert "合計を報告して" in first.data["text"]
    assert not any("合計を報告して" in m.text for m in adapter.requests[1].messages)
    result = RunEvent.objects.get(run=run, subagent__isnull=True, kind=RunEventKind.TOOL_RESULT)
    assert subagent.uid in result.data["content"][0]["text"]
    # 子だけを待つので、保存してWorkerを手放す
    assert run.status == RunStatus.WAITING_BACKGROUND
    assert sessions[0].suspends == [True]
    assert not run.has_live_attempt()


def test_finished_subagent_wakes_the_parent_with_its_report_and_files(conversation):
    run, subagent = start_and_wait(conversation)
    blob = get_storage().put_immutable("blobs/table.csv", io.BytesIO(b"a,b\n1,2\n"))
    child_adapter = FakeAdapter([reply("合計は 3 です。")])
    run_child(subagent, child_adapter, outputs=[CollectedOutput("table.csv", blob)])

    assert subagent.status == SubagentStatus.COMPLETED
    assert subagent.result_text == "合計は 3 です。"
    assert [f["path"] for f in subagent.result_files] == ["table.csv"]
    artifact = run.artifacts.get(kind=ArtifactKind.SUBAGENT_RESULT)
    assert artifact.path == f"subagents/{subagent.uid}/table.csv"
    # 子の文脈は子の記録だけ。親の発言は見えない
    assert not any("作業して" in m.text for m in child_adapter.requests[0].messages)
    assert "subagent" in child_adapter.requests[0].messages[0].text
    assert "subagent" not in {t.name for t in child_adapter.requests[0].tools}
    # 子の利用量とステップは子のもの。親のステップ数は増えない
    assert UsageLedger.objects.filter(run=run, subagent=subagent).count() == 1
    run.refresh_from_db()
    assert run.step_count == 2
    assert subagent.step_count == 1
    # 一時のコピーは消す
    assert subagent.copy_key == ""
    # 手放していた親は、順番待ちに戻って起こされる
    assert run.status == RunStatus.QUEUED

    parent_adapter = FakeAdapter([reply("合計は 3 でした。")])
    run_parent(run, parent_adapter)
    assert run.status == RunStatus.COMPLETED
    finished = RunEvent.objects.get(run=run, kind=RunEventKind.SUBAGENT_FINISHED)
    assert finished.subagent is None
    text = finished.data["text"]
    assert "<untrusted_content" in text and "合計は 3 です。" in text
    assert f"/workspace/input/subagents/{subagent.uid}/" in text
    assert any("合計は 3 です。" in m.text for m in parent_adapter.requests[0].messages)
    # 子の記録は親の文脈に入らない
    assert not any("合計を報告して" in m.text for m in parent_adapter.requests[0].messages)


def test_subagent_finishing_while_the_parent_runs_is_reported_at_the_next_step(conversation):
    run = post(conversation)
    attempt = claim_run(run.pk)
    assert attempt is not None
    child: dict[str, Any] = {}

    class Adapter(FakeAdapter):
        def stream(self, request):
            # 2回目の呼出しの前に、子を動かして終わらせる
            if len(self.requests) == 1:
                child["session"] = run_child(Subagent.objects.get(run=run), FakeAdapter([reply("できました。")]))
            yield from super().stream(request)

    adapter = Adapter([calls(START), calls(("r1", "todo_write", {"todos": []})), reply("まとめました。")])
    Runner(attempt, adapter_factory=adapter, sleep=lambda _s: None, session_factory=cast(Any, SubagentSession)).run()
    run.refresh_from_db()

    assert run.status == RunStatus.COMPLETED
    assert any("できました。" in m.text for m in adapter.requests[2].messages)
    # 親が動いている間は順番待ちに戻さない（同じAttemptのまま）
    assert run.attempts.filter(subagent__isnull=True).count() == 1
    assert RunStatus.QUEUED not in statuses(run)[1:]


def test_child_attempts_are_numbered_apart_from_the_parent(conversation):
    run, subagent = start_and_wait(conversation)
    child_attempt = claim_subagent(subagent.pk)
    assert child_attempt is not None
    run.refresh_from_db()
    assert child_attempt.holds(run)
    # 子が動いていても、親は新しいAttemptを作れて、子のAttemptを終わらせない
    Run.objects.filter(pk=run.pk).update(status=RunStatus.QUEUED)
    parent_attempt = claim_run(run.pk)
    assert parent_attempt is not None
    assert parent_attempt.number == 2
    run.refresh_from_db()
    assert parent_attempt.holds(run)
    assert child_attempt.holds(run)
    child_attempt.refresh_from_db()
    assert child_attempt.ended is None


def test_concurrent_subagents_are_limited(conversation):
    run = post(conversation)
    starts = [(f"s{i}", "subagent", {"description": f"作業{i}", "prompt": "調べて"}) for i in range(3)]
    with override_settings(YORIKI_SUBAGENTS_PER_RUN=2):
        run_parent(run, FakeAdapter([calls(*starts[:1]), calls(*starts[1:2]), calls(*starts[2:]), reply("待つ")]))
    assert Subagent.objects.filter(run=run).count() == 2
    rejected = ToolInvocation.objects.get(run=run, call_id="s2")
    assert rejected.status == ToolStatus.FAILED
    result = RunEvent.objects.get(run=run, kind=RunEventKind.TOOL_RESULT, data__call_id="s2")
    assert result.data["is_error"]


def test_parent_can_stop_a_subagent_and_it_is_not_reported(conversation):
    run = post(conversation)
    kill_args = {"id": ""}
    kill = ("k1", "subagent_kill", kill_args)

    class Adapter(FakeAdapter):
        def stream(self, request):
            if len(self.requests) == 1:
                # 2回目の応答で、起動した子のIDを指定して止める
                kill_args["id"] = Subagent.objects.get(run=run).uid
            yield from super().stream(request)

    adapter = Adapter([calls(START), calls(kill), reply("やめました。")])
    run_parent(run, adapter)

    subagent = Subagent.objects.get(run=run)
    assert subagent.status == SubagentStatus.CANCELLED
    assert subagent.reported
    assert run.status == RunStatus.COMPLETED
    assert not RunEvent.objects.filter(run=run, kind=RunEventKind.SUBAGENT_FINISHED).exists()
    # 動き出す前に止めたので、Workerはclaimしない
    assert claim_subagent(subagent.pk) is None


def test_stopped_subagent_stops_at_its_next_step_without_waking_the_parent(conversation):
    run, subagent = start_and_wait(conversation)

    class Adapter(FakeAdapter):
        def stream(self, request):
            # 子の1回目の呼出しの間に、親が止める
            Subagent.objects.filter(pk=subagent.pk).update(status=SubagentStatus.CANCELLED, reported=True)
            yield from super().stream(request)

    run_child(subagent, Adapter([calls(("r1", "todo_write", {"todos": []}))]))
    assert subagent.status == SubagentStatus.CANCELLED
    run.refresh_from_db()
    assert run.status == RunStatus.WAITING_BACKGROUND
    # 止めたので、子は呼出しの結果を記録せず、Workerを空ける
    assert not RunEvent.objects.filter(run=run, subagent=subagent, kind=RunEventKind.TOOL_RESULT).exists()
    assert not subagent.attempts.filter(ended__isnull=True).exists()


def test_cancel_waits_for_the_running_subagent(conversation, app_user):
    run, subagent = start_and_wait(conversation)
    child_attempt = claim_subagent(subagent.pk)
    assert child_attempt is not None
    run.refresh_from_db()

    run.request_cancel("停止", app_user)
    run.refresh_from_db()
    # 親はWorkerを手放しているが、子が動いているので停止中にする
    assert run.status == RunStatus.CANCELLING
    assert not child_attempt.holds(run)

    SubagentRunner(
        child_attempt,
        adapter_factory=FakeAdapter([]),
        sleep=lambda _s: None,
        session_factory=cast(Any, SubagentSession),
    ).run()
    run.refresh_from_db()
    subagent.refresh_from_db()
    assert subagent.status == SubagentStatus.CANCELLED
    assert run.status == RunStatus.CANCELLED


def test_cancel_with_only_a_queued_subagent_finishes_at_once(conversation, app_user):
    run, subagent = start_and_wait(conversation)
    run.request_cancel("停止", app_user)
    run.refresh_from_db()
    subagent.refresh_from_db()
    assert run.status == RunStatus.CANCELLED
    assert subagent.status == SubagentStatus.CANCELLED
    assert claim_subagent(subagent.pk) is None


def test_message_wakes_the_parent_waiting_only_for_subagents(conversation):
    run, _subagent = start_and_wait(conversation)
    post_message(conversation, body="追加で確認して", client_message_id="", actor=conversation.owner)
    run.refresh_from_db()
    assert run.status == RunStatus.QUEUED


def test_lost_child_worker_fails_the_subagent_and_wakes_the_parent(conversation):
    run, subagent = start_and_wait(conversation)
    child_attempt = claim_subagent(subagent.pk)
    assert child_attempt is not None
    ExecutionAttempt.objects.filter(pk=child_attempt.pk).update(lease_expires=timezone.now() - timedelta(seconds=1))

    _recover(child_attempt.pk)
    subagent.refresh_from_db()
    run.refresh_from_db()
    assert subagent.status == SubagentStatus.FAILED
    assert "lost" in subagent.error
    # 子は再試行しない
    assert subagent.attempts.count() == 1
    assert run.status == RunStatus.QUEUED

    _discard_subagent_copies()
    subagent.refresh_from_db()
    assert subagent.copy_key == ""


def test_subagent_with_a_cloud_model_stops_when_the_conversation_becomes_confidential(conversation):
    run, subagent = start_and_wait(conversation)
    assert not subagent.model_version.is_local
    Run.objects.filter(pk=run.pk).update(handles_sensitive_data=True)
    run_child(subagent, FakeAdapter([]))
    assert subagent.status == SubagentStatus.CANCELLED
    assert "confidential" in subagent.error
    run.refresh_from_db()
    assert run.status == RunStatus.QUEUED


def test_subagent_time_limit(conversation):
    _run, subagent = start_and_wait(conversation)

    class Adapter(FakeAdapter):
        def stream(self, request):
            Subagent.objects.filter(pk=subagent.pk).update(started=timezone.now() - timedelta(hours=1))
            yield from super().stream(request)

    run_child(subagent, Adapter([calls(("r1", "todo_write", {"todos": []}))]))
    assert subagent.status == SubagentStatus.FAILED
    assert "time limit" in subagent.error


def test_model_for_a_subagent_is_checked(conversation):
    run = post(conversation)
    local = ModelProfileFactory.create(
        name="社内モデル",
        version__connection=LLMConnectionFactory.create(kind=ProviderKind.LOCAL),
        version__supports_tools=True,
    )
    start = ("s1", "subagent", {"description": "調べる", "prompt": "調べて", "model": "社内モデル"})
    unknown = ("s2", "subagent", {"description": "調べる", "prompt": "調べて", "model": "ないモデル"})
    run_parent(run, FakeAdapter([calls(start), calls(unknown), reply("待つ")]))
    subagent = Subagent.objects.get(run=run)
    assert subagent.model_version_id == local.current_version_id
    result = RunEvent.objects.get(run=run, kind=RunEventKind.TOOL_RESULT, data__call_id="s2")
    assert result.data["is_error"]
    assert "社内モデル" in result.data["content"][0]["text"]


def test_task_runs_cannot_choose_another_model_and_cannot_submit_while_subagents_run():
    run = task_run()
    ModelProfileFactory.create(name="別のモデル", version__supports_tools=True)
    attempt = claim_run(run.pk)
    assert attempt is not None
    session = SubagentSession(run, attempt)
    other = ("s1", "subagent", {"description": "調べる", "prompt": "調べて", "model": "別のモデル"})
    from harness.tools.subagent import execute_subagent  # noqa: PLC0415

    context = ToolContext(run, attempt, ToolCall(*other), session=cast(Any, session))
    assert execute_subagent(other[2], context).is_error
    ok = ToolContext(run, attempt, ToolCall(*START), session=cast(Any, session))
    assert not execute_subagent(START[2], ok).is_error

    result = submit_result({"a": 1}, ok, schema={"type": "object"})
    assert result.is_error
    assert "subagent" in str(result.content[0])


def test_task_run_waiting_for_subagents_saves_the_workspace():
    run = task_run()
    assert not run.is_interactive
    sessions = run_parent(run, FakeAdapter([calls(START), reply("待ちます。"), reply("待ちます。")]))
    assert run.status == RunStatus.WAITING_BACKGROUND
    # 答える人がいないRunでも、親が続きから動けるよう保存する（design.md §7.9）
    assert sessions[0].suspends == [True]


def test_jobs_and_subagents_wait_together(conversation):
    run = post(conversation)

    def factory(run_obj, attempt_obj, **kwargs):
        session = JobSession(run_obj, attempt_obj, tick=lambda s: s.finish(), **kwargs)
        session.copy_workspace = lambda key: (None, get_storage().backend_id, {})  # type: ignore[attr-defined]
        return session

    run_parent(run, FakeAdapter([calls(START), reply("待ちます。"), reply("ジョブは終わりました。")]), factory)
    # ジョブが動いている間は手放さずに見張り、ジョブが終わって子だけになったら手放す
    assert run.status == RunStatus.WAITING_BACKGROUND
    assert not run.has_live_attempt()
    assert statuses(run)[-3:] == [RunStatus.WAITING_BACKGROUND, RunStatus.RUNNING, RunStatus.WAITING_BACKGROUND]
    assert Checkpoint.objects.filter(run=run).count() == 0  # 偽のSandboxは保存しない


def test_unseen_subagent_results_reach_the_next_run(conversation, app_user):
    run, subagent = start_and_wait(conversation)
    run_child(subagent, FakeAdapter([reply("報告です。")]))
    run.refresh_from_db()
    assert run.status == RunStatus.QUEUED
    # 親が受け取る前に止めた
    run.request_cancel("停止", app_user)
    finished = RunEvent.objects.get(run=run, kind=RunEventKind.SUBAGENT_FINISHED)
    assert "報告です。" in finished.data["text"]


def test_running_subagents_are_stopped_when_the_run_ends(conversation):
    run, subagent = start_and_wait(conversation)
    assert claim_subagent(subagent.pk) is not None
    run.refresh_from_db()
    run.set_status(RunStatus.FAILED, error="失敗")
    subagent.refresh_from_db()
    assert subagent.status == SubagentStatus.CANCELLED
    notice = RunEvent.objects.filter(run=run, kind=RunEventKind.CONTEXT).latest("sequence")
    assert subagent.uid in notice.data["text"]


def test_only_new_or_changed_output_files_are_returned():
    archive = io.BytesIO(
        _tar({"output/same.txt": b"same", "output/changed.txt": b"new", "output/new.txt": b"n", "work/x": b"w"})
    )
    baseline = output_hashes(io.BytesIO(_tar({"output/same.txt": b"same", "output/changed.txt": b"old"})))
    collected = collect_outputs(archive, get_storage(), baseline)
    assert sorted(c.path for c in collected) == ["changed.txt", "new.txt"]


def test_skill_loaded_by_a_subagent_is_not_loaded_for_the_parent(conversation, app_user):
    skill = SkillFactory.create(name="請求書の照合", created_by=app_user)
    SkillVersionFactory.create(skill=skill, instructions="1. 突き合わせる")
    run, subagent = start_and_wait(conversation)
    child = FakeAdapter([calls(("c1", "skill", {"name": "請求書の照合"})), reply("照合しました。")])
    run_child(subagent, child)

    assert subagent.status == SubagentStatus.COMPLETED
    assert (
        "1. 突き合わせる"
        in RunEvent.objects.get(run=run, subagent=subagent, data__call_id="c1").data["content"][0]["text"]
    )
    assert [e["skill"] for e in subagent.skills] == [skill.uid]
    run.refresh_from_db()
    assert not [e for e in run.manifest.get("appended", []) if e["kind"] == "skill"]
    assert loaded_entries(run) == []


def test_connector_results_of_a_subagent_are_not_placed_for_the_parent(conversation, settings, tmp_path):
    settings.YORIKI_WORKSPACES_DIR = str(tmp_path)
    run, subagent = start_and_wait(conversation)
    blob = BlobObject.put(io.BytesIO(b"a\n1\n"))
    Artifact.create_connector_result(run, "connectors/p/result.csv", "p.csv", blob)
    Artifact.create_connector_result(run, "connectors/c/result.csv", "c.csv", blob, subagent=subagent)
    parent_attempt = ExecutionAttempt.start(run)
    parent = SandboxSession(run, parent_attempt)
    parent.sync_inputs()
    assert (parent.input_dir / "connectors/p/result.csv").exists()
    assert not (parent.input_dir / "connectors/c").exists()

    child_attempt = claim_subagent(subagent.pk)
    assert child_attempt is not None
    child = SandboxSession(run, child_attempt, subagent=subagent)
    child.sync_inputs()
    assert (child.input_dir / "connectors/p/result.csv").exists()
    assert (child.input_dir / "connectors/c/result.csv").exists()


def test_time_limit_is_checked_while_the_model_answers(conversation, monkeypatch):
    _run, subagent = start_and_wait(conversation)
    # 区切りでは上限の前で、応答を受け取り始めた後に上限を超える
    answers = iter([False])
    monkeypatch.setattr(SubagentRunner, "_timed_out", lambda self: next(answers, True))

    run_child(subagent, FakeAdapter([reply("まだ途中です。")]))
    assert subagent.status == SubagentStatus.FAILED
    assert "time limit" in subagent.error
    assert subagent.result_text == ""
