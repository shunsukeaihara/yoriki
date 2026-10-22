"""コマンドの実行とバックグラウンドのジョブ（harness.md §7.8・§8）。

コマンドは呼出しごとに新しいbashで動く（常駐のシェルは持たない）。`shell` は終わりを `timeout_seconds` まで待ち、
終わらなければ止めずにジョブとして続け、`job_id` を返す。
"""

from typing import Any

from runtime.launcher import WORK_DIR

from ..sandbox import MAX_JOBS, Job, SandboxSession, workspace_path
from .base import Effect, Tool, ToolContext, ToolResult
from .output import SPILL_MAX_CHARS, limited

DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600
# 出力が大きいときに読む範囲（バイト）。先頭2割・末尾8割を見せるのに足りる分だけを読む
WINDOW = 2 * 1024 * 1024
HEAD_BYTES = 64 * 1024
TAIL_BYTES = 512 * 1024
HEAD_RATIO = 0.2

SHELL_DESCRIPTION = """Run a bash command in the sandbox.

- Each call starts a new shell: `cd` and `export` do not carry over to the next call. Use `workdir` instead of \
`cd`. The default working directory is /workspace/work.
- There is no network. `pip install` does not work.
- Standard input is closed: do not use interactive commands (editors, pagers, `input()`).
- Files: /workspace/input and /workspace/master are read-only; write intermediate files in /workspace/work and \
deliverables in /workspace/output. Python with pandas, openpyxl, pypdf, pdfplumber, python-docx, python-pptx and \
matplotlib is available as `python`.
- To read, search or change files, use `read`, `grep`, `glob`, `write` and `edit` instead of `cat`, `grep`, `sed` \
or `ls`. Before deleting or moving files, check the absolute paths.
- The command waits up to `timeout_seconds` (default 120, max 600). If it has not finished by then, it keeps \
running as a background job and you get a `job_id`. Start long commands (several minutes or more) with \
`background: true` and continue with other work; you are notified when a job finishes, so do not poll it with \
`sleep`. Up to 4 jobs can run at the same time.
- Do not change a file with a job while also changing it with `write` or `edit`.
- Long output is truncated (the end is kept) and saved to a file under /workspace/work/.yoriki/outputs/."""

SHELL_PARAMETERS = {
    "type": "object",
    "properties": {
        "description": {
            "type": "string",
            "minLength": 1,
            "maxLength": 100,
            "description": 'What the command does in 5-10 words, shown to the user, e.g. "請求書のPDFから表を抽出".',
        },
        "command": {"type": "string", "minLength": 1, "description": "The bash command."},
        "workdir": {"type": "string", "description": "Working directory inside /workspace. Default /workspace/work."},
        "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": MAX_TIMEOUT},
        "background": {"type": "boolean", "description": "Start as a background job without waiting."},
    },
    "required": ["description", "command"],
    "additionalProperties": False,
}


def _job_text(job: Job, start: int, end: int) -> tuple[str, str]:
    """`start` から `end` までの（見せる部分、退避する全体）。大きい出力は先頭と末尾だけを読む。
    動いているジョブは読む間も出力が増えるので、`end` より先は読まない（次の読み出しで返す）"""
    size = end - start
    if size <= WINDOW:
        text = job.read_output(start, size)
        return text, text
    head = job.read_output(start, HEAD_BYTES)
    tail = job.read_output(end - TAIL_BYTES, TAIL_BYTES)
    return f"{head}\n…\n{tail}", job.read_output(start, min(size, SPILL_MAX_CHARS))


def _output(context: ToolContext, job: Job) -> str:
    """前回の続きからの出力。読んだ位置を進める"""
    end = job.output_size()
    shown, full = _job_text(job, job.read_offset, end)
    job.read_offset = end
    return limited(context, shown, head_ratio=HEAD_RATIO, full_text=full)


def _status(job: Job) -> str:
    if not job.finished:
        return f"running ({job.elapsed:.0f} seconds so far)"
    if job.stopped == "timeout":
        return f"stopped: it exceeded the job time limit of {job.timeout_seconds} seconds"
    if job.stopped:
        return "stopped"
    return f"finished with exit code {job.exit_code}"


def _max_timeout(context: ToolContext) -> int:
    version = context.run.runtime_version
    return min(MAX_TIMEOUT, version.command_timeout_seconds if version else MAX_TIMEOUT)


