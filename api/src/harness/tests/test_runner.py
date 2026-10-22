"""Runnerのテスト。LLMの応答は FakeAdapter で差し替え、RunEvent・状態・会話のメッセージを確かめる"""

import pytest

from conversation.dispatch import MessageMode, post_message
from conversation.factories import ConversationFactory
from conversation.models import ConversationMessage, InstructionState, MessageRole, UserInstruction
from harness import runner as runner_module
from harness.runner import Runner
from llm.providers.base import ProviderError, StopReason, ToolChoice
from run.models import (
    ClarificationKind,
    ClarificationRequest,
    ClarificationStatus,
    Run,
    RunEndReason,
    RunEvent,
    RunEventKind,
    RunStatus,
    ToolInvocation,
    ToolStatus,
)
from run.tasks import claim_run
from runtime.models import RuntimeProfile
from usage.models import UsageLedger

from .fakes import FakeAdapter, calls, reply

# Runを始めるには既定のRuntime Profileが要る
pytestmark = pytest.mark.usefixtures("default_runtime")

QUESTION = {
    "questions": [
        {
            "header": "取引先",
            "question": "どちらの取引先ですか",
            "options": [{"label": "本社 (Recommended)"}, {"label": "大阪支店"}],
        }
    ]
}


@pytest.fixture(name="conversation")
def fixture_conversation(app_user):
    return ConversationFactory.create(owner=app_user)


def start(conversation, body="請求書を確認して") -> Run:
    post_message(conversation, body=body, client_message_id="", mode=MessageMode.AUTO, actor=conversation.owner)
    return Run.objects.get(conversation=conversation)


def execute(run: Run, adapter: FakeAdapter) -> Run:
    attempt = claim_run(run.pk)
    assert attempt is not None
    Runner(attempt, adapter_factory=adapter, sleep=lambda _s: None).run()
    run.refresh_from_db()
    return run


def kinds(run: Run) -> list[int]:
    return list(RunEvent.objects.filter(run=run).order_by("sequence").values_list("kind", flat=True))


@pytest.mark.django_db
def test_reply_completes_the_run(conversation):
    run = start(conversation)
    adapter = FakeAdapter([reply("確認しました。問題はありません。")])
    run = execute(run, adapter)

    assert run.status == RunStatus.COMPLETED
    assert run.end_reason == RunEndReason.COMPLETED
    agent = ConversationMessage.objects.get(conversation=conversation, role=MessageRole.AGENT)
    assert agent.body == "確認しました。問題はありません。"
    assert agent.run == run
    usage = UsageLedger.objects.get(run=run)
    assert (usage.input_tokens, usage.output_tokens) == (100, 20)

    request = adapter.requests[0]
    assert request.messages[0].role == "system"
    assert any(m.role == "user" and m.text == "請求書を確認して" for m in request.messages)
    assert {t.name for t in request.tools} == {
        "ask_user",
        "todo_write",
        "read",
        "write",
        "edit",
        "glob",
        "grep",
        "shell",
        "job_output",
        "job_kill",
    }


@pytest.mark.django_db
def test_tool_call_then_reply(conversation):
    run = start(conversation)
    todos = {"todos": [{"content": "読む", "status": "in_progress"}, {"content": "確かめる", "status": "pending"}]}
    adapter = FakeAdapter([calls(("call_1", "todo_write", todos)), reply("終わりました。")])
    run = execute(run, adapter)

    assert run.status == RunStatus.COMPLETED
    assert run.step_count == 2
    invocation = ToolInvocation.objects.get(run=run, call_id="call_1")
    assert invocation.status == ToolStatus.SUCCEEDED
    assert invocation.value == todos
    # 2回目の要求には、Tool呼出しとその結果が続けて入る
    second = adapter.requests[1].messages
    index = next(i for i, m in enumerate(second) if m.tool_calls)
    assert second[index + 1].role == "tool"
    assert second[index + 1].tool_call_id == "call_1"


