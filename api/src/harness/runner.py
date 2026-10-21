"""エージェントの実行のループ（harness.md §4）。

1ステップは「区切りの確認 → 文脈の組立 → モデルの呼出し → 要求されたToolの実行 → 結果の記録」だけで、業務の
振る舞いはToolとSkillで与える。書込みはすべて `Run.fenced()` の中で行い、実行の権利を失ったAttemptは何も書かない。
"""

import contextlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from django.utils import timezone
from django.utils.translation import gettext as _

from conversation.models import (
    ConversationMessage,
    InstructionKind,
    InstructionState,
    MessageRole,
    UserInstruction,
)
from llm.providers import get_adapter
from llm.providers.base import (
    ChatRequest,
    Completed,
    Message,
    ProviderAdapter,
    ProviderError,
    ProviderState,
    StopReason,
    TextDelta,
    ToolCall,
    ToolCallRequested,
    ToolChoice,
    Usage,
)
from run.errors import LeaseLost
from run.models import (
    ExecutionAttempt,
    Run,
    RunEndReason,
    RunEvent,
    RunEventKind,
    RunPrompt,
    RunStatus,
    ToolInvocation,
    ToolStatus,
)
from stream.notifier import notify_delta
from usage.models import UsageLedger, UsagePurpose

from . import compaction
from .context import ADDITIONAL_INSTRUCTION_PREFIX, build_items, parts_to_json, pending_tool_calls, provider_key
from .context import runtime_context as build_runtime_context
from .prompts import system_prompt
from .recovery import request_model_switch
from .tools import ALL_TOOLS
from .tools.base import Effect, Tool, ToolContext, ToolResult

logger = logging.getLogger(__name__)

MAX_STEPS = 200
# プロバイダの障害の再試行の間隔（秒）。1秒から倍にして最大30秒（harness.md §10.8）
RETRY_DELAYS = (1, 2, 4, 8, 16)
# LLM出力の差分をまとめて送る間隔（秒。realtime.md §4.1）
DELTA_INTERVAL = 0.15
# モデルの呼出しの間にleaseを延ばす間隔（秒）
HEARTBEAT_INTERVAL = 20
# 同じ呼出し・同じエラーを繰り返したときに、注意を添える回数と、止める回数（harness.md §11.3）
REPEAT_WARN, REPEAT_STOP = 3, 5
# 応答の異常（空、出力の上限で切れた）を許す回数
MAX_NUDGES = 3

REPEAT_NOTICE = (
    "\n\n[You are repeating the same tool call with identical arguments. Check the previous result and try a "
    "different approach or different arguments.]"
)
SAME_ERROR_NOTICE = (
    "\n\n[Repeating the same call will not fix this error. Correct the arguments or change the approach.]"
)
STEP_LIMIT_NOTICE = (
    "The maximum number of steps for this request has been reached. Tools are now disabled. Reply with text only: "
    "summarize what you have done, what remains, and what the user should do next."
)


class ContextOverflow(Exception):
    pass


class RepeatedErrors(Exception):
    """同じ呼出し・同じエラーを繰り返した。Runを失敗にする"""


@dataclass
class ModelResponse:
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    provider_state: dict[str, Any] | None = None
    usage: Usage | None = None
    completed: Completed | None = None


