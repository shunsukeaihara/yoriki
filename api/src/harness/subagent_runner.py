"""サブエージェントのRunner（harness.md §10.11）。

親のRunnerと同じループで、子の記録（RunEventの `subagent`）だけから文脈を組み立てる。親との違い：
- 発言は受けず、確認待ち・裏の作業の待ち・保存・新しいAttemptへの移行もない。最後の応答で終わる。
- 書込みは子のAttemptで行い、親のRunが停止中・終わった後と、親が子を止めた後は断る（Run.fenced()）。
- 終わったら、返すテキストとファイルを確定してから親を起こす。親は次の区切りで `subagent.finished` として受け取る。
- Workerやコンテナが落ちても再試行しない。失敗として親へ伝える。
"""

import contextlib
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from artifact.models import Artifact, BlobObject
from run.errors import LeaseLost
from run.models import (
    Run,
    RunEndReason,
    RunEventKind,
    RunStatus,
    Subagent,
    SubagentStatus,
    ToolInvocation,
    ToolStatus,
)
from runtime.launcher import SandboxUnavailable

from . import subagents
from .context import RUNTIME_CONTEXT_TAG
from .prompts import system_prompt
from .runner import ATTEMPT_LIMIT_SECONDS, RepeatedErrors, Runner
from .tools.base import Tool

logger = logging.getLogger(__name__)

# 子が書いてよい親のRunの状態。停止中・終わった後は書かない（design.md §5.3）
ACTIVE_PARENT_STATUSES = (RunStatus.RUNNING, RunStatus.WAITING_USER, RunStatus.WAITING_BACKGROUND)
DEFAULT_TIMEOUT_SECONDS = 1800
JOBS_AT_END_NOTICE = (
    "Background jobs are still running: {jobs}. They are stopped when you write your final reply. Wait for them with "
    "job_output if you need their results, or stop them with job_kill, and then write your final reply."
)


class TimedOut(Exception):
    """子の実行時間の上限を、モデルの応答を受け取る間に超えた"""