@pytest.mark.django_db
def test_ask_user_waits_and_resumes_with_the_answer(conversation, app_user):
    run = start(conversation)
    run = execute(run, FakeAdapter([calls(("call_q", "ask_user", QUESTION))]))

    assert run.status == RunStatus.WAITING_USER
    clarification = ClarificationRequest.objects.get(run=run)
    assert clarification.kind == ClarificationKind.QUESTION
    assert clarification.tool_call_id == "call_q"
    # 回答が届くまで、ask_user の結果は記録しない
    assert RunEventKind.TOOL_RESULT not in kinds(run)

    clarification.accept_answer([{"selected": ["本社 (Recommended)"], "text": ""}], "", app_user)
    run.refresh_from_db()
    assert run.status == RunStatus.QUEUED

    adapter = FakeAdapter([reply("本社として処理しました。")])
    run = execute(run, adapter)
    assert run.status == RunStatus.COMPLETED
    messages = adapter.requests[0].messages
    index = next(i for i, m in enumerate(messages) if m.tool_calls)
    assert messages[index + 1].tool_call_id == "call_q"
    assert "本社 (Recommended)" in messages[index + 1].text


@pytest.mark.django_db
def test_free_text_message_answers_the_open_question(conversation, app_user):
    run = start(conversation)
    run = execute(run, FakeAdapter([calls(("call_q", "ask_user", QUESTION))]))
    post_message(conversation, body="大阪支店です", client_message_id="", mode=MessageMode.AUTO, actor=app_user)

    clarification = ClarificationRequest.objects.get(run=run)
    assert clarification.status == ClarificationStatus.ANSWERED
    assert clarification.comment == "大阪支店です"
    run.refresh_from_db()
    assert run.status == RunStatus.QUEUED


@pytest.mark.django_db
def test_calls_after_ask_user_are_not_executed(conversation):
    run = start(conversation)
    todos = {"todos": [{"content": "読む", "status": "pending"}]}
    run = execute(run, FakeAdapter([calls(("call_q", "ask_user", QUESTION), ("call_t", "todo_write", todos))]))

    assert run.status == RunStatus.WAITING_USER
    result = RunEvent.objects.get(run=run, kind=RunEventKind.TOOL_RESULT)
    assert result.data["call_id"] == "call_t"
    assert result.data["is_error"]


@pytest.mark.django_db
def test_additional_instruction_is_applied_at_the_next_step(conversation, app_user):
    run = start(conversation)
    todos = {"todos": [{"content": "読む", "status": "in_progress"}]}

    class InstructingAdapter(FakeAdapter):
        def stream(self, request):
            if len(self.requests) == 0:
                # 1つ目の応答の途中で、利用者が追加の指示を送る
                post_message(
                    conversation, body="支店は別にして", client_message_id="", mode=MessageMode.AUTO, actor=app_user
                )
            yield from super().stream(request)

    adapter = InstructingAdapter([calls(("call_1", "todo_write", todos)), reply("支店を分けました。")])
    run = execute(run, adapter)

    assert run.status == RunStatus.COMPLETED
    instruction = UserInstruction.objects.get(conversation=conversation)
    assert instruction.state == InstructionState.APPLIED
    texts = [m.text for m in adapter.requests[1].messages if m.role == "user"]
    assert any("支店は別にして" in t for t in texts)


@pytest.mark.django_db
def test_next_request_starts_after_completion(conversation, app_user):
    run = start(conversation)
    post_message(conversation, body="次はこれ", client_message_id="", mode=MessageMode.NEXT, actor=app_user)
    run = execute(run, FakeAdapter([reply("完了")]))

    assert run.status == RunStatus.COMPLETED
    next_run = Run.objects.exclude(pk=run.pk).get(conversation=conversation)
    assert next_run.status == RunStatus.QUEUED
    assert UserInstruction.objects.get(conversation=conversation).state == InstructionState.STARTED


@pytest.mark.django_db
def test_next_requests_that_cannot_start_are_discarded_with_the_reason(conversation, app_user, default_runtime):
    # 投稿の後に既定のRuntime Profileがなくなったら、次の依頼は始めず、理由を会話に残す（黙って捨てない）
    run = start(conversation)
    post_message(conversation, body="次はこれ", client_message_id="", mode=MessageMode.NEXT, actor=app_user)
    post_message(conversation, body="その次はこれ", client_message_id="", mode=MessageMode.NEXT, actor=app_user)
    RuntimeProfile.objects.filter(pk=default_runtime.pk).update(is_default=False)
    run = execute(run, FakeAdapter([reply("完了")]))

    assert run.status == RunStatus.COMPLETED
    assert not Run.objects.exclude(pk=run.pk).filter(conversation=conversation).exists()
    states = UserInstruction.objects.filter(conversation=conversation).values_list("state", flat=True)
    assert list(states) == [InstructionState.DISCARDED, InstructionState.DISCARDED]
    notices = ConversationMessage.objects.filter(conversation=conversation, role=MessageRole.SYSTEM)
    assert notices.count() == 2
    assert all("Runtime Profile" in n.body for n in notices)


