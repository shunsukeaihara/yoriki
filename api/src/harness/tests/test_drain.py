"""Workerのdrain中のRunnerの動き（deployment.md §6）"""

from typing import Any

import pytest

from run.models import ExecutionAttempt, RunEvent, RunEventKind, RunStatus, Subagent, SubagentStatus, WorkerDrain
from run.tasks import claim_run, claim_subagent

from .fakes import FakeAdapter, calls, reply
from .test_subagents import START, run_parent, start_and_wait
from .test_subagents import post as post_parent
from .test_waiting import ANSWER, QUESTION, JobSession, execute, fixture_conversation, post, run_with, task_run

__all__ = ["fixture_conversation"]

pytestmark = [pytest.mark.usefixtures("default_runtime"), pytest.mark.django_db]

TODO: tuple[str, str, dict[str, Any]] = ("c1", "todo_write", {"todos": []})


class DrainingAdapter(FakeAdapter):
    """最初の応答の間に、drainが始まる"""

    def stream(self, request):
        WorkerDrain.start()
        yield from super().stream(request)


def test_run_saves_and_returns_to_the_queue_at_the_next_boundary(conversation):
    run = post(conversation)
    # ジョブは最初から動いていない
    adapter = DrainingAdapter([calls(TODO)])
    run, session = run_without_jobs(run, adapter)

    assert run.status == RunStatus.QUEUED
    assert session.suspends == [True]
    assert len(adapter.requests) == 1
    attempt = ExecutionAttempt.objects.get(run=run)
    assert attempt.ended is not None
    # 障害ではないので、やり直しの回数に数えない
    assert attempt.failure == ""

    WorkerDrain.stop()
    resumed = FakeAdapter([reply("終わりました。")])
    run, _session = run_without_jobs(run, resumed)
    assert run.status == RunStatus.COMPLETED


def test_run_nobody_can_answer_is_saved_too():
    run, session = run_without_jobs(task_run(), DrainingAdapter([calls(TODO)]))
    assert run.status == RunStatus.QUEUED
    assert session.suspends == [True]


def test_run_that_finishes_in_the_current_step_is_completed(conversation):
    run, _session = run_without_jobs(post(conversation), DrainingAdapter([reply("終わりました。")]))
    assert run.status == RunStatus.COMPLETED


def test_running_jobs_are_waited_for(conversation):
    """ジョブが終わるまで止めずに続け、終わったら保存してWorkerを手放す"""
    adapter = DrainingAdapter([calls(TODO), reply("待ちます。")])
    run, session = execute(post(conversation), adapter, lambda s: s.finish())

    assert len(adapter.requests) == 2
    assert run.status == RunStatus.QUEUED
    assert session.suspends == [True]
    # ジョブは止められずに終わり、その後に保存した
    finished = RunEvent.objects.get(run=run, kind=RunEventKind.JOB_FINISHED)
    assert finished.data["stopped"] == ""


def test_answer_during_the_drain_waits_in_the_queue(conversation, app_user):
    run, _session = run_without_jobs(post(conversation), DrainingAdapter([calls(("c1", "ask_user", QUESTION))]))
    assert run.status == RunStatus.WAITING_USER
    run.clarifications.get().accept_answer(ANSWER, "", app_user)
    run.refresh_from_db()
    assert run.status == RunStatus.QUEUED
    assert claim_run(run.pk) is None


def test_queued_subagent_is_not_claimed(conversation):
    _run, subagent = start_and_wait(conversation)
    WorkerDrain.start()
    assert claim_subagent(subagent.pk) is None
    subagent.refresh_from_db()
    assert subagent.status == SubagentStatus.QUEUED
    WorkerDrain.stop()
    assert claim_subagent(subagent.pk) is not None


def test_running_subagent_continues_while_the_parent_is_queued(conversation):
    run = post_parent(conversation)

    class Adapter(FakeAdapter):
        def stream(self, request):
            if self.requests:
                # 2回目の応答の前に子が動き出し、応答の間にdrainが始まる
                if not WorkerDrain.is_active():
                    assert claim_subagent(Subagent.objects.get(run=run).pk) is not None
                WorkerDrain.start()
            yield from super().stream(request)

    run_parent(run, Adapter([calls(START), calls(TODO)]))
    assert run.status == RunStatus.QUEUED
    attempt = ExecutionAttempt.objects.get(run=run, subagent__isnull=False)
    assert attempt.holds(run)


def run_without_jobs(run, adapter):
    """ジョブのないSandboxで動かす"""
    sessions = []

    def factory(run_obj, attempt_obj, **kwargs):
        session = JobSession(run_obj, attempt_obj, tick=lambda _s: None, **kwargs)
        session.finish()
        session.job.reported = True
        sessions.append(session)
        return session

    run_with(run, adapter, factory)
    return run, sessions[0]
