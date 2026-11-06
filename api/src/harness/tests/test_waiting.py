"""親が動き終えた後、ジョブが動いている間の待ち（design.md §5.3、harness.md §8.3）のテスト。Dockerは使わない"""

from collections.abc import Callable
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from django.utils import timezone

from conversation.dispatch import MessageMode, post_message
from conversation.factories import ConversationFactory
from conversation.models import ConversationMessage, MessageRole
from harness.runner import ATTEMPT_LIMIT_SECONDS, MAX_STEPS, RUNNING_JOBS_NOTICE, Runner
from harness.sandbox import Suspended
from harness.tools.base import ToolContext
from harness.tools.submit_result import execute as submit_result
from harness.tools.submit_result import make_submit_result_tool
from llm.providers.base import ToolCall
from run.factories import ClarificationRequestFactory
from run.models import (
    Checkpoint,
    ClarificationRequest,
    ClarificationStatus,
    ExecutionAttempt,
    Run,
    RunEndReason,
    RunEvent,
    RunEventKind,
    RunLaunch,
    RunStatus,
)
from run.tasks import _expire_clarifications, _recover, claim_run
from runtime.launcher import SandboxUnavailable
from skill.factories import SkillFactory, SkillVersionFactory
from skill.models import SkillScope
from task.dispatch import start_task_run
from task.factories import TaskDefinitionFactory
from task.models import TaskScope
from user.factories import UserFactory, grant
from utils.permissions import AppPermission

from .fakes import FakeAdapter, FakeSession, calls, reply

pytestmark = [pytest.mark.usefixtures("default_runtime"), pytest.mark.django_db]

QUESTION = {
    "questions": [
        {"header": "形式", "question": "どちらで出しますか", "options": [{"label": "CSV"}, {"label": "Excel"}]}
    ]
}
ANSWER = [{"selected": ["CSV"], "text": ""}]


class JobSession(FakeSession):
    """ジョブが動いているSandboxSession。待つ間の1回ごとに `tick` を呼ぶ"""

    def __init__(self, run, attempt, *, tick: Callable[["JobSession"], None], **kwargs):
        super().__init__(run, attempt, **kwargs)
        self.tick = tick
        self.job = SimpleNamespace(
            job_id="job_1",
            description="変換",
            exit_code=None,
            stopped="",
            finished=False,
            reported=False,
            timeout_seconds=1800,
        )
        self.jobs = {"job_1": self.job}
        self.suspends: list[bool] = []
        self.synced = 0
        self.attempt_ended_at_close: bool | None = None

    @property
    def started(self) -> bool:
        return True

    def running_jobs(self, *, refresh: bool = False) -> list:
        return [] if self.job.finished else [self.job]

    def finished_unreported(self) -> list:
        return [self.job] if self.job.finished and not self.job.reported else []

    def serve_script_requests(self, timeout: float = 0, *, limit: int = 8) -> None:
        if timeout:
            self.tick(self)

    def sync_inputs(self) -> None:
        self.synced += 1

    def suspend(self, *, snapshot=True):
        self.suspends.append(snapshot)
        self.job.finished = True
        self.job.stopped = self.job.stopped or "suspended"

    def close(self) -> None:
        self.attempt_ended_at_close = ExecutionAttempt.objects.get(pk=self.attempt.pk).ended is not None

    def finish(self) -> None:
        self.job.finished, self.job.exit_code = True, 0


@pytest.fixture(name="conversation")
def fixture_conversation(app_user):
    return ConversationFactory.create(owner=app_user)


def post(conversation, body="作業して") -> Run:
    post_message(conversation, body=body, client_message_id="", mode=MessageMode.AUTO, actor=conversation.owner)
    return Run.objects.filter(conversation=conversation).latest("pk")


def run_with(run: Run, adapter: FakeAdapter, session_factory: Any) -> None:
    attempt = claim_run(run.pk)
    assert attempt is not None
    Runner(attempt, adapter_factory=adapter, sleep=lambda _s: None, session_factory=session_factory).run()
    run.refresh_from_db()