@pytest.mark.django_db
def test_cancel_while_running_stops_at_the_boundary(conversation, app_user):
    run = start(conversation)
    todos = {"todos": [{"content": "読む", "status": "pending"}]}

    class CancellingAdapter(FakeAdapter):
        def stream(self, request):
            Run.objects.get(pk=run.pk).request_cancel("不要になった", actor=app_user)
            yield from super().stream(request)

    run = execute(run, CancellingAdapter([calls(("c1", "todo_write", todos), ("c2", "todo_write", todos))]))
    assert run.status == RunStatus.CANCELLED
    # 2つ目の呼出しは実行せず、取消の結果で閉じる
    results = RunEvent.objects.filter(run=run, kind=RunEventKind.TOOL_RESULT).order_by("sequence")
    assert [r.data["call_id"] for r in results] == ["c1", "c2"]
    assert "cancelled" in results[1].data["content"][0]["text"]


@pytest.mark.django_db
def test_invalid_arguments_and_unknown_tool_are_returned_to_the_model(conversation):
    run = start(conversation)
    adapter = FakeAdapter(
        [
            calls(("c1", "todo_write", {"todos": "読む"}), ("c2", "grep_search", {})),
            reply("やり直しました。"),
        ]
    )
    run = execute(run, adapter)
    assert run.status == RunStatus.COMPLETED
    tool_messages = [m for m in adapter.requests[1].messages if m.role == "tool"]
    assert "Invalid arguments" in tool_messages[0].text
    assert 'No tool named "grep_search"' in tool_messages[1].text


@pytest.mark.django_db
def test_repeating_the_same_failing_call_fails_the_run(conversation):
    run = start(conversation)
    bad = {"todos": "x"}
    adapter = FakeAdapter([calls((f"c{i}", "todo_write", bad)) for i in range(6)])
    run = execute(run, adapter)
    assert run.status == RunStatus.FAILED
    assert run.end_reason == RunEndReason.REPEATED_ERRORS


@pytest.mark.django_db
def test_provider_failure_asks_to_switch_models(conversation):
    run = start(conversation)
    errors: list = [ProviderError("一時的な障害", retryable=True, status_code=503) for _ in range(6)]
    run = execute(run, FakeAdapter(errors))

    assert run.status == RunStatus.WAITING_USER
    clarification = ClarificationRequest.objects.get(run=run)
    assert clarification.kind == ClarificationKind.MODEL_SWITCH
    assert kinds(run).count(RunEventKind.LLM_RETRY) == 5


@pytest.mark.django_db
def test_non_retryable_provider_error_fails_the_run(conversation):
    run = start(conversation)
    run = execute(run, FakeAdapter([ProviderError("API keyが正しくありません", status_code=401)]))
    assert run.status == RunStatus.FAILED
    assert run.end_reason == RunEndReason.PROVIDER


@pytest.mark.django_db
def test_step_limit_ends_with_a_summary_without_tools(conversation, monkeypatch):
    monkeypatch.setattr(runner_module, "MAX_STEPS", 1)
    run = start(conversation)
    todos = {"todos": [{"content": "読む", "status": "pending"}]}
    adapter = FakeAdapter([calls(("c1", "todo_write", todos)), reply("ここまでの結果です。")])
    run = execute(run, adapter)

    assert run.status == RunStatus.FAILED
    assert run.end_reason == RunEndReason.STEP_LIMIT
    last = adapter.requests[-1]
    assert last.tool_choice == ToolChoice.NONE
    assert last.tools  # Toolの定義は残す（prompt cacheを保つ）
    assert ConversationMessage.objects.filter(run=run, role=MessageRole.AGENT).exists()


@pytest.mark.django_db
def test_empty_reply_is_nudged(conversation):
    run = start(conversation)
    adapter = FakeAdapter([reply(""), reply("答えです。")])
    run = execute(run, adapter)
    assert run.status == RunStatus.COMPLETED
    assert "empty" in adapter.requests[1].messages[-1].text


@pytest.mark.django_db
def test_max_tokens_reply_is_nudged(conversation):
    run = start(conversation)
    adapter = FakeAdapter([reply("途中まで", stop=StopReason.MAX_TOKENS), reply("続きです。")])
    run = execute(run, adapter)
    assert run.status == RunStatus.COMPLETED
    assert "cut off" in adapter.requests[1].messages[-1].text