def execute_shell(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    session = context.sandbox_session()
    workdir = workspace_path(arguments.get("workdir") or WORK_DIR)
    if workdir is None:
        return ToolResult.error(f"workdir {arguments.get('workdir')} is outside /workspace.")
    session.ensure()
    # 終わったジョブを数えないよう、Dockerのexecの状態で確かめ直してから数える
    running = session.running_jobs(refresh=True)
    if len(running) >= MAX_JOBS:
        ids = ", ".join(j.job_id for j in running)
        return ToolResult.error(
            f"{MAX_JOBS} jobs are already running ({ids}). Wait for one to finish, or stop one with job_kill."
        )
    job = session.start_job(arguments["command"], workdir, arguments["description"])
    value = {"job_id": job.job_id}
    meta = {"description": arguments["description"]}
    if arguments.get("background"):
        text = (
            f"Started background job {job.job_id} in {workdir}. You will be notified when it finishes. "
            "Continue with other work; use job_output to read its output, or job_kill to stop it."
        )
        return ToolResult.text(text, value=value, meta=meta)
    timeout = min(arguments.get("timeout_seconds", DEFAULT_TIMEOUT), _max_timeout(context))
    if session.wait(job, timeout, interrupted=context.interrupted):
        job.reported = True  # 結果をここで返すので、job.finished は記録しない
        lines = [
            f"Exit code: {job.exit_code}" if not job.stopped else f"Status: {_status(job)}",
            f"Wall time: {job.elapsed:.1f} seconds",
            f"Working directory: {workdir}",
            "Output:",
            _output(context, job),
        ]
        return ToolResult.text("\n".join(lines), value=value | {"exit_code": job.exit_code}, meta=meta)
    lines = [
        f"The command is still running after {job.elapsed:.0f} seconds. It continues as background job "
        f"{job.job_id}; you will be notified when it finishes. Use job_output to read new output, or job_kill to "
        "stop it.",
        f"Working directory: {workdir}",
        "Output so far:",
        _output(context, job),
    ]
    return ToolResult.text("\n".join(lines), value=value, meta=meta)


SHELL = Tool("shell", SHELL_DESCRIPTION, SHELL_PARAMETERS, execute_shell, Effect.WRITE_SANDBOX, needs_sandbox=True)


def _find(session: SandboxSession, job_id: str) -> Job | ToolResult:
    job = session.jobs.get(job_id)
    if job is not None:
        return job
    known = ", ".join(session.jobs) or "none"
    return ToolResult.error(
        f"No job {job_id} (jobs in this session: {known}). Jobs are stopped when the work pauses, for example "
        "while waiting for the user's answer; run the command again if needed."
    )


JOB_OUTPUT_DESCRIPTION = """Read the new output of a background job since the last read, and its status.

- `wait`: seconds to wait for the job to finish before returning (default 0, max 600). Prefer doing other work \
over waiting; you are notified when a job finishes."""

JOB_OUTPUT_PARAMETERS = {
    "type": "object",
    "properties": {
        "job_id": {"type": "string", "minLength": 1},
        "wait": {"type": "integer", "minimum": 0, "maximum": MAX_TIMEOUT},
    },
    "required": ["job_id"],
    "additionalProperties": False,
}


def execute_job_output(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    session = context.sandbox_session()
    job = _find(session, arguments["job_id"])
    if isinstance(job, ToolResult):
        return job
    if arguments.get("wait"):
        session.wait(job, arguments["wait"], interrupted=context.interrupted)
    else:
        session.refresh(job)
    if job.finished:
        job.reported = True
    text = "\n".join([f"Job {job.job_id}: {_status(job)}", "New output:", _output(context, job)])
    return ToolResult.text(text, value={"job_id": job.job_id, "finished": job.finished, "exit_code": job.exit_code})


JOB_OUTPUT = Tool(
    "job_output",
    JOB_OUTPUT_DESCRIPTION,
    JOB_OUTPUT_PARAMETERS,
    execute_job_output,
    Effect.READ,
    needs_sandbox=True,
)

JOB_KILL_DESCRIPTION = "Stop a background job (its whole process group)."

JOB_KILL_PARAMETERS = {
    "type": "object",
    "properties": {"job_id": {"type": "string", "minLength": 1}},
    "required": ["job_id"],
    "additionalProperties": False,
}


def execute_job_kill(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    session = context.sandbox_session()
    job = _find(session, arguments["job_id"])
    if isinstance(job, ToolResult):
        return job
    if job.finished:
        return ToolResult.text(f"Job {job.job_id} has already {_status(job)}.", value={"job_id": job.job_id})
    if not session.kill(job):
        return ToolResult.error(f"Job {job.job_id} did not stop yet. Call job_kill again in a moment.")
    job.reported = True
    text = "\n".join([f"Job {job.job_id}: stopped.", "Output since the last read:", _output(context, job)])
    return ToolResult.text(text, value={"job_id": job.job_id})


JOB_KILL = Tool(
    "job_kill", JOB_KILL_DESCRIPTION, JOB_KILL_PARAMETERS, execute_job_kill, Effect.WRITE_SANDBOX, needs_sandbox=True
)