def execute(run: Run, adapter: FakeAdapter, tick=lambda _s: None) -> tuple[Run, JobSession]:
    sessions: list[JobSession] = []

    def factory(run_obj, attempt_obj, **kwargs):
        sessions.append(JobSession(run_obj, attempt_obj, tick=tick, **kwargs))
        return sessions[0]

    run_with(run, adapter, factory)
    return run, sessions[0]


def task_run() -> Run:
    skill = SkillFactory.create(scope=SkillScope.SYSTEM, name="確認", owner=None)
    SkillVersionFactory.create(skill=skill, tools=["shell"])
    admin = grant(UserFactory.create(), AppPermission.MANAGE_SYSTEM_TASKS)
    task = TaskDefinitionFactory.create(
        scope=TaskScope.SYSTEM, created_by=admin, revision__skills=[{"skill": skill.uid, "version": 1}]
    )
    return start_task_run(task, inputs={}, actor=admin, launch=RunLaunch.MANUAL)


def statuses(run: Run) -> list[int]:
    return [e.data["status"] for e in RunEvent.objects.filter(run=run, kind=RunEventKind.STATUS).order_by("sequence")]


def agent_messages(run: Run) -> list[str]:
    rows = ConversationMessage.objects.filter(run=run, role=MessageRole.AGENT).order_by("pk")
    return [m.body for m in rows]


def test_reply_waits_for_the_job_and_the_finished_job_wakes_the_parent(conversation):
    run = post(conversation)
    seen = []

    def tick(session):
        run.refresh_from_db()
        seen.append(run.status)
        session.finish()

    adapter = FakeAdapter([reply("始めました。"), reply("終わりました。")])
    run, session = execute(run, adapter, tick)

    assert seen == [RunStatus.WAITING_BACKGROUND]
    assert run.status == RunStatus.COMPLETED
    assert statuses(run)[-3:] == [RunStatus.WAITING_BACKGROUND, RunStatus.RUNNING, RunStatus.COMPLETED]
    # 同じAttemptのまま。話し終えただけではジョブを止めない（会話では知らせずに待つ）
    assert run.attempts.count() == 1
    assert session.suspends == [True]
    assert agent_messages(run) == ["始めました。", "終わりました。"]
    assert not any("still running" in m.text for m in adapter.requests[1].messages)
    assert any("finished with exit code 0" in m.text for m in adapter.requests[1].messages)
    assert RunEvent.objects.filter(run=run, kind=RunEventKind.JOB_FINISHED).count() == 1


def test_message_wakes_the_parent_while_the_job_keeps_running(conversation, app_user):
    run = post(conversation)
    sent: list[bool] = []

    def tick(session):
        if not sent:
            sent.append(True)
            post_message(
                conversation, body="急いで", client_message_id="", mode=MessageMode.AUTO, actor=conversation.owner
            )
        else:
            session.finish()

    adapter = FakeAdapter([reply("始めました。"), reply("急ぎます。"), reply("終わりました。")])
    run, session = execute(run, adapter, tick)

    assert run.status == RunStatus.COMPLETED
    assert run.attempts.count() == 1
    assert any("急いで" in m.text for m in adapter.requests[1].messages)
    # 2つ目の応答でもジョブが動いているので、また待つ
    assert statuses(run).count(RunStatus.WAITING_BACKGROUND) == 2
    assert session.suspends == [True]