@pytest.mark.django_db
def test_compaction_replaces_the_older_part(conversation, monkeypatch):
    run = start(conversation, body="長い依頼です" * 10)
    todos = {"todos": [{"content": "読む", "status": "in_progress"}]}
    overflow = ProviderError("prompt is too long", context_overflow=True)
    script = [calls((f"c{i}", "todo_write", todos), text=f"作業{i}" * 20) for i in range(4)]
    adapter = FakeAdapter([*script, overflow, reply("- 目的：請求書の確認"), reply("完了しました。")])
    run = execute(run, adapter)

    assert run.status == RunStatus.COMPLETED
    compaction = RunEvent.objects.get(run=run, kind=RunEventKind.COMPACTION)
    assert "<compacted-summary>" in compaction.data["text"]
    assert "長い依頼です" in compaction.data["text"]  # 利用者の発言は原文のまま残す
    summary_request = adapter.requests[5]
    assert summary_request.tool_choice == ToolChoice.NONE
    final_messages = adapter.requests[-1].messages
    assert any("<compacted-summary>" in m.text for m in final_messages)
    assert sum(1 for m in final_messages if m.tool_calls) < 4


@pytest.mark.django_db
def test_provider_state_is_replayed_only_for_the_same_model(conversation):
    run = start(conversation)
    todos = {"todos": [{"content": "読む", "status": "pending"}]}
    first = calls(("c1", "todo_write", todos))
    first.insert(0, reply("", state={"blocks": ["thinking"]})[0])
    adapter = FakeAdapter([first, reply("完了")])
    run = execute(run, adapter)
    assistant = next(m for m in adapter.requests[1].messages if m.tool_calls)
    assert assistant.provider_state == {"blocks": ["thinking"]}


@pytest.mark.django_db
def test_lost_lease_writes_nothing(conversation):
    run = start(conversation)
    attempt = claim_run(run.pk)
    assert attempt is not None
    # 別のAttemptが始まった（Reconcilerが復旧した）後に、古いAttemptが応答を書こうとする
    from run.models import ExecutionAttempt  # noqa: PLC0415

    ExecutionAttempt.start(Run.objects.get(pk=run.pk))
    Runner(attempt, adapter_factory=FakeAdapter([reply("古い応答")]), sleep=lambda _s: None).run()
    assert not ConversationMessage.objects.filter(run=run, role=MessageRole.AGENT).exists()
    assert Run.objects.get(pk=run.pk).status == RunStatus.RUNNING


@pytest.mark.django_db
def test_instruction_during_the_final_reply_continues_the_run(conversation, app_user):
    run = start(conversation)

    class LateInstructionAdapter(FakeAdapter):
        def stream(self, request):
            if len(self.requests) == 0:
                post_message(
                    conversation, body="やっぱり支店別に", client_message_id="", mode=MessageMode.AUTO, actor=app_user
                )
            yield from super().stream(request)

    adapter = LateInstructionAdapter([reply("完了しました。"), reply("支店別にしました。")])
    run = execute(run, adapter)
    assert run.status == RunStatus.COMPLETED
    assert UserInstruction.objects.get(conversation=conversation).state == InstructionState.APPLIED
    assert len(adapter.requests) == 2
    assert any("やっぱり支店別に" in m.text for m in adapter.requests[1].messages)


@pytest.mark.django_db
def test_unapplied_instruction_becomes_the_next_request_when_the_run_ends(conversation, app_user):
    run = start(conversation)
    claim_run(run.pk)
    post_message(conversation, body="追加でこれも", client_message_id="", mode=MessageMode.AUTO, actor=app_user)
    Run.objects.get(pk=run.pk).request_cancel("", actor=app_user)
    run.refresh_from_db()
    run.set_status(RunStatus.CANCELLED)
    instruction = UserInstruction.objects.get(conversation=conversation)
    assert instruction.state == InstructionState.STARTED
    assert instruction.run is not None and instruction.run != run


@pytest.mark.django_db
def test_orphaned_question_is_closed_on_resume(conversation):
    run = start(conversation)
    attempt = claim_run(run.pk)
    assert attempt is not None
    ClarificationRequest.objects.create(run=run, kind=ClarificationKind.QUESTION, tool_call_id="old", questions=[])
    Runner(attempt, adapter_factory=FakeAdapter([reply("完了")]), sleep=lambda _s: None).run()
    assert ClarificationRequest.objects.get(run=run).status == ClarificationStatus.CANCELLED
