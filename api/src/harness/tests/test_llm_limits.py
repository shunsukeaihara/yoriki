"""接続の上限の中でのモデルの呼出し（harness.md §10.8）"""

import time
from datetime import timedelta

import pytest
from django.utils import timezone

from conversation.factories import ConversationFactory
from harness import compaction
from harness import runner as runner_module
from harness.runner import Runner
from harness.subagent_runner import SubagentRunner
from llm import limits
from llm.models import LLMConnection, LLMConnectionLease
from llm.providers.base import ProviderError
from run import models as run_models
from run.models import ClarificationKind, Run, RunEvent, RunEventKind, RunStatus, Subagent, SubagentStatus, WorkerDrain
from run.tasks import claim_run, claim_subagent
from usage.models import BudgetReservation

from .fakes import FakeAdapter, reply
from .test_runner import execute, kinds, start
from .test_subagents import SubagentSession, start_and_wait

pytestmark = pytest.mark.usefixtures("default_runtime")


class SlowAdapter(FakeAdapter):
    """最初のイベントが届くまで黙っている"""

    def stream(self, request):
        time.sleep(2.5)
        yield from super().stream(request)


@pytest.fixture(name="run")
def fixture_run(app_user) -> Run:
    return start(ConversationFactory.create(owner=app_user))


def occupy(run: Run) -> LLMConnectionLease:
    """接続の枠をほかの呼出しで埋める"""
    target = run.model_version.connection
    LLMConnection.objects.filter(pk=target.pk).update(max_concurrency=1)
    now = timezone.now()
    return LLMConnectionLease.objects.create(
        connection=target, holder="other", created=now, expires=now + timedelta(hours=1)
    )


def execute_waiting(run: Run, adapter: FakeAdapter, sleep) -> Run:
    attempt = claim_run(run.pk)
    assert attempt is not None
    Runner(attempt, adapter_factory=adapter, sleep=sleep).run()
    run.refresh_from_db()
    return run


@pytest.mark.django_db(transaction=True)
def test_lease_is_kept_until_the_first_event(run, monkeypatch):
    monkeypatch.setattr(run_models, "LEASE_SECONDS", 1)
    monkeypatch.setattr(runner_module, "HEARTBEAT_INTERVAL", 0.2)
    run = execute_waiting(run, SlowAdapter([reply("完了")]), lambda _s: None)
    assert run.status == RunStatus.COMPLETED


@pytest.mark.django_db
def test_waits_for_the_slot_and_records_it_once(run):
    held = occupy(run)
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 3:
            LLMConnectionLease.objects.filter(pk=held.pk).update(expires=timezone.now())

    run = execute_waiting(run, FakeAdapter([reply("完了")]), sleep)
    assert run.status == RunStatus.COMPLETED
    assert kinds(run).count(RunEventKind.LLM_WAIT) == 1
    assert RunEvent.objects.get(run=run, kind=RunEventKind.LLM_WAIT).comment
    # 呼出しが終われば枠を返す
    assert not LLMConnectionLease.objects.filter(expires__gt=timezone.now()).exists()


@pytest.mark.django_db
def test_slot_is_released_between_retries(run):
    LLMConnection.objects.filter(pk=run.model_version.connection_id).update(max_concurrency=1)
    active: list[bool] = []

    def sleep(_seconds: float) -> None:
        active.append(LLMConnectionLease.objects.filter(expires__gt=timezone.now()).exists())

    failure = ProviderError("一時的な障害", retryable=True, status_code=503)
    run = execute_waiting(run, FakeAdapter([failure, reply("完了")]), sleep)
    assert run.status == RunStatus.COMPLETED
    assert active == [False]
    # 失敗した要求も、1分あたりの要求数に数える
    assert LLMConnectionLease.objects.count() == 2


@pytest.mark.django_db
def test_busy_connection_is_handled_as_a_provider_failure_without_retries(run, monkeypatch):
    monkeypatch.setattr(limits, "MAX_WAIT_SECONDS", 0)
    occupy(run)
    adapter = FakeAdapter([])
    run = execute(run, adapter)
    assert run.status == RunStatus.WAITING_USER
    assert run.clarifications.get().kind == ClarificationKind.MODEL_SWITCH
    assert kinds(run).count(RunEventKind.LLM_RETRY) == 0
    assert adapter.requests == []
    assert not BudgetReservation.objects.exists()


@pytest.mark.django_db
def test_busy_connection_during_compaction_is_a_provider_failure(run, monkeypatch):
    monkeypatch.setattr(limits, "MAX_WAIT_SECONDS", 0)
    monkeypatch.setattr(compaction, "threshold", lambda _version: -1)
    events = compaction.choose_range
    monkeypatch.setattr(
        compaction,
        "choose_range",
        lambda items: events(items) or type("Range", (), {"start": 0, "end": 10**6, "user_texts": []})(),
    )
    occupy(run)
    run = execute(run, FakeAdapter([]))
    assert run.status == RunStatus.WAITING_USER


@pytest.mark.django_db
def test_cancel_while_waiting(run, app_user):
    occupy(run)
    adapter = FakeAdapter([])

    def sleep(_seconds: float) -> None:
        Run.objects.filter(pk=run.pk).update(status=RunStatus.CANCELLING)

    run = execute_waiting(run, adapter, sleep)
    assert run.status == RunStatus.CANCELLED
    assert adapter.requests == []


@pytest.mark.django_db
def test_drain_while_waiting_returns_the_run_to_the_queue(run):
    occupy(run)
    run = execute_waiting(run, FakeAdapter([]), lambda _s: WorkerDrain.start())
    assert run.status == RunStatus.QUEUED


@pytest.mark.django_db
def test_lease_lost_while_waiting_stops_the_attempt(run):
    occupy(run)
    run_pk = run.pk
    adapter = FakeAdapter([])

    def sleep(_seconds: float) -> None:
        # 別のWorkerが回復させた
        Run.objects.filter(pk=run_pk).update(status=RunStatus.RETRY_WAIT)

    run = execute_waiting(run, adapter, sleep)
    assert run.status == RunStatus.RETRY_WAIT
    assert adapter.requests == []


def wait_as_subagent(conversation, sleep) -> Subagent:
    """子が枠を待つ間に `sleep` を呼ぶ"""
    run, subagent = start_and_wait(conversation)
    occupy(run)
    attempt = claim_subagent(subagent.pk)
    assert attempt is not None
    SubagentRunner(
        attempt, adapter_factory=FakeAdapter([]), sleep=sleep(subagent), session_factory=SubagentSession
    ).run()
    subagent.refresh_from_db()
    return subagent


@pytest.mark.django_db
def test_subagent_stops_waiting_when_the_conversation_becomes_confidential(app_user):
    def sleep(subagent):
        return lambda _s: Run.objects.filter(pk=subagent.run_id).update(handles_sensitive_data=True)

    subagent = wait_as_subagent(ConversationFactory.create(owner=app_user), sleep)
    assert subagent.status == SubagentStatus.CANCELLED


@pytest.mark.django_db
def test_subagent_stops_waiting_at_its_time_limit(app_user, monkeypatch):
    def sleep(_subagent):
        return lambda _s: monkeypatch.setattr(SubagentRunner, "_timeout_seconds", lambda _self: 0)

    subagent = wait_as_subagent(ConversationFactory.create(owner=app_user), sleep)
    assert subagent.status == SubagentStatus.FAILED
    assert "time limit" in subagent.error