def test_message_before_the_reply_ends_does_not_stop_the_job(conversation):
    """親が応答を返す間に届いた発言では、保存もジョブの停止もしない"""
    run = post(conversation)

    class PostsWhileReplying(FakeAdapter):
        def stream(self, request):
            if not self.requests:
                post_message(
                    conversation, body="追加", client_message_id="", mode=MessageMode.AUTO, actor=conversation.owner
                )
            yield from super().stream(request)

    adapter = PostsWhileReplying([reply("ひとまず返します。"), reply("反映しました。")])
    finished = []

    def factory(run_obj, attempt_obj, **kwargs):
        session = JobSession(run_obj, attempt_obj, tick=lambda _s: None, **kwargs)
        session.job.finished, session.job.reported = True, True  # ジョブはもう動いていない
        finished.append(session)
        return session

    run_with(run, adapter, factory)
    assert run.status == RunStatus.COMPLETED
    assert agent_messages(run) == ["ひとまず返します。", "反映しました。"]
    # 完了のときの1回だけ保存する
    assert finished[0].suspends == [True]


def test_job_finishing_during_the_final_reply_is_shown_to_the_parent_first(conversation):
    """モデルの応答を待つ間に終わったジョブの結果を見ないまま完了しない"""
    run = post(conversation)
    sessions: list[JobSession] = []

    class FinishesWhileReplying(FakeAdapter):
        def stream(self, request):
            sessions[0].finish()
            yield from super().stream(request)

    def factory(run_obj, attempt_obj, **kwargs):
        sessions.append(JobSession(run_obj, attempt_obj, tick=lambda _s: None, **kwargs))
        return sessions[0]

    adapter = FinishesWhileReplying([reply("途中です。"), reply("確かめました。")])
    run_with(run, adapter, factory)
    assert run.status == RunStatus.COMPLETED
    assert any("finished with exit code 0" in m.text for m in adapter.requests[1].messages)
    assert agent_messages(run) == ["途中です。", "確かめました。"]


def test_jobs_finished_while_paused_are_all_reported_after_the_answer(conversation, app_user):
    run = post(conversation)
    attempt = claim_run(run.pk)
    assert attempt is not None
    Checkpoint.objects.create(
        run=run,
        attempt=attempt,
        sequence=1,
        storage="test",
        key="k",
        size=0,
        sha256="0",
        finished_jobs=[
            {
                "job_id": f"job_{n}",
                "description": "変換",
                "exit_code": 0,
                "stopped": "",
                "timeout_seconds": 1800,
                "output": f"/workspace/work/.yoriki/outputs/job_{n}.txt",
                "reported": False,
            }
            for n in (1, 2)
        ],
    )
    attempt.end()
    Run.objects.filter(pk=run.pk).update(status=RunStatus.QUEUED)
    run.refresh_from_db()
    adapter = FakeAdapter([reply("確かめました。")])
    run_with(run, adapter, FakeSession)
    assert RunEvent.objects.filter(run=run, kind=RunEventKind.JOB_FINISHED).count() == 2
    assert sum("job_" in m.text and "while the work was paused" in m.text for m in adapter.requests[0].messages) == 2


def test_step_limit_with_a_running_job_does_not_complete(conversation):
    """もう親を起こせないので、ジョブを止めて上限の失敗にする。結果を確定していても完了にしない"""
    run = post(conversation)
    Run.objects.filter(pk=run.pk).update(step_count=MAX_STEPS, result={"a": 1})
    run.refresh_from_db()
    run_obj_tools = make_submit_result_tool({"type": "object"})

    class WithResult(Runner):
        def _exposed_tools(self):
            return (run_obj_tools,)

    attempt = claim_run(run.pk)
    assert attempt is not None
    WithResult(
        attempt,
        adapter_factory=FakeAdapter([reply("まとめです。")]),
        sleep=lambda _s: None,
        session_factory=cast(Any, lambda r, a, **kw: JobSession(r, a, tick=lambda _s: None, **kw)),
    ).run()
    run.refresh_from_db()
    assert run.status == RunStatus.FAILED
    assert run.end_reason == RunEndReason.STEP_LIMIT