class SubagentRunner(Runner):
    def __init__(self, attempt, **kwargs):
        super().__init__(attempt, **kwargs)
        self.subagent: Subagent = Subagent.objects.select_related(
            "model_version__connection", "model_version__profile"
        ).get(pk=attempt.subagent_id)
        self.warned_jobs = False
        self._use_subagent_model()

    def _use_subagent_model(self) -> None:
        """子は起動したときに固定したモデルで動き、ステップ数を子ごとに数える。親のRunの行には書かない（Runの
        モデルの項目はメモリの上で差し替えるだけ）"""
        self.run_obj.model_version = self.subagent.model_version
        self.run_obj.effort = self.subagent.effort
        self.run_obj.step_count = self.subagent.step_count

    # --- 入口 ---

    def run(self) -> None:
        try:
            self._resume()
            while self._step():
                pass
        except LeaseLost:
            logger.info("subagent %s of run %s stopped", self.subagent.uid, self.run_obj.uid)
        except RepeatedErrors:
            self._end(SubagentStatus.FAILED, error="it repeated the same failing call")
        except TimedOut:
            self._end(SubagentStatus.FAILED, error=self._timeout_error())
        except SandboxUnavailable as exc:
            logger.warning("subagent %s: sandbox unavailable: %s", self.subagent.uid, exc)
            self._end(SubagentStatus.FAILED, error="its sandbox stopped")
        except Exception:
            logger.exception("subagent %s: unexpected error", self.subagent.uid)
            self._end(SubagentStatus.FAILED, error="an internal error stopped it")
        finally:
            self.session.close()
            self.attempt.end()
            self._after_exit()

    def _after_exit(self) -> None:
        """Workerが止まったことを確かめた後。止められた・書けなくなった子を終わりの状態にし、一時のコピーを消し、停止中の
        親のRunを確かめる（最後に止まった子が停止済みにする。design.md §5.3）"""
        with transaction.atomic():
            run = Run.locked(self.run_obj.pk)
            subagent = Subagent.objects.select_for_update().get(pk=self.subagent.pk)
            if subagent.status == SubagentStatus.RUNNING:
                if run.status in ACTIVE_PARENT_STATUSES:
                    if subagents.end(subagent, SubagentStatus.FAILED, error="its worker stopped"):
                        run.wake_for_background()
                else:
                    Subagent.objects.filter(pk=subagent.pk).update(
                        status=SubagentStatus.CANCELLED, reported=True, finished=timezone.now()
                    )
                subagent.refresh_from_db(fields=["status"])
            # 途中で止まったToolの呼出しを閉じる（子のAttemptはこれで終わり、やり直さない）
            stopped = ToolStatus.CANCELLED if subagent.status == SubagentStatus.CANCELLED else ToolStatus.UNKNOWN
            ToolInvocation.objects.filter(
                run=run, subagent=subagent, status__in=(ToolStatus.REQUESTED, ToolStatus.RUNNING)
            ).update(status=stopped, ended=timezone.now())
            run.settle_cancelling()
        subagents.discard_copy(subagent)

    def _end(
        self, status: SubagentStatus, *, text: str = "", files: list[dict[str, str]] | None = None, error: str = ""
    ) -> None:
        """返すものを確定して、親を起こす。親のRunが停止中・終わった後、または止められた後なら書かない"""
        with contextlib.suppress(LeaseLost), self.run_obj.fenced(self.attempt) as run:
            if subagents.end(self.subagent, status, text=text, files=files, error=error):
                run.wake_for_background()

    # --- 再開と区切り ---

    def _resume(self) -> None:
        """子は1つのAttemptだけで動くので、実行時の文脈を記録するだけ"""
        events = self._events()
        self.start_sequence = events[-1].sequence if events else 0
        self.inherited = []
        self.tools = self._exposed_tools()
        context = self._runtime_context()
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

    def _refresh(self) -> Run:
        run = super()._refresh()
        self.subagent.refresh_from_db(fields=["status", "step_count", "started"])
        self._use_subagent_model()
        return run

    def _boundary(self) -> bool:
        self.attempt.heartbeat()
        run = self._refresh()
        self._check_alive(run)
        if run.handles_sensitive_data and not self.run_obj.model_version.is_local:
            # 機密の会話の内容をクラウドのモデルへ送らない（harness.md §10.11）
            self._end(SubagentStatus.CANCELLED, error="the conversation became confidential")
            return False
        if revoked := self._revoked():
            self._end(SubagentStatus.FAILED, error=revoked[1])
            return False
        self._record_finished_jobs()
        if self._timed_out():
            self._end(SubagentStatus.FAILED, error=self._timeout_error())
            return False
        return True

    def _check_alive(self, run: Run) -> None:
        """親のRunが停止中・終わった、または親が止めたなら、Attemptを抜ける（_after_exit() が片付ける）"""
        if run.status not in ACTIVE_PARENT_STATUSES or self.subagent.status != SubagentStatus.RUNNING:
            raise LeaseLost(f"subagent={self.subagent.uid} status={self.subagent.status} run={run.status}")

    def _timeout_seconds(self) -> int:
        version = self.run_obj.runtime_version
        seconds = version.subagent_timeout_seconds if version is not None else DEFAULT_TIMEOUT_SECONDS
        # 子は新しいAttemptへ移らないので、Attemptの上限を超えない（harness.md §4.4）
        return min(seconds, ATTEMPT_LIMIT_SECONDS)

    def _time_over(self) -> bool:
        """子はRunの実行時間ではなく、子の実行時間の上限（_timed_out()）で止める"""
        return False

    def _timeout_error(self) -> str:
        return f"it exceeded the time limit of {self._timeout_seconds() // 60} minutes"

    def _check_streaming(self) -> None:
        """応答が届くたびに上限を確かめる（最初の応答が届くまでの待ちは、接続のtimeoutまで切れない）"""
        if self._timed_out():
            raise TimedOut()

    def _timed_out(self) -> bool:
        started = self.subagent.started or self.attempt.started
        return timezone.now() - started >= timedelta(seconds=self._timeout_seconds())

    def _interrupted(self) -> bool:
        status = Run.objects.filter(pk=self.run_obj.pk).values_list("status", flat=True).first()
        current = Subagent.objects.filter(pk=self.subagent.pk).values_list("status", flat=True).first()
        return status not in ACTIVE_PARENT_STATUSES or current != SubagentStatus.RUNNING or self._timed_out()

    def _between_calls(self, remaining) -> bool | None:
        self.attempt.heartbeat()
        run = self._refresh()
        self._check_alive(run)
        if self._timed_out():
            self._close_calls(remaining, "Not executed: the time limit was reached.")
            self._end(SubagentStatus.FAILED, error=self._timeout_error())
            return False
        return None

    # --- Toolとprompt ---

    def _exposed_tools(self) -> tuple[Tool, ...]:
        return subagents.child_tools(super()._exposed_tools())

    def _system_prompt(self) -> str:
        return system_prompt(self.tools, template_name="subagent.md")

    def _notify_delta(self, step: int, text: str) -> None:
        """子の応答の途中経過は、親の画面へ流さない"""

    def _count_step(self, run: Run) -> None:
        Subagent.objects.filter(pk=self.subagent.pk).update(step_count=self.run_obj.step_count + 1)
        self.run_obj.step_count += 1
        self.subagent.step_count = self.run_obj.step_count

    def _result_missing(self) -> bool:
        return False

    # --- 終わり方 ---

    def _handle_reply(self, response) -> bool:
        from llm.providers.base import StopReason  # noqa: PLC0415

        stop = response.completed.stop_reason if response.completed else StopReason.OTHER
        if stop == StopReason.MAX_TOKENS:
            return self._nudge("Your previous response was cut off at the output limit. Continue in smaller pieces.")
        if not response.text.strip():
            return self._nudge("Your previous response was empty. Call a tool or write your final report.")
        running = self._running_jobs()
        if running and not self.warned_jobs:
            self.warned_jobs = True
            jobs = ", ".join(f"{j.job_id} ({j.description})" for j in running)
            self._record(RunEventKind.CONTEXT, {"text": JOBS_AT_END_NOTICE.format(jobs=jobs)})
            return True
        return self._finish(response.text)

    def _finish_at_step_limit(self, text: str, end_reason: RunEndReason = RunEndReason.STEP_LIMIT) -> bool:
        if end_reason == RunEndReason.BUDGET:
            return self._finish(text, SubagentStatus.FAILED, error="this month's budget is used up")
        return self._finish(text, SubagentStatus.FAILED, error="it reached the maximum number of steps")

    def _finish(self, text: str, status: SubagentStatus = SubagentStatus.COMPLETED, *, error: str = "") -> bool:
        """返すファイル（/workspace/output のうち増えた・変わったもの）を保存し、テキストとあわせて確定する"""
        backend, outputs = self.session.collect_returned(self.subagent.baseline)
        with self.run_obj.fenced(self.attempt) as run:
            files = []
            for output in outputs:
                path = f"{subagents.result_dir(self.subagent)}/{output.path}"
                artifact = Artifact.create_subagent_result(run, path, BlobObject.register(output.stored, backend))
                files.append({"path": output.path, "artifact": artifact.uid})
            if subagents.end(self.subagent, status, text=text, files=files, error=error):
                run.wake_for_background()
        return False

    def _handle_provider_failure(self, error) -> None:
        self._end(SubagentStatus.FAILED, error=f"the model could not be reached ({error.message})")

    def _fail(self, end_reason: RunEndReason, message: str) -> None:
        self._end(SubagentStatus.FAILED, error=message)

    def _finish_cancelled(self) -> None:
        raise LeaseLost(f"subagent={self.subagent.uid}: the parent run is stopping")

    def _checkpoint(self, *, required: bool = True, snapshot: bool | None = None) -> None:
        """子のワークスペースは親のコピーで、保存しない（design.md §7.9）"""

    def _watch(self) -> bool:
        return False
