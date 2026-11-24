"""モデルの応答の途中での停止（harness.md §10.8・§11.2）"""

import threading
import time
from decimal import Decimal

import pytest
from django.utils import timezone

from conversation.dispatch import post_message
from conversation.factories import ConversationFactory
from conversation.models import ConversationMessage, MessageRole
from harness import compaction
from harness import runner as runner_module
from harness.context import STOPPED_REPLY, build_messages
from harness.runner import Runner
from harness.subagent_runner import SubagentRunner
from llm.models import LLMConnectionLease
from llm.providers.base import (
    ProviderError,
    TextDelta,
    ToolCall,
    ToolCallRequested,
    Usage,
    UsageProgress,
    connection_error,
)
from run.models import (
    ClarificationRequest,
    Run,
    RunEvent,
    RunEventKind,
    RunStatus,
    Subagent,
    SubagentStatus,
)
from run.tasks import claim_run
from usage.models import BudgetReservation, UsageLedger, UsagePurpose

from .fakes import FakeAdapter, reply
from .test_runner import kinds, start
from .test_subagents import run_child, start_and_wait

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("default_runtime", "fast_stop")]


@pytest.fixture(name="fast_stop")
def fixture_fast_stop(monkeypatch):
    monkeypatch.setattr(runner_module, "STOP_INTERVAL", 0.05)


@pytest.fixture(name="conversation")
def fixture_conversation(app_user):
    return ConversationFactory.create(owner=app_user)


class SilentAdapter(FakeAdapter):
    """`events` を返した後、打ち切られるまで黙る。打ち切りは実際と同じく ProviderError になる"""

    def __init__(self, events=(), *, stop):
        super().__init__([])
        self.events = events
        self.stop = stop

    def stream(self, request):
        self.requests.append(request)
        yield from self.events
        self.stop()
        assert self.canceller is not None
        deadline = time.monotonic() + 10
        while not self.canceller.cancelled:
            if time.monotonic() > deadline:
                raise AssertionError("the stream was not cancelled")
            time.sleep(0.02)
        raise connection_error()


def execute(run: Run, adapter: FakeAdapter, sleep=lambda _s: None) -> Run:
    attempt = claim_run(run.pk)
    assert attempt is not None
    Runner(attempt, adapter_factory=adapter, sleep=sleep).run()
    run.refresh_from_db()
    return run


def stopper(run: Run, actor):
    return lambda: Run.objects.get(pk=run.pk).request_cancel("不要になった", actor=actor)


def test_stops_in_the_middle_and_keeps_the_text(conversation, app_user):
    run = start(conversation)
    events = [TextDelta("途中までの"), TextDelta("答え"), ToolCallRequested(ToolCall("c1", "todo_write", {}))]
    run = execute(run, SilentAdapter(events, stop=stopper(run, app_user)))

    assert run.status == RunStatus.CANCELLED
    message = ConversationMessage.objects.get(run=run, role=MessageRole.AGENT)
    assert (message.body, message.is_partial) == ("途中までの答え", True)
    # 障害として扱わない。Toolも実行しない
    recorded = kinds(run)
    assert RunEventKind.LLM_RETRY not in recorded
    assert RunEventKind.ASSISTANT_ATTEMPT not in recorded
    assert RunEventKind.TOOL_RESULT not in recorded
    assert not ClarificationRequest.objects.filter(run=run).exists()
    # 枠と予約を返す
    assert not LLMConnectionLease.objects.filter(expires__gt=timezone.now()).exists()
    assert not BudgetReservation.objects.exists()
    # 利用量は見積もりで記録する
    usage = UsageLedger.objects.get(run=run)
    assert usage.is_estimated
    assert usage.purpose == UsagePurpose.STEP
    assert usage.input_tokens > 0
    assert usage.output_tokens == len("途中までの答え")
    assert usage.cost >= Decimal(0)

    # 次のRunの文脈に、途中で止められた応答として入る
    post_message(conversation, body="続きをお願い", client_message_id="", actor=app_user)
    following = Run.objects.get(parent=run)
    adapter = FakeAdapter([reply("続きです。")])
    following = execute(following, adapter)
    assert following.status == RunStatus.COMPLETED
    sent = [(m.role, m.text) for m in adapter.requests[0].messages]
    position = sent.index(("assistant", "途中までの答え"))
    assert sent[position + 1] == ("user", STOPPED_REPLY)
    assert not any(m.tool_calls for m in adapter.requests[0].messages)
    assert not ConversationMessage.objects.get(run=following, role=MessageRole.AGENT).is_partial


def test_stops_while_the_model_is_silent(conversation, app_user):
    run = start(conversation)
    run = execute(run, SilentAdapter(stop=stopper(run, app_user)))
    assert run.status == RunStatus.CANCELLED
    assert RunEventKind.ASSISTANT_STOPPED not in kinds(run)
    assert not ConversationMessage.objects.filter(run=run, role=MessageRole.AGENT).exists()
    usage = UsageLedger.objects.get(run=run)
    assert usage.is_estimated
    assert usage.output_tokens == 0