def test_step_limit_with_an_unseen_finished_job_does_not_complete(conversation):
    run = post(conversation)
    Run.objects.filter(pk=run.pk).update(step_count=MAX_STEPS, result={"a": 1})
    run.refresh_from_db()
    tool = make_submit_result_tool({"type": "object"})

    class WithResult(Runner):
        def _exposed_tools(self):
            return (tool,)

    sessions: list[JobSession] = []

    def factory(r, a, **kw):
        sessions.append(JobSession(r, a, tick=lambda _s: None, **kw))
        return sessions[0]

    class FinishesWhileReplying(FakeAdapter):
        def stream(self, request):
            sessions[0].finish()
            yield from super().stream(request)

    attempt = claim_run(run.pk)
    assert attempt is not None
    WithResult(
        attempt,
        adapter_factory=FinishesWhileReplying([reply("まとめです。")]),
        sleep=lambda _s: None,
        session_factory=factory,
    ).run()
    run.refresh_from_db()
    assert run.status == RunStatus.FAILED
    assert run.end_reason == RunEndReason.STEP_LIMIT


def test_new_attempt_after_a_lost_worker_says_the_jobs_are_gone(conversation):
    run = post(conversation)
    attempt = claim_run(run.pk)
    assert attempt is not None
    RunEvent.record(
        run,
        RunEventKind.TOOL_RESULT,
        {"call_id": "c1", "name": "shell", "content": [{"type": "text", "text": "started"}], "is_error": False},
    )
    Run.objects.filter(pk=run.pk).update(status=RunStatus.WAITING_BACKGROUND)
    ExecutionAttempt.objects.filter(pk=attempt.pk).update(lease_expires=timezone.now() - timedelta(minutes=1))
    _recover(attempt.pk)
    run.refresh_from_db()
    assert run.status == RunStatus.QUEUED
    notice_seen: list[str] = []

    class Recording(FakeAdapter):
        def stream(self, request):
            notice_seen.extend(m.text for m in request.messages)
            yield from super().stream(request)

    run_with(run, Recording([reply("確かめます。")]), FakeSession)
    assert any("Background jobs from that attempt are not running anymore" in text for text in notice_seen)


class SavingSession(JobSession):
    """止めたジョブ・見ていないジョブを保存の結果として返す"""

    def suspend(self, *, snapshot=True):
        stopped = [{"job_id": "job_1", "command": "sleep 60"}] if not self.job.finished else []
        super().suspend(snapshot=snapshot)
        finished = [
            {
                "job_id": "job_2",
                "description": "集計",
                "exit_code": 0,
                "stopped": "",
                "timeout_seconds": 1800,
                "output": "/workspace/work/.yoriki/outputs/job_2.txt",
                "reported": False,
            }
        ]
        return Suspended(None, "test", stopped, [], {}, finished)


def test_jobs_stopped_when_a_run_ends_reach_the_next_run(conversation, app_user):
    """止めたジョブと見ていないジョブの終了は、そのRunの記録に書き、次のRunの文脈に入る"""
    run = post(conversation)

    def tick(_session):
        Run.objects.get(pk=run.pk).request_cancel("止めて", app_user)

    run_with(run, FakeAdapter([reply("始めました。")]), lambda r, a, **kw: SavingSession(r, a, tick=tick, **kw))
    run.refresh_from_db()
    assert run.status == RunStatus.CANCELLED

    next_run = post(conversation, "続けて")
    assert next_run.parent_id == run.pk
    adapter = FakeAdapter([reply("確かめます。")])
    run_with(next_run, adapter, FakeSession)
    texts = [m.text for m in adapter.requests[0].messages]
    assert any("job_1" in t and "stopped when this work ended" in t for t in texts)
    assert any("job_2" in t and "job_2.txt" in t for t in texts)


