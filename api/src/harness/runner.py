"""エージェントの実行のループ（harness.md §4）。

1ステップは「区切りの確認 → 文脈の組立 → モデルの呼出し → 要求されたToolの実行 → 結果の記録」だけで、業務の
振る舞いはToolとSkillで与える。書込みはすべて `Run.fenced()` の中で行い、実行の権利を失ったAttemptは何も書かない。
"""

import contextlib
import json
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from typing import Any

from django.db.models import Max
from django.utils import timezone
from django.utils.translation import gettext as _

from artifact.models import Artifact, BlobObject
from conversation.dispatch import user_message_data
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
    TextPart,
    ToolCall,
    ToolCallRequested,
    ToolChoice,
    Usage,
)
from run.dispatch import enqueue_run
from run.errors import LeaseLost
from run.models import (
    MAX_ATTEMPTS,
    Checkpoint,
    ClarificationKind,
    ClarificationRequest,
    ClarificationStatus,
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
from run.tasks import CLARIFICATION_TTL
from runtime.launcher import SandboxUnavailable
from stream.notifier import notify_delta
from usage.models import UsageLedger, UsagePurpose

from . import compaction
from .context import (
    ADDITIONAL_INSTRUCTION_PREFIX,
    RUNTIME_CONTEXT_TAG,
    Item,
    build_items,
    inherited_items,
    parts_to_json,
    pending_tool_calls,
    provider_key,
)
from .context import runtime_context as build_runtime_context
from .prompts import system_prompt
from .recovery import request_model_switch
from .sandbox import OUT_OF_MEMORY_FAILURE, OUT_OF_MEMORY_NOTICE, SandboxSession
from .skills import explicit_versions, revoked_skill
from .tools import ALL_TOOLS, SANDBOX_TOOLS, WORKSPACE_WRITING_TOOLS
from .tools.base import Effect, Tool, ToolContext, ToolResult
from .tools.skill_tool import make_skill_tool
from .tools.submit_result import NAME as SUBMIT_RESULT
from .tools.submit_result import make_submit_result_tool

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
# 並べて実行するToolの数の上限（harness.md §7.6）
MAX_PARALLEL = 4

REPEAT_NOTICE = (
    "\n\n[You are repeating the same tool call with identical arguments. Check the previous result and try a "
    "different approach or different arguments.]"
)
SAME_ERROR_NOTICE = (
    "\n\n[Repeating the same call will not fix this error. Correct the arguments or change the approach.]"
)
RUNNING_JOBS_NOTICE = (
    "Background jobs are still running: {jobs}. Before you finish, wait for them (job_output with wait) or stop "
    "them (job_kill). Jobs are stopped when the work ends."
)
RESULT_REQUIRED_NOTICE = (
    "This work must end with submit_result. Check the result, call submit_result with it, and then write your final "
    "reply."
)
RESULT_RESET_NOTICE = (
    "The result you submitted was cleared because the user gave a new instruction. Apply it, then call "
    "submit_result again with the updated result."
)
RESULT_GRACE_NOTICE = (
    "The maximum number of steps is near: this is the last step in which you can call tools. Call submit_result "
    "now with the result you have, and note anything unfinished in it if the schema allows."
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
        session_factory: Callable[..., SandboxSession] = SandboxSession,
    ):
        self.attempt = attempt
        self.run_obj: Run = Run.objects.select_related(
            "conversation", "owner", "model_version__connection", "model_version__profile", "runtime_version"
        ).get(pk=attempt.run_id)
        self.adapter_factory = adapter_factory
        self.sleep = sleep
        self.session = session_factory(self.run_obj, attempt, heartbeat=attempt.heartbeat)
        # このAttemptを始めたときのRunEventの最後のsequence。読んだファイルの版の記録を選ぶのに使う
        self.start_sequence = 0
        self.warned_running_jobs = False
        self.tools: tuple[Tool, ...] = ()
        # Skillで決まるTool（Run開始時に固定したManifestから作る。Runの中では変わらない）
        self.skill_tools: tuple[Tool, ...] | None = None
        self.nudges = 0
        self.compacted_for_overflow = False
        self.repeats: dict[str, int] = {}
        self.last_call_key = ""
        self.last_error_key = ""
        self.error_repeats = 0
        # 同じ会話の前の依頼から引き継ぐ文脈（親のRunは終わっていて変わらないので、最初に1回だけ読む）
        self.inherited: list[Item] = []

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
        except SandboxUnavailable as exc:
            logger.warning("run %s: sandbox unavailable: %s", self.run_obj.uid, exc)
            with contextlib.suppress(LeaseLost):
                self._sandbox_failure(exc)
        except Exception:
            logger.exception("run %s: unexpected error", self.run_obj.uid)
            with contextlib.suppress(LeaseLost):
                self._fail(RunEndReason.INTERNAL, _("内部のエラーで実行を続けられませんでした。"))
        finally:
            self.session.close()
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
        self.run_obj.manifest = run.manifest
        return self.run_obj

    # --- 再開 ---

    def _resume(self) -> None:
        """結果のないTool呼出しを閉じ、Workspaceの食い違いと実行時の文脈の変化を追記する（harness.md §11.1）"""
        events = self._events()
        self.start_sequence = events[-1].sequence if events else 0
        self.inherited = inherited_items(self.run_obj)
        self.tools = self._exposed_tools()
        tools = {t.name: t for t in self.tools}
        # 実行中のRunに回答待ちの確認が残っていれば、前のAttemptが待ちに移る前に止まったもの。答えられないので閉じる
        ClarificationRequest.objects.filter(
            run=self.run_obj, kind=ClarificationKind.QUESTION, status=ClarificationStatus.OPEN
        ).update(status=ClarificationStatus.CANCELLED)
        for call in pending_tool_calls(events):
            tool = tools.get(call["name"])
            if tool is not None and tool.replay_safe:
                # 読むだけのToolは、同じ引数で実行し直す
                self._run_tool(tool, ToolCall(call["id"], call["name"], call["arguments"]))
                continue
            text = (
                "The previous attempt was interrupted while this tool call was running, and its result was not "
                "recorded. Its outcome is unknown. If the operation only reads data or is safe to repeat, call it "
                "again. If it may have changed files or external systems, check the current state first. "
                "Do not retry blindly."
            )
            self._record_tool_result(call["id"], call["name"], ToolResult.error(text), ToolStatus.UNKNOWN)
        self._out_of_memory_notice()
        self._workspace_notices(self._events())
        user = self.run_obj.owner
        context = build_runtime_context(self.run_obj, user_name=user.display_name or user.email)
        last = next(
            (
                e
                for e in reversed(events)
                if e.kind == RunEventKind.CONTEXT and e.data.get("text", "").startswith(RUNTIME_CONTEXT_TAG)
            ),
            None,
        )
        if last is None or last.data.get("text") != context:
            self._record(RunEventKind.CONTEXT, {"text": context})

    def _out_of_memory_notice(self) -> None:
        """前のAttemptのSandboxがメモリの上限を超えて止まったなら、モデルに知らせる（同じことを繰り返させない）"""
        previous = self.run_obj.attempts.filter(number__lt=self.attempt.number).order_by("-number").first()
        version = self.run_obj.runtime_version
        if previous is None or version is None or not previous.failure.startswith(OUT_OF_MEMORY_FAILURE):
            return
        text = OUT_OF_MEMORY_NOTICE.format(memory_mb=version.memory_mb)
        self._record(RunEventKind.CONTEXT, {"text": text, "sandbox": "out_of_memory"})

    def _workspace_notices(self, events: list[RunEvent]) -> None:
        """新しいAttemptのWorkspaceは最後のCheckpointから戻る。それより後にファイルを変えたTool呼出しと、
        保存の前に止めたジョブを、モデルに知らせる（harness.md §8.3・§11.1）"""
        if self.run_obj.runtime_version_id is None:
            return
        checkpoint = Checkpoint.latest_for(self.run_obj)
        own = checkpoint if checkpoint is not None and checkpoint.run_id == self.run_obj.pk else None
        notices = [e for e in events if e.kind == RunEventKind.CONTEXT and e.data.get("workspace")]
        since = max([own.sequence if own else 0, *(e.sequence for e in notices if e.data["workspace"] == "reset")])
        changed = [
            f"{e.data['name']} ({e.data['call_id']})"
            for e in events
            if e.kind == RunEventKind.TOOL_RESULT
            and e.sequence > since
            and e.data.get("name") in WORKSPACE_WRITING_TOOLS
        ]
        # 退避した出力のファイルも、最後のCheckpointより後のものは消えている
        spilled = [
            path
            for e in events
            if e.kind == RunEventKind.TOOL_RESULT and e.sequence > since
            for path in (e.data.get("meta") or {}).get("spilled", [])
        ]
        if changed or spilled:
            # Sandboxは最初のSandboxのToolで起動して戻すので、ここでは戻せたかがまだ分からない。
            # 戻せなかったときは、その時点で知らせる（SandboxSession.notice）
            if own is not None:
                state = "go back to the last saved state of this work"
            elif checkpoint is not None:
                state = "go back to their state at the start of this request"
            else:
                state = "are empty again"
            parts = [
                f"The previous attempt was interrupted. The files in /workspace/work and /workspace/output {state}."
            ]
            if changed:
                parts.append(
                    "These later tool calls changed files, and their effects are not in the current files: "
                    f"{', '.join(changed[:20])}. Check the current files and redo what is needed. Read a file again "
                    "before you edit it."
                )
            if spilled:
                parts.append(
                    f"The saved full outputs of earlier tool calls are gone: {', '.join(spilled[:20])}. Call the tool "
                    "again if you need the full output."
                )
            text = "[" + " ".join(parts) + "]"
            self._record(RunEventKind.CONTEXT, {"text": text, "workspace": "reset"})
        if own is not None and own.stopped_jobs and not any(e.data.get("checkpoint") == own.uid for e in notices):
            jobs = ", ".join(f"{j['job_id']} (`{j['command']}`)" for j in own.stopped_jobs)
            text = (
                f"[These background jobs were stopped when the work paused, and they are not running now: {jobs}. "
                "Run them again if you still need them.]"
            )
            self._record(RunEventKind.CONTEXT, {"text": text, "workspace": "jobs_stopped", "checkpoint": own.uid})

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
        if run.step_count == MAX_STEPS - 1 and self._result_missing():
            # 上限の前に1回だけ、今ある結果で業務結果を確定する猶予を与える（harness.md §4.4）
            self._record(RunEventKind.CONTEXT, {"text": RESULT_GRACE_NOTICE})
        system = system_prompt(self.tools)
        prompt = RunPrompt.store(system, [self._tool_json(t) for t in self.tools])
        events = self._events()
        self._record_header(prompt, events)
        items = build_items(run, events, self.inherited)

        if compaction.estimate_tokens(events, items) > compaction.threshold(version):
            return self._compact(system, items)

        result = self._ask_model(system, items, final=final)
        if isinstance(result, bool):
            return result
        self._record_response(result, UsagePurpose.STEP, images_sent=compaction.images_sent(items))
        if final:
            return self._finish_at_step_limit(result.text)
        if not result.calls:
            return self._handle_reply(result)
        return self._execute_calls(result.calls)

    def _finish_at_step_limit(self, text: str) -> bool:
        """ステップ数の上限の最後のまとめの応答で終える。業務結果を確定していれば完了にする（その間に追加の指示が
        届いていれば、取り込んで続ける）"""
        if any(t.name == SUBMIT_RESULT for t in self.tools) and not self._result_missing():
            return self._finish_with_reply(text, RunStatus.COMPLETED, RunEndReason.COMPLETED)
        self._finish_with_reply(text, RunStatus.FAILED, RunEndReason.STEP_LIMIT)
        return False

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
            reasoning=version.supports_reasoning,
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
        if reason := revoked_skill(run):
            # 停止・権限の失効は、始めた後のRunでも次の区切りから使わせない（design.md §3.2）
            self._fail(RunEndReason.SKILL, reason)
            return False
        self._apply_instructions()
        self._record_finished_jobs()
        return True

    def _record_finished_jobs(self) -> None:
        """終わったバックグラウンドのジョブを job.finished として記録し、モデルに知らせる（harness.md §8.3）"""
        if not self.session.started:
            return
        for job in self.session.finished_unreported():
            job.reported = True
            if job.stopped == "timeout":
                status = f"was stopped because it exceeded the time limit of {job.timeout_seconds} seconds"
            else:
                status = f"finished with exit code {job.exit_code}"
            text = f"[Background job {job.job_id} ({job.description}) {status}. Read its output with job_output.]"
            if job.stopped == "timeout":
                comment = _("%(description)s（実行時間の上限で停止）") % {"description": job.description}
            else:
                comment = _("%(description)s（終了コード %(code)s）") % {
                    "description": job.description,
                    "code": job.exit_code,
                }
            self._record(
                RunEventKind.JOB_FINISHED,
                {"job_id": job.job_id, "exit_code": job.exit_code, "stopped": job.stopped, "text": text},
                comment=comment,
            )

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
                RunEvent.record(run, RunEventKind.USER_MESSAGE, user_message_data(instruction.message, text))
                instruction.state = InstructionState.APPLIED
                instruction.applied = timezone.now()
                instruction.save(update_fields=["state", "applied", "updated"])
                applied = True
            if applied and run.result is not None:
                # 確定した業務結果は、取り込んだ指示を反映していない。確定し直させる（harness.md §10.4・§10.5）
                Run.objects.filter(pk=run.pk).update(result=None, updated=timezone.now())
                self.run_obj.result = None
                RunEvent.record(run, RunEventKind.CONTEXT, {"text": RESULT_RESET_NOTICE, "result_reset": True})
        if applied and self.session.started:
            # 追加指示に付いた添付を、動いているSandboxの /workspace/input にも置く
            self.session.sync_inputs()
        return applied

    def _exposed_tools(self) -> tuple[Tool, ...]:
        """Runに出すToolの一覧（harness.md §7.5）。Tool呼出しに対応しないモデルには出さない。

        `/` でSkillを指定したRunでは、そのSkillの使ってよいToolに限り、業務結果のSchemaがあれば submit_result を
        加える。指定がなければ、利用者が使えるToolの全体と、Skillを読み込む `skill` を出す
        """
        if not self.run_obj.model_version.supports_tools:
            return ()
        tools = self._skill_tools()
        if self.run_obj.runtime_version_id is None:
            tools = tuple(t for t in tools if t not in SANDBOX_TOOLS)
        if not self.run_obj.is_interactive:
            tools = tuple(t for t in tools if t.name != "ask_user")
        return tools

    def _skill_tools(self) -> tuple[Tool, ...]:
        if self.skill_tools is not None:
            return self.skill_tools
        explicit = explicit_versions(self.run_obj)
        if explicit:
            allowed = {name for version in explicit for name in version.tools}
            tools = tuple(t for t in ALL_TOOLS if t.name in allowed)
            schema = next((v.output_schema for v in explicit if v.output_schema), None)
            if schema:
                tools += (make_submit_result_tool(schema),)
        else:
            tools = ALL_TOOLS
            catalog = ((self.run_obj.manifest or {}).get("fixed") or {}).get("skill_catalog") or {}
            if names := [e["name"] for e in catalog.get("skills", [])]:
                tools += (make_skill_tool(names),)
        self.skill_tools = tools
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
        self._checkpoint()
        with self.run_obj.fenced(self.attempt) as run:
            request_model_switch(run, error.message)
            run.set_status(RunStatus.WAITING_USER, comment=_("モデルの切り替えを確認しています。"))

    def _record_response(self, response: ModelResponse, purpose: UsagePurpose, *, images_sent: int = 0) -> None:
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
            # 要求で送った画像の数。次の要求のtoken数の見積もりに使う（compaction.estimate_tokens()）
            "images_sent": images_sent,
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
        running = self.session.running_jobs() if self.session.started else []
        if running and not self.warned_running_jobs:
            # 最後の応答の前に、動いているジョブを待つか止めるかを1回だけ決めさせる（harness.md §8.3）
            self.warned_running_jobs = True
            jobs = ", ".join(f"{j.job_id} ({j.description})" for j in running)
            self._record(RunEventKind.CONTEXT, {"text": RUNNING_JOBS_NOTICE.format(jobs=jobs)})
            return True
        if self._result_missing():
            # 業務結果のSchemaがあるのに submit_result を呼ばなかった。1回だけ促す（harness.md §10.5）。
            # 追加の指示で確定した結果を取り消した後は、もう一度促してよい
            notices = [e for e in self._events() if e.kind == RunEventKind.CONTEXT]
            since = max((e.sequence for e in notices if e.data.get("result_reset")), default=0)
            if not any(e.data.get("result_required") for e in notices if e.sequence > since):
                self._record(RunEventKind.CONTEXT, {"text": RESULT_REQUIRED_NOTICE, "result_required": True})
                return True
            return self._finish_with_reply(
                response.text,
                RunStatus.FAILED,
                RunEndReason.NO_RESULT,
                error=_("Skillが決めた形の業務結果を、エージェントが確定しませんでした。"),
            )
        # 最後の応答の間に追加指示が届いていれば、終えずに取り込んで続ける（harness.md §10.4）
        return self._finish_with_reply(response.text, RunStatus.COMPLETED, RunEndReason.COMPLETED)

    def _result_missing(self) -> bool:
        """業務結果のSchemaがあるRunで、まだ submit_result で確定していない"""
        return any(t.name == SUBMIT_RESULT for t in self.tools) and self.run_obj.result is None

    def _nudge(self, text: str) -> bool:
        self.nudges += 1
        if self.nudges > MAX_NUDGES:
            self._fail(RunEndReason.REPEATED_ERRORS, _("モデルの応答が繰り返し不完全でした。"))
            return False
        self._record(RunEventKind.CONTEXT, {"text": text})
        return True

    def _finish_with_reply(self, text: str, status: RunStatus, end_reason: RunEndReason, *, error: str = "") -> bool:
        """応答を会話に載せてRunを終える。完了の前に追加指示が届いていれば、終えずにTrue（続ける）を返す"""
        self._checkpoint()
        with self.run_obj.fenced(self.attempt) as run:
            if text.strip():
                ConversationMessage.objects.create(
                    conversation_id=run.conversation_id, run=run, role=MessageRole.AGENT, body=text
                )
                type(run.conversation).objects.filter(pk=run.conversation_id).update(last_posted=timezone.now())
            pending = UserInstruction.objects.filter(
                run=run, kind=InstructionKind.CURRENT, state=InstructionState.RECEIVED
            ).exists()
            if status == RunStatus.COMPLETED and pending:
                return True
            if status != RunStatus.COMPLETED:
                error = error or str(end_reason.label)
            run.set_status(status, end_reason=end_reason, error=error)
        return False

    def _finish_cancelled(self) -> None:
        # 停止を優先する。Workspaceを保存できなくても止める（design.md §7.9）
        self._checkpoint(required=False)
        with self.run_obj.fenced(self.attempt) as run:
            ToolInvocation.objects.filter(run=run, status__in=(ToolStatus.REQUESTED, ToolStatus.RUNNING)).update(
                status=ToolStatus.CANCELLED, ended=timezone.now()
            )
            run.set_status(RunStatus.CANCELLED, comment=_("停止しました。"), end_reason=RunEndReason.CANCELLED)

    def _fail(self, end_reason: RunEndReason, message: str) -> None:
        self._checkpoint(required=False)
        with self.run_obj.fenced(self.attempt) as run:
            if run.status == RunStatus.CANCELLING:
                run.set_status(RunStatus.CANCELLED, end_reason=RunEndReason.CANCELLED)
            else:
                run.set_status(RunStatus.FAILED, end_reason=end_reason, error=message)

    # --- Sandbox ---

    def _checkpoint(self, *, required: bool = True) -> None:
        """Attemptを終える前に、ジョブを止めてWorkspaceを保存する（design.md §7.9）。Sandboxを使っていなければ
        何もしない。

        保存できなければ、待ち・完了にはせず、Sandboxの障害として別のAttemptでやり直す（required）
        """
        try:
            saved = self.session.suspend()
        except SandboxUnavailable:
            if required:
                raise
            logger.warning("run %s: cannot save the workspace", self.run_obj.uid, exc_info=True)
            return
        if saved is None:
            return
        with self.run_obj.fenced(self.attempt) as run:
            sequence = RunEvent.objects.filter(run=run).aggregate(last=Max("sequence"))["last"] or 0
            Checkpoint.objects.create(
                run=run,
                attempt=self.attempt,
                sequence=sequence,
                storage=saved.backend_id,
                key=saved.snapshot.key,
                size=saved.snapshot.size,
                sha256=saved.snapshot.sha256,
                stopped_jobs=saved.stopped,
            )
            # /workspace/output のファイルは、利用者が会話の画面から取得できる成果物にする（design.md §7.4）
            for output in saved.outputs:
                Artifact.create_output(run, output.path, BlobObject.register(output.stored, saved.backend_id))

    def _sandbox_failure(self, error: SandboxUnavailable) -> None:
        """Sandboxを起動できない・失われた。やり直せるなら別のAttemptで（harness.md §4.3の retry_wait）"""
        if not error.retryable:
            self._fail(RunEndReason.SANDBOX, _("コマンドを動かす環境を使えません。基盤管理者に問い合わせてください。"))
            return
        out_of_memory = self.session.out_of_memory()
        failure = f"{OUT_OF_MEMORY_FAILURE}: {error}" if out_of_memory else f"sandbox: {error}"
        with self.run_obj.fenced(self.attempt) as run:
            ExecutionAttempt.objects.filter(pk=self.attempt.pk).update(ended=timezone.now(), failure=failure[:1000])
            if run.status == RunStatus.CANCELLING:
                run.set_status(RunStatus.CANCELLED, comment=_("停止しました。"), end_reason=RunEndReason.CANCELLED)
                return
            failures = run.attempts.exclude(failure="").exclude(failure="superseded").count()
            if failures >= MAX_ATTEMPTS:
                if out_of_memory:
                    error_text = _("コマンドがメモリの上限を超えて、コマンドを動かす環境が繰り返し止まりました。")
                else:
                    error_text = _("コマンドを動かす環境が繰り返し失われました。")
                run.set_status(RunStatus.FAILED, end_reason=RunEndReason.SANDBOX, error=error_text)
                return
            run.set_status(RunStatus.RETRY_WAIT, comment=_("コマンドを動かす環境が使えなくなったので、やり直します。"))
            run.set_status(RunStatus.QUEUED)
            enqueue_run(run)

    def _file_version(self, path: str) -> str | None:
        """読んだ・書いたファイルの最後の版（harness.md §7.8）。今のWorkspaceに当てはまる記録だけを使う：
        このAttemptの記録と、戻したCheckpoint（このRunのもの）の時点までの記録"""
        restored = self.session.restored
        limit = restored.sequence if restored is not None and restored.run_id == self.run_obj.pk else 0
        results = RunEvent.objects.filter(run=self.run_obj, kind=RunEventKind.TOOL_RESULT).order_by("-sequence")
        for sequence, data in results.values_list("sequence", "data").iterator():
            if limit < sequence <= self.start_sequence:
                continue
            file = (data.get("value") or {}).get("file") if isinstance(data.get("value"), dict) else None
            if file and file.get("path") == path and not data.get("is_error"):
                return file["sha256"]
        return None

    def _interrupted(self) -> bool:
        """取消・追加指示が届いた。長く待つTool（shell）は待つのをやめて、区切りへ戻る"""
        status = Run.objects.filter(pk=self.run_obj.pk).values_list("status", flat=True).first()
        if status == RunStatus.CANCELLING:
            return True
        return UserInstruction.objects.filter(
            run=self.run_obj, kind=InstructionKind.CURRENT, state=InstructionState.RECEIVED
        ).exists()

    # --- Tool ---

    def _execute_calls(self, calls: list[ToolCall]) -> bool:
        """モデルが返した順に実行する。並べてよい呼出しが続く間は1つの組にして並べて実行し、組と組の間を
        安全な区切りとして確かめる（harness.md §4.2・§7.6）"""
        tools = {t.name: t for t in self.tools}
        index = 0
        while index < len(calls):
            call = calls[index]
            rest = calls[index + 1 :]
            if index > 0:
                interrupted = self._between_calls(calls[index:])
                if interrupted is not None:
                    return interrupted
            tool = tools.get(call.name)
            if tool is None:
                self._record_tool_result(call.id, call.name, ToolResult.error(_unknown_tool(call.name, tools)))
                index += 1
                continue
            group = self._concurrent_group(calls[index:], tools)
            if len(group) > 1:
                self._run_group([(tools[c.name], c) for c in group])
                index += len(group)
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
            index += 1
        return True

    @staticmethod
    def _concurrent_group(calls: list[ToolCall], tools: dict[str, Tool]) -> list[ToolCall]:
        group: list[ToolCall] = []
        for call in calls[:MAX_PARALLEL]:
            tool = tools.get(call.name)
            if tool is None or tool.alone or not tool.concurrent(call.arguments):
                break
            group.append(call)
        return group or calls[:1]

    def _run_group(self, pairs: list[tuple[Tool, ToolCall]]) -> None:
        """並べて実行する。DBへの書込み（呼出しの記録・結果の記録・Sandboxの起動）はこのスレッドで行い、
        別のスレッドではToolの実行（Sandboxの補助コマンド）だけを行う。結果はモデルが返した順に記録する"""
        # 引数の誤りもその位置のまま、モデルが返した順に記録する
        prepared: list[tuple[Tool, ToolCall, ToolInvocation | None, ToolResult | None]] = []
        for tool, call in pairs:
            problem = tool.validate(call.arguments)
            if problem:
                prepared.append((tool, call, None, ToolResult.error(problem)))
            else:
                prepared.append((tool, call, self._start_invocation(call), None))
        if any(tool.needs_sandbox and invocation for tool, _call, invocation, _error in prepared):
            self.session.ensure()
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL, thread_name_prefix="tool") as pool:
            futures = [
                pool.submit(self._execute, tool, call, invocation) if invocation is not None else None
                for tool, call, invocation, _error in prepared
            ]
            # 待つ間もleaseを延ばす。別のスレッドはDBに触らないので、ここで延ばす
            pending = {f for f in futures if f is not None}
            while pending:
                _done, pending = wait(pending, timeout=HEARTBEAT_INTERVAL)
                if pending:
                    self.attempt.heartbeat()
            results = [future.result() if future is not None else None for future in futures]
        for (tool, call, invocation, error), result in zip(prepared, results, strict=True):
            if invocation is None or result is None:
                assert error is not None
                self._record_tool_result(call.id, call.name, self._with_repeat_notice(call, error), ToolStatus.FAILED)
            else:
                self._finish_tool(tool, call, invocation, result)

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
        invocation = self._start_invocation(call)
        return self._finish_tool(tool, call, invocation, self._execute(tool, call, invocation))

    def _start_invocation(self, call: ToolCall) -> ToolInvocation:
        """実行の前に記録する。再開で同じ呼出しを実行し直すときは、同じ行を使う（harness.md §11.1）"""
        with self.run_obj.fenced(self.attempt) as run:
            invocation, _created = ToolInvocation.objects.update_or_create(
                run=run,
                call_id=call.id,
                defaults={
                    "attempt": self.attempt,
                    "name": call.name,
                    "arguments": call.arguments,
                    "status": ToolStatus.RUNNING,
                },
            )
        return invocation

    def _execute(self, tool: Tool, call: ToolCall, invocation: ToolInvocation) -> ToolResult:
        """Toolを実行する。並べて実行するときは別のスレッドで呼ばれるので、ここではDBに書かない"""
        context = ToolContext(
            self.run_obj,
            self.attempt,
            call,
            invocation=invocation.uid,
            session=self.session,
            file_version=self._file_version,
            interrupted=self._interrupted,
            exposed=frozenset(t.name for t in self.tools),
        )
        try:
            result = tool.execute(call.arguments, context)
            if context.spilled:
                result = replace(result, meta={**result.meta, "spilled": context.spilled})
            return result
        except (LeaseLost, SandboxUnavailable):
            raise
        except Exception as exc:
            logger.exception("run %s: tool %s failed", self.run_obj.uid, call.name)
            return ToolResult.error(f"The tool failed: {type(exc).__name__}. Try a different approach.")

    def _finish_tool(self, tool: Tool, call: ToolCall, invocation: ToolInvocation, result: ToolResult) -> ToolResult:
        if result.waits_for_user:
            ToolInvocation.objects.filter(pk=invocation.pk).update(value=result.value)
            return result
        if tool.needs_sandbox and (notice := self.session.take_notice()):
            result = replace(result, content=(TextPart(notice), *result.content))
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
        if any(not isinstance(p, TextPart) for p in result.content):
            # 画像等があれば、部品の並び（PDFのページの見出しと画像の対応）を保ったまま末尾に足す
            return replace(result, content=(*result.content, TextPart(notice.lstrip())))
        text = "".join(p.text for p in result.content if isinstance(p, TextPart)) + notice
        return replace(result, content=(TextPart(text),))

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
        self._checkpoint()
        with self.run_obj.fenced(self.attempt) as run:
            # 確認の作成とWAITING_USERへの遷移を同じtransactionで行う。途中でleaseを失っても、
            # 答えられない確認だけが残ることはない
            clarification = ClarificationRequest.objects.create(
                run=run,
                kind=ClarificationKind.QUESTION,
                tool_call_id=call.id,
                questions=(invocation.value or {})["questions"],
                expires=timezone.now() + CLARIFICATION_TTL,
            )
            ToolInvocation.objects.filter(pk=invocation.pk).update(value={"clarification": clarification.uid})
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
            reasoning=version.supports_reasoning,
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