def test_usage_reported_so_far_is_used(conversation, app_user):
    run = start(conversation)
    events = [UsageProgress(Usage(1234, 1, 1000, 100)), TextDelta("あいう")]
    run = execute(run, SilentAdapter(events, stop=stopper(run, app_user)))
    usage = UsageLedger.objects.get(run=run)
    assert (usage.input_tokens, usage.cached_input_tokens, usage.cache_write_tokens) == (1234, 1000, 100)
    assert usage.output_tokens == 3
    assert usage.is_estimated


def test_a_finished_response_is_recorded_as_usual(conversation, app_user):
    """打ち切る前に受け終えた応答は、確定した利用量で記録する"""
    run = start(conversation)
    stop = stopper(run, app_user)

    class Adapter(FakeAdapter):
        def stream(self, request):
            yield from super().stream(request)
            stop()

    run = execute(run, Adapter([reply("できました。")]))
    assert run.status == RunStatus.CANCELLED
    usage = UsageLedger.objects.get(run=run)
    assert (usage.input_tokens, usage.output_tokens, usage.is_estimated) == (100, 20, False)
    assert RunEventKind.ASSISTANT_STOPPED not in kinds(run)


def test_stops_while_waiting_to_retry(conversation, app_user):
    run = start(conversation)
    stop = stopper(run, app_user)
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 1:
            stop()

    adapter = FakeAdapter([ProviderError("一時的な障害", retryable=True, status_code=503)])
    run = execute(run, adapter, sleep)
    assert run.status == RunStatus.CANCELLED
    assert len(adapter.requests) == 1
    assert len(waits) == 1


def test_stops_during_compaction(conversation, app_user, monkeypatch):
    run = start(conversation)
    monkeypatch.setattr(compaction, "threshold", lambda _version: -1)
    chosen = type("Range", (), {"start": 0, "end": 10**6, "user_texts": []})()
    monkeypatch.setattr(compaction, "choose_range", lambda _items: chosen)
    run = execute(run, SilentAdapter([TextDelta("要約の途中")], stop=stopper(run, app_user)))
    assert run.status == RunStatus.CANCELLED
    assert RunEventKind.COMPACTION not in kinds(run)
    # 要約の途中の文は、会話にも文脈にも残さない
    assert RunEventKind.ASSISTANT_STOPPED not in kinds(run)
    assert not ConversationMessage.objects.filter(run=run, role=MessageRole.AGENT).exists()
    assert UsageLedger.objects.get(run=run).purpose == UsagePurpose.COMPACTION


def test_other_status_checks_do_not_stop_the_stream(conversation):
    """停止の依頼がなければ、黙っている間も待つ"""
    run = start(conversation)

    class Adapter(FakeAdapter):
        def stream(self, request):
            time.sleep(0.5)
            yield from super().stream(request)

    assert execute(run, Adapter([reply("完了")])).status == RunStatus.COMPLETED


def test_cancelling_the_run_stops_the_subagent_in_the_middle(conversation, app_user):
    run, subagent = start_and_wait(conversation)
    run_child(subagent, SilentAdapter([TextDelta("調査の途中")], stop=stopper(run, app_user)))
    run.refresh_from_db()
    assert subagent.status == SubagentStatus.CANCELLED
    assert run.status == RunStatus.CANCELLED
    assert UsageLedger.objects.get(subagent=subagent).is_estimated
    # 停止中の親には、途中までの文を書かない
    assert not RunEvent.objects.filter(run=run, subagent=subagent, kind=RunEventKind.ASSISTANT_STOPPED).exists()
    assert not ConversationMessage.objects.filter(run=run, body="調査の途中").exists()


def test_killing_the_subagent_stops_it_in_the_middle(conversation):
    run, subagent = start_and_wait(conversation)

    def kill() -> None:
        Subagent.objects.filter(pk=subagent.pk).update(status=SubagentStatus.CANCELLED, reported=True)

    run_child(subagent, SilentAdapter([TextDelta("調査の途中")], stop=kill))
    run.refresh_from_db()
    assert subagent.status == SubagentStatus.CANCELLED
    assert run.status == RunStatus.WAITING_BACKGROUND
    assert UsageLedger.objects.get(subagent=subagent).is_estimated


def test_the_time_limit_stops_a_silent_subagent(conversation, monkeypatch):
    _run, subagent = start_and_wait(conversation)
    expired = threading.Event()
    monkeypatch.setattr(SubagentRunner, "_timed_out", lambda self: expired.is_set())
    run_child(subagent, SilentAdapter([TextDelta("調査の途中")], stop=expired.set))
    assert subagent.status == SubagentStatus.FAILED
    assert "time limit" in subagent.error
    assert UsageLedger.objects.get(subagent=subagent).is_estimated


def test_stopped_text_in_the_context(conversation):
    run = start(conversation)
    RunEvent.record(run, RunEventKind.ASSISTANT_STOPPED, {"step": 1, "text": "途中"})
    messages = build_messages(run, RunEvent.objects.filter(run=run).order_by("sequence"))
    assert [(m.role, m.text) for m in messages[-2:]] == [("assistant", "途中"), ("user", STOPPED_REPLY)]