def test_expired_question_tells_the_next_run_that_the_jobs_are_gone(conversation):
    run = post(conversation)

    def tick(_session):
        ClarificationRequest.objects.filter(run=run).update(expires=timezone.now() - timedelta(minutes=1))
        _expire_clarifications(timezone.now())

    run, _session = execute(run, FakeAdapter([calls(("c1", "ask_user", QUESTION))]), tick)
    assert run.status == RunStatus.FAILED
    next_run = post(conversation, "続けて")
    adapter = FakeAdapter([reply("確かめます。")])
    run_with(next_run, adapter, FakeSession)
    messages = adapter.requests[0].messages
    assert any("question expired while background jobs were running" in m.text for m in messages)
    # 結果のないまま終わった確認の呼出しには、結果が分からないことを補う
    index = next(i for i, m in enumerate(messages) if m.tool_calls)
    assert messages[index + 1].tool_call_id == "c1"


def test_cancel_while_waiting_stops_the_job(conversation, app_user):
    run = post(conversation)

    def tick(_session):
        Run.objects.get(pk=run.pk).request_cancel("止めて", app_user)

    run, session = execute(run, FakeAdapter([reply("始めました。")]), tick)
    assert run.status == RunStatus.CANCELLED
    assert RunStatus.CANCELLING in statuses(run)
    assert session.suspends == [True]


def test_question_keeps_the_job_running_and_the_answer_continues_in_the_same_attempt(conversation, app_user):
    run = post(conversation)
    answered: list[bool] = []

    def tick(session):
        if not answered:
            run.refresh_from_db()
            assert run.status == RunStatus.WAITING_USER
            answered.append(True)
            ClarificationRequest.objects.get(run=run).accept_answer(ANSWER, "", app_user)
        else:
            session.finish()

    adapter = FakeAdapter([calls(("c1", "ask_user", QUESTION)), reply("途中です。"), reply("終わりました。")])
    run, session = execute(run, adapter, tick)

    assert run.status == RunStatus.COMPLETED
    assert run.attempts.count() == 1
    # 回答の添付を動いているSandboxへ置く
    assert session.synced == 1
    assert session.suspends == [True]
    second = adapter.requests[1].messages
    index = next(i for i, m in enumerate(second) if m.tool_calls)
    assert second[index + 1].tool_call_id == "c1"


def test_job_finishing_during_a_question_releases_the_worker_without_waking_the_parent(conversation, app_user):
    run = post(conversation)
    adapter = FakeAdapter([calls(("c1", "ask_user", QUESTION))])
    run, session = execute(run, adapter, lambda s: s.finish())

    assert run.status == RunStatus.WAITING_USER
    assert len(adapter.requests) == 1
    assert session.suspends == [True]
    # 回答は新しいAttemptで受ける
    assert session.attempt_ended_at_close is True
    ClarificationRequest.objects.get(run=run).accept_answer(ANSWER, "", app_user)
    run.refresh_from_db()
    assert run.status == RunStatus.QUEUED


def test_answer_while_saving_continues_in_the_same_attempt(conversation, app_user):
    """手放す保存の間に回答が届いても、回答を取りこぼさない"""
    run = post(conversation)

    class Answering(JobSession):
        def suspend(self, *, snapshot=True):
            result = super().suspend(snapshot=snapshot)
            if len(self.suspends) == 1:
                ClarificationRequest.objects.get(run=run).accept_answer(ANSWER, "", app_user)
            return result

    adapter = FakeAdapter([calls(("c1", "ask_user", QUESTION)), reply("続けました。")])
    run_with(run, adapter, lambda r, a, **kw: Answering(r, a, tick=lambda s: s.finish(), **kw))
    assert run.status == RunStatus.COMPLETED
    assert run.attempts.count() == 1


def test_question_without_jobs_ends_the_attempt_before_the_answer_can_arrive(conversation, app_user):
    run = post(conversation)
    sessions = []

    def factory(run_obj, attempt_obj, **kwargs):
        session = JobSession(run_obj, attempt_obj, tick=lambda _s: None, **kwargs)
        session.job.finished, session.job.reported = True, True
        sessions.append(session)
        return session

    run_with(run, FakeAdapter([calls(("c1", "ask_user", QUESTION))]), factory)
    assert sessions[0].attempt_ended_at_close is True