class Runner:
    def __init__(
        self,
        attempt: ExecutionAttempt,
        *,
        adapter_factory: Callable[..., ProviderAdapter] = get_adapter,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.attempt = attempt
        self.run_obj: Run = Run.objects.select_related(
            "conversation", "owner", "model_version__connection", "model_version__profile"
        ).get(pk=attempt.run_id)
        self.adapter_factory = adapter_factory
        self.sleep = sleep
        self.tools: tuple[Tool, ...] = ()
        self.nudges = 0
        self.compacted_for_overflow = False
        self.repeats: dict[str, int] = {}
        self.last_call_key = ""
        self.last_error_key = ""
        self.error_repeats = 0

    # --- 入口 ---

    def run(self) -> None:
        try:
            self._resume()
            while self._step():
                pass
        except LeaseLost:
            logger.info("run %s: attempt %s lost the lease", self.run_obj.uid, self.attempt.number)
        except RepeatedErrors:
            self._fail(RunEndReason.REPEATED_ERRORS, _("同じ操作を繰り返していて、作業が進みませんでした。"))
        except Exception:
            logger.exception("run %s: unexpected error", self.run_obj.uid)
            with contextlib.suppress(LeaseLost):
                self._fail(RunEndReason.INTERNAL, _("内部のエラーで実行を続けられませんでした。"))
        finally:
            self.attempt.end()

    # --- 記録 ---

    def _record(self, kind: RunEventKind, data: dict[str, Any], comment: str = "") -> RunEvent:
        with self.run_obj.fenced(self.attempt) as run:
            return RunEvent.record(run, kind, data, comment=comment)

    def _events(self) -> list[RunEvent]:
        return list(RunEvent.objects.filter(run=self.run_obj).order_by("sequence"))

    def _refresh(self) -> Run:
        run = Run.objects.select_related("model_version__connection", "model_version__profile").get(pk=self.run_obj.pk)
        self.run_obj.status = run.status
        self.run_obj.model_version = run.model_version
        self.run_obj.effort = run.effort
        self.run_obj.step_count = run.step_count
        return self.run_obj

    # --- 再開 ---

    def _resume(self) -> None:
        """結果のないTool呼出しを閉じ、実行時の文脈が変わっていれば追記する（harness.md §11.1）"""
        events = self._events()
        for call in pending_tool_calls(events):
            text = (
                "The previous attempt was interrupted while this tool call was running, and its result was not "
                "recorded. Its outcome is unknown. If the operation only reads data or is safe to repeat, call it "
                "again. If it may have changed files or external systems, check the current state first. "
                "Do not retry blindly."
            )
            self._record_tool_result(call["id"], call["name"], ToolResult.error(text), ToolStatus.UNKNOWN)
        user = self.run_obj.owner
        context = build_runtime_context(self.run_obj, user_name=user.display_name or user.email)
        last = next((e for e in reversed(events) if e.kind == RunEventKind.CONTEXT), None)
        if last is None or last.data.get("text") != context:
            self._record(RunEventKind.CONTEXT, {"text": context})

    # --- ステップ ---

    def _step(self) -> bool:
        """1ステップを進める。続けるならTrue"""
        if not self._boundary():
            return False
        run = self.run_obj
        version = run.model_version
        final = run.step_count >= MAX_STEPS
        if final:
            self._record(RunEventKind.CONTEXT, {"text": STEP_LIMIT_NOTICE})

        self.tools = self._exposed_tools()
        system = system_prompt(self.tools)
        prompt = RunPrompt.store(system, [self._tool_json(t) for t in self.tools])
        events = self._events()
        self._record_header(prompt, events)
        items = build_items(run, events)

        if compaction.estimate_tokens(events) > compaction.threshold(version):
            return self._compact(system, items)

        result = self._ask_model(system, items, final=final)
        if isinstance(result, bool):
            return result
        self._record_response(result, UsagePurpose.STEP)
        if final:
            self._finish_with_reply(result.text, RunStatus.FAILED, RunEndReason.STEP_LIMIT)
            return False
        if not result.calls:
            return self._handle_reply(result)
        return self._execute_calls(result.calls)

    def _ask_model(self, system: str, items, *, final: bool) -> ModelResponse | bool:
        """モデルを呼ぶ。応答が得られなければ、ステップを続けるかどうか（圧縮した・待ちや失敗にした）を返す"""
        run = self.run_obj
        version = run.model_version
        request = ChatRequest(
            model=version.model_name,
            messages=[Message("system", system), *(i.message for i in items)],
            tools=[t.definition() for t in self.tools],
            max_output_tokens=version.max_output_tokens,
            effort=run.effort or None,
            tool_choice=ToolChoice.NONE if final else ToolChoice.AUTO,
            use_cache=version.supports_prompt_caching,
        )
        try:
            response = self._call_model(request, run.step_count + 1)
        except ContextOverflow:
            if self.compacted_for_overflow:
                self._fail(RunEndReason.CONTEXT, _("会話が長すぎて、モデルの文脈に収まりません。"))
                return False
            self.compacted_for_overflow = True
            return self._compact(system, items)
        return False if response is None else response

    def _boundary(self) -> bool:
        """安全な区切り：leaseを延ばし、停止の依頼を確かめ、追加指示を取り込む（harness.md §4.2）"""
        self.attempt.heartbeat()
        run = self._refresh()
        if run.status == RunStatus.CANCELLING:
            self._finish_cancelled()
            return False
        if run.status != RunStatus.RUNNING:
            raise LeaseLost(f"run={run.uid} status={run.status}")
        self._apply_instructions()
        return True

    def _apply_instructions(self) -> bool:
        """届いている追加指示を、受付順に利用者の発言として取り込む。取り込んだらTrue"""
        applied = False
        with self.run_obj.fenced(self.attempt) as run:
            instructions = (
                UserInstruction.objects.select_for_update(of=("self",))
                .filter(run=run, kind=InstructionKind.CURRENT, state=InstructionState.RECEIVED)
                .select_related("message")
                .order_by("pk")
            )
            for instruction in instructions:
                text = f"{ADDITIONAL_INSTRUCTION_PREFIX}\n{instruction.message.body}"
                RunEvent.record(run, RunEventKind.USER_MESSAGE, {"text": text, "message": instruction.message.uid})
                instruction.state = InstructionState.APPLIED
                instruction.applied = timezone.now()
                instruction.save(update_fields=["state", "applied", "updated"])
                applied = True
        return applied

    def _exposed_tools(self) -> tuple[Tool, ...]:
        """Runに出すToolの一覧（harness.md §7.5）。Tool呼出しに対応しないモデルには出さない"""
        if not self.run_obj.model_version.supports_tools:
            return ()
        tools = ALL_TOOLS
        if not self.run_obj.is_interactive:
            tools = tuple(t for t in tools if t.name != "ask_user")
        return tools

    @staticmethod
    def _tool_json(tool: Tool) -> dict[str, Any]:
        return {"name": tool.name, "description": tool.description, "parameters": tool.parameters}

    def _record_header(self, prompt: RunPrompt, events: list[RunEvent]) -> None:
        header = {"prompt": prompt.hash, "model_version": self.run_obj.model_version_id, "effort": self.run_obj.effort}
        last = next((e for e in reversed(events) if e.kind == RunEventKind.REQUEST_HEADER), None)
        if last is None or last.data != header:
            self._record(RunEventKind.REQUEST_HEADER, header)

    # --- モデルの呼出し ---

    def _call_model(self, request: ChatRequest, step: int) -> ModelResponse | None:
        """同じモデルで間をおいて再試行する。続かなければ、会話では切り替えを確かめ、タスクでは失敗にする"""
        adapter = self.adapter_factory(self.run_obj.model_version.connection)
        error: ProviderError | None = None
        for attempt, delay in enumerate((*RETRY_DELAYS, None)):
            try:
                return self._stream(adapter, request, step)
            except ProviderError as exc:
                error = exc
                if exc.context_overflow:
                    raise ContextOverflow() from exc
                if not exc.retryable or delay is None:
                    break
                # 待つ前に記録する（DeepSeek Harnessの方式）。待つ間もleaseを延ばす
                self._record(
                    RunEventKind.LLM_RETRY,
                    {"attempt": attempt + 1, "delay": delay, "error": exc.message, "status_code": exc.status_code},
                )
                self._wait(delay)
        assert error is not None
        self._handle_provider_failure(error)
        return None

    def _stream(self, adapter: ProviderAdapter, request: ChatRequest, step: int) -> ModelResponse:
        response = ModelResponse()
        pending, last_flush, last_beat = "", time.monotonic(), time.monotonic()
        try:
            for event in adapter.stream(request):
                match event:
                    case TextDelta(text=text):
                        response.text += text
                        pending += text
                    case ToolCallRequested(call=call):
                        response.calls.append(call)
                    case ProviderState(data=data):
                        response.provider_state = data
                    case Usage():
                        response.usage = event
                    case Completed():
                        response.completed = event
                now = time.monotonic()
                if pending and now - last_flush >= DELTA_INTERVAL:
                    notify_delta(self.run_obj.uid, step, pending)
                    pending, last_flush = "", now
                if now - last_beat >= HEARTBEAT_INTERVAL:
                    self.attempt.heartbeat()
                    last_beat = now
        except ProviderError:
            if response.text or response.calls:
                # 途中まで届いた応答は、モデルに見せる履歴には入れない（harness.md §11.2）
                self._record(RunEventKind.ASSISTANT_ATTEMPT, {"step": step, "text": response.text})
            raise
        if pending:
            notify_delta(self.run_obj.uid, step, pending)
        return response

    def _wait(self, seconds: float) -> None:
        remaining = seconds
        while remaining > 0:
            chunk = min(remaining, HEARTBEAT_INTERVAL)
            self.sleep(chunk)
            remaining -= chunk
            self.attempt.heartbeat()

    def _handle_provider_failure(self, error: ProviderError) -> None:
        if not (error.retryable and self.run_obj.is_interactive):
            self._fail(RunEndReason.PROVIDER, error.message)
            return
        with self.run_obj.fenced(self.attempt) as run:
            request_model_switch(run, error.message)
            run.set_status(RunStatus.WAITING_USER, comment=_("モデルの切り替えを確認しています。"))

    def _record_response(self, response: ModelResponse, purpose: UsagePurpose) -> None:
        completed = response.completed or Completed(StopReason.OTHER)
        usage = response.usage or Usage(0, 0)
        data = {
            "step": self.run_obj.step_count + 1,
            "text": response.text,
            "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in response.calls],
            "provider": provider_key(self.run_obj),
            "provider_state": response.provider_state,
            "usage": {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cached_input_tokens": usage.cached_input_tokens,
                "reasoning_tokens": usage.reasoning_tokens,
            },
            "stop_reason": completed.stop_reason,
            "request_id": completed.request_id,
        }
        with self.run_obj.fenced(self.attempt) as run:
            RunEvent.record(run, RunEventKind.ASSISTANT_MESSAGE, data)
            Run.objects.filter(pk=run.pk).update(step_count=run.step_count + 1, updated=timezone.now())
            self.run_obj.step_count = run.step_count + 1
            UsageLedger.record(
                run=run,
                model_version=self.run_obj.model_version,
                purpose=purpose,
                usage=usage,
                request_id=completed.request_id,
            )

    # --- 最後の応答 ---

    def _handle_reply(self, response: ModelResponse) -> bool:
        stop = response.completed.stop_reason if response.completed else StopReason.OTHER
        if stop == StopReason.MAX_TOKENS:
            return self._nudge("Your previous response was cut off at the output limit. Continue in smaller pieces.")
        if not response.text.strip():
            return self._nudge("Your previous response was empty. Call a tool or write your reply to the user.")
        self._finish_with_reply(response.text, RunStatus.COMPLETED, RunEndReason.COMPLETED)
        return False

    def _nudge(self, text: str) -> bool:
        self.nudges += 1
        if self.nudges > MAX_NUDGES:
            self._fail(RunEndReason.REPEATED_ERRORS, _("モデルの応答が繰り返し不完全でした。"))
            return False
        self._record(RunEventKind.CONTEXT, {"text": text})
        return True

    def _finish_with_reply(self, text: str, status: RunStatus, end_reason: RunEndReason) -> None:
        with self.run_obj.fenced(self.attempt) as run:
            if text.strip():
                ConversationMessage.objects.create(
                    conversation_id=run.conversation_id, run=run, role=MessageRole.AGENT, body=text
                )
                type(run.conversation).objects.filter(pk=run.conversation_id).update(last_posted=timezone.now())
            error = "" if status == RunStatus.COMPLETED else str(end_reason.label)
            run.set_status(status, end_reason=end_reason, error=error)

    def _finish_cancelled(self) -> None:
        with self.run_obj.fenced(self.attempt) as run:
            ToolInvocation.objects.filter(run=run, status__in=(ToolStatus.REQUESTED, ToolStatus.RUNNING)).update(
                status=ToolStatus.CANCELLED, ended=timezone.now()
            )
            run.set_status(RunStatus.CANCELLED, comment=_("停止しました。"), end_reason=RunEndReason.CANCELLED)

    def _fail(self, end_reason: RunEndReason, message: str) -> None:
        with self.run_obj.fenced(self.attempt) as run:
            if run.status == RunStatus.CANCELLING:
                run.set_status(RunStatus.CANCELLED, end_reason=RunEndReason.CANCELLED)
            else:
                run.set_status(RunStatus.FAILED, end_reason=end_reason, error=message)

    # --- Tool ---

    def _execute_calls(self, calls: list[ToolCall]) -> bool:
        """モデルが返した順に実行する。呼出しと呼出しの間も安全な区切りとして確かめる（harness.md §4.2）"""
        tools = {t.name: t for t in self.tools}
        for index, call in enumerate(calls):
            rest = calls[index + 1 :]
            if index > 0:
                interrupted = self._between_calls(calls[index:])
                if interrupted is not None:
                    return interrupted
            tool = tools.get(call.name)
            if tool is None:
                self._record_tool_result(call.id, call.name, ToolResult.error(_unknown_tool(call.name, tools)))
                continue
            if tool.alone and len(calls) > 1 and index < len(calls) - 1:
                result = self._run_tool(tool, call)
                if result.waits_for_user:
                    return self._wait_for_user(call, rest)
                self._close_calls(rest, "Not executed: this tool must be called alone. Call the others again.")
                return True
            result = self._run_tool(tool, call)
            if result.waits_for_user:
                return self._wait_for_user(call, rest)
        return True

    def _between_calls(self, remaining: list[ToolCall]) -> bool | None:
        """取消・追加指示が届いていれば、残りの呼出しを閉じる。続けてよければNone"""
        self.attempt.heartbeat()
        run = self._refresh()
        if run.status == RunStatus.CANCELLING:
            self._close_calls(remaining, "Not executed: the user cancelled the work.")
            self._finish_cancelled()
            return False
        pending = UserInstruction.objects.filter(
            run=run, kind=InstructionKind.CURRENT, state=InstructionState.RECEIVED
        ).exists()
        if pending:
            tools = {t.name: t for t in self.tools}
            writes = [c for c in remaining if (t := tools.get(c.name)) is None or t.effect != Effect.READ]
            reads = [c for c in remaining if c not in writes]
            for read in reads:
                tool = tools[read.name]
                self._run_tool(tool, read)
            self._close_calls(
                writes, "Not executed: a new instruction from the user arrived. Read it, then call again if needed."
            )
            return True
        return None

    def _run_tool(self, tool: Tool, call: ToolCall) -> ToolResult:
        problem = tool.validate(call.arguments)
        if problem:
            result = ToolResult.error(problem)
            self._record_tool_result(call.id, call.name, self._with_repeat_notice(call, result), ToolStatus.FAILED)
            return result
        with self.run_obj.fenced(self.attempt) as run:
            invocation = ToolInvocation.objects.create(
                run=run,
                attempt=self.attempt,
                call_id=call.id,
                name=call.name,
                arguments=call.arguments,
                status=ToolStatus.RUNNING,
            )
        try:
            result = tool.execute(call.arguments, ToolContext(self.run_obj, self.attempt, call))
        except LeaseLost:
            raise
        except Exception as exc:
            logger.exception("run %s: tool %s failed", self.run_obj.uid, call.name)
            result = ToolResult.error(f"The tool failed: {type(exc).__name__}. Try a different approach.")
        if result.waits_for_user:
            ToolInvocation.objects.filter(pk=invocation.pk).update(value=result.value)
            return result
        status = ToolStatus.FAILED if result.is_error else ToolStatus.SUCCEEDED
        self._record_tool_result(call.id, call.name, self._with_repeat_notice(call, result), status, invocation)
        return result

    def _with_repeat_notice(self, call: ToolCall, result: ToolResult) -> ToolResult:
        """同じ呼出し・同じエラーの繰返しに注意を添え、続けば止める（harness.md §11.3）"""
        key = f"{call.name}:{json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)}"
        self.repeats[key] = self.repeats.get(key, 0) + 1 if key == self.last_call_key else 1
        self.last_call_key = key
        error_key = f"{call.name}:{result.content}" if result.is_error else ""
        self.error_repeats = self.error_repeats + 1 if error_key and error_key == self.last_error_key else 1
        self.last_error_key = error_key
        if self.repeats[key] >= REPEAT_STOP or (error_key and self.error_repeats >= REPEAT_STOP):
            raise RepeatedErrors()
        notice = ""
        if self.repeats[key] >= REPEAT_WARN:
            notice = REPEAT_NOTICE
        elif error_key and self.error_repeats >= REPEAT_WARN:
            notice = SAME_ERROR_NOTICE
        if not notice:
            return result
        text = "".join(getattr(p, "text", "") for p in result.content) + notice
        return ToolResult.text(text, is_error=result.is_error, value=result.value, meta=result.meta)

    def _record_tool_result(
        self,
        call_id: str,
        name: str,
        result: ToolResult,
        status: ToolStatus = ToolStatus.FAILED,
        invocation: ToolInvocation | None = None,
    ) -> None:
        data = {
            "call_id": call_id,
            "name": name,
            "content": parts_to_json(result.content),
            "is_error": result.is_error,
            "value": result.value,
            "meta": result.meta,
        }
        with self.run_obj.fenced(self.attempt) as run:
            RunEvent.record(run, RunEventKind.TOOL_RESULT, data)
            if invocation is not None:
                ToolInvocation.objects.filter(pk=invocation.pk).update(
                    status=status, value=result.value, ended=timezone.now()
                )
            elif status == ToolStatus.UNKNOWN:
                ToolInvocation.objects.filter(run=run, call_id=call_id).update(status=status, ended=timezone.now())

    def _close_calls(self, calls: list[ToolCall], text: str) -> None:
        for call in calls:
            self._record_tool_result(call.id, call.name, ToolResult.error(text), ToolStatus.CANCELLED)

    def _wait_for_user(self, call: ToolCall, rest: list[ToolCall]) -> bool:
        """ask_user の結果は記録せずにWAITING_USERにし、Attemptを終える（harness.md §10.3）"""
        self._close_calls(rest, "Not executed: waiting for the user's answer. Call again after the answer if needed.")
        invocation = ToolInvocation.objects.get(run=self.run_obj, call_id=call.id)
        clarification_uid = (invocation.value or {}).get("clarification")
        with self.run_obj.fenced(self.attempt) as run:
            from run.models import ClarificationRequest  # noqa: PLC0415  ここでだけ使う

            clarification = ClarificationRequest.objects.get(uid=clarification_uid)
            RunEvent.record(
                run,
                RunEventKind.CLARIFICATION_REQUESTED,
                {"clarification": clarification.uid, "kind": clarification.kind, "questions": clarification.questions},
            )
            run.set_status(RunStatus.WAITING_USER, comment=_("確認をお願いしています。"))
        return False

    # --- 圧縮 ---

    def _compact(self, system: str, items) -> bool:
        chosen = compaction.choose_range(items)
        if chosen is None:
            self._fail(RunEndReason.CONTEXT, _("会話が長すぎて、モデルの文脈に収まりません。"))
            return False
        history = [i.message for i in items if chosen.start <= i.sequence <= chosen.end]
        version = self.run_obj.model_version
        request = ChatRequest(
            model=version.model_name,
            messages=compaction.summary_request_messages(system, history),
            tools=[t.definition() for t in self.tools],
            max_output_tokens=min(version.max_output_tokens, 8_000),
            effort=self.run_obj.effort or None,
            tool_choice=ToolChoice.NONE,
            use_cache=version.supports_prompt_caching,
        )
        try:
            response = self._stream(self.adapter_factory(version.connection), request, self.run_obj.step_count)
        except ProviderError as exc:
            self._fail(RunEndReason.CONTEXT, exc.message)
            return False
        if not response.text.strip():
            self._fail(RunEndReason.CONTEXT, _("会話の要約に失敗しました。"))
            return False
        with self.run_obj.fenced(self.attempt) as run:
            RunEvent.record(
                run,
                RunEventKind.COMPACTION,
                {
                    "from": chosen.start,
                    "to": chosen.end,
                    "text": compaction.summary_text(response.text, chosen.user_texts),
                },
            )
            UsageLedger.record(
                run=run,
                model_version=version,
                purpose=UsagePurpose.COMPACTION,
                usage=response.usage or Usage(0, 0),
                request_id=response.completed.request_id if response.completed else "",
            )
        return True


def _unknown_tool(name: str, tools: dict[str, Tool]) -> str:
    import difflib  # noqa: PLC0415  知らないToolの名前のときだけ使う

    close = difflib.get_close_matches(name, list(tools), n=3)
    hint = f" Did you mean: {', '.join(close)}?" if close else ""
    return f'No tool named "{name}" is available.{hint} Use a tool from the available tool list.'