def test_expired_question_fails_at_once_even_with_a_running_job(conversation):
    run = post(conversation)

    def tick(_session):
        ClarificationRequest.objects.filter(run=run).update(expires=timezone.now() - timedelta(minutes=1))
        _expire_clarifications(timezone.now())

    run, session = execute(run, FakeAdapter([calls(("c1", "ask_user", QUESTION))]), tick)
    assert run.status == RunStatus.FAILED
    assert run.end_reason == RunEndReason.EXPIRED
    assert RunStatus.CANCELLING not in statuses(run)
    # 回収も保存もせず、コンテナを片付ける
    assert session.suspends == []
    assert ExecutionAttempt.objects.get(run=run).ended is not None


def test_sandbox_lost_during_a_question_keeps_the_question(conversation, app_user):
    run = post(conversation)

    class Lost(JobSession):
        def running_jobs(self, *, refresh: bool = False) -> list:
            run.refresh_from_db()
            if run.status == RunStatus.WAITING_USER:
                raise SandboxUnavailable("gone")
            return super().running_jobs(refresh=refresh)

    run_with(
        run,
        FakeAdapter([calls(("c1", "ask_user", QUESTION))]),
        lambda r, a, **kw: Lost(r, a, tick=lambda _s: None, **kw),
    )
    assert run.status == RunStatus.WAITING_USER
    assert ExecutionAttempt.objects.get(run=run).failure.startswith("sandbox:")
    clarification = ClarificationRequest.objects.get(run=run)
    assert clarification.status == ClarificationStatus.OPEN
    clarification.accept_answer(ANSWER, "", app_user)
    run.refresh_from_db()
    assert run.status == RunStatus.QUEUED


def test_sandbox_lost_while_waiting_for_the_job_retries(conversation):
    run = post(conversation)

    class Lost(JobSession):
        def finished_unreported(self) -> list:
            run.refresh_from_db()
            if run.status == RunStatus.WAITING_BACKGROUND:
                raise SandboxUnavailable("gone")
            return super().finished_unreported()

    run_with(run, FakeAdapter([reply("始めました。")]), lambda r, a, **kw: Lost(r, a, tick=lambda _s: None, **kw))
    assert run.status == RunStatus.QUEUED
    assert RunStatus.RETRY_WAIT in statuses(run)


def waiting_with_an_expired_lease(run: Run, status: RunStatus) -> ExecutionAttempt:
    attempt = claim_run(run.pk)
    assert attempt is not None
    Run.objects.filter(pk=run.pk).update(status=status)
    ExecutionAttempt.objects.filter(pk=attempt.pk).update(lease_expires=timezone.now() - timedelta(minutes=1))
    return attempt


def test_worker_lost_during_a_question_keeps_the_question(conversation, app_user):
    run = post(conversation)
    attempt = waiting_with_an_expired_lease(run, RunStatus.WAITING_USER)
    clarification = ClarificationRequestFactory.create(run=run)
    _recover(attempt.pk)
    run.refresh_from_db()
    attempt.refresh_from_db()
    assert run.status == RunStatus.WAITING_USER
    assert attempt.failure == "lease_expired"
    clarification.accept_answer(ANSWER, "", app_user)
    run.refresh_from_db()
    assert run.status == RunStatus.QUEUED


def test_answer_before_the_lost_worker_is_noticed_does_not_wait_for_it(conversation, app_user):
    """leaseの切れたAttemptは手放したものとして扱い、回答を新しいAttemptへ渡す"""
    run = post(conversation)
    waiting_with_an_expired_lease(run, RunStatus.WAITING_USER)
    ClarificationRequestFactory.create(run=run).accept_answer(ANSWER, "", app_user)
    run.refresh_from_db()
    assert run.status == RunStatus.QUEUED


def test_worker_lost_while_waiting_for_the_job_retries(conversation):
    run = post(conversation)
    attempt = waiting_with_an_expired_lease(run, RunStatus.WAITING_BACKGROUND)
    _recover(attempt.pk)
    run.refresh_from_db()
    assert run.status == RunStatus.QUEUED


def test_cancel_without_a_live_attempt_finishes_at_once(conversation, app_user):
    run = post(conversation)
    waiting_with_an_expired_lease(run, RunStatus.WAITING_USER)
    Run.objects.get(pk=run.pk).request_cancel("止めて", app_user)
    run.refresh_from_db()
    assert run.status == RunStatus.CANCELLED


def over_the_limit(session: JobSession) -> None:
    started = timezone.now() - timedelta(seconds=ATTEMPT_LIMIT_SECONDS + 1)
    session.attempt.started = started
    ExecutionAttempt.objects.filter(pk=session.attempt.pk).update(started=started)


def test_attempt_limit_moves_to_a_new_attempt(conversation):
    run = post(conversation)
    run, session = execute(run, FakeAdapter([reply("始めました。")]), over_the_limit)

    assert run.status == RunStatus.QUEUED
    assert session.suspends == [True]
    attempt = ExecutionAttempt.objects.get(run=run)
    assert attempt.ended is not None
    # 障害ではないので、やり直しの回数に数えない
    assert attempt.failure == ""


def test_attempt_limit_during_a_question_releases_the_worker(conversation, app_user):
    run = post(conversation)
    run, session = execute(run, FakeAdapter([calls(("c1", "ask_user", QUESTION))]), over_the_limit)
    assert run.status == RunStatus.WAITING_USER
    assert session.suspends == [True]
    assert ExecutionAttempt.objects.get(run=run).ended is not None


def test_attempt_limit_saves_even_when_nobody_can_answer():
    run = task_run()
    assert not run.is_interactive
    attempt = claim_run(run.pk)
    assert attempt is not None
    ExecutionAttempt.objects.filter(pk=attempt.pk).update(
        started=timezone.now() - timedelta(seconds=ATTEMPT_LIMIT_SECONDS + 1)
    )
    attempt.refresh_from_db()
    sessions = []

    def factory(run_obj, attempt_obj, **kwargs):
        sessions.append(JobSession(run_obj, attempt_obj, tick=lambda _s: None, **kwargs))
        return sessions[0]

    Runner(attempt, adapter_factory=FakeAdapter([]), sleep=lambda _s: None, session_factory=factory).run()
    run.refresh_from_db()
    assert run.status == RunStatus.QUEUED
    assert sessions[0].suspends == [True]


def test_task_is_told_about_the_jobs_once_and_then_waits():
    run = task_run()
    adapter = FakeAdapter([reply("終わります。"), reply("待ちます。"), reply("終わりました。")])
    run, _session = execute(run, adapter, lambda s: s.finish())

    assert run.status == RunStatus.COMPLETED
    notice = RUNNING_JOBS_NOTICE.split("{jobs}", maxsplit=1)[0]
    assert any(notice in m.text for m in adapter.requests[1].messages)
    assert RunStatus.WAITING_BACKGROUND in statuses(run)


def test_submit_result_is_rejected_while_jobs_are_running(conversation):
    run = post(conversation)
    attempt = claim_run(run.pk)
    assert attempt is not None
    session = JobSession(run, attempt, tick=lambda _s: None)
    call = ToolCall("c1", "submit_result", {"a": 1})
    context = ToolContext(run, attempt, call, session=cast(Any, session))

    result = submit_result({"a": 1}, context, schema={"type": "object"})
    assert result.is_error
    assert "job_1" in str(result.content[0])
    run.refresh_from_db()
    assert run.result is None

    session.finish()
    assert not submit_result({"a": 1}, context, schema={"type": "object"}).is_error
    run.refresh_from_db()
    assert run.result == {"a": 1}
