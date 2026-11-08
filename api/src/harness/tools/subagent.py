"""`subagent`・`subagent_kill`（harness.md §7.4・§10.11）。親のRunだけに出す"""

from typing import Any

from django.conf import settings
from django.utils import timezone
from nanoid import generate

from run.dispatch import enqueue_subagent
from run.models import LIVE_SUBAGENT_STATUSES, RunEvent, RunEventKind, Subagent, SubagentStatus
from runtime.launcher import SandboxUnavailable

from .. import subagents
from .base import Effect, Tool, ToolContext, ToolResult

SUBAGENT_DESCRIPTION = """Start a subagent: another agent that works on a copy of your workspace, in its own sandbox, \
while you continue. It returns its ID at once, and you are called again with its report when it finishes.
- Use it for independent work you can describe completely: research in files or databases, a separate deliverable, \
or a change you will review. Do not use it for small steps; do them yourself.
- The subagent does not see this conversation. Write the goal, the inputs (paths), the constraints, and what to \
report back in `prompt`. It cannot ask the user, change skills, or start subagents.
- It works on a copy: its changes do not affect your files, and yours after the start do not reach it. It returns \
a text (for a change to files, ask for a diff and apply it yourself) and the new or changed files of its \
/workspace/output, which appear read-only in /workspace/input/subagents/<id>/.
- At most {limit} subagents run at the same time. Stop the ones you no longer need with `subagent_kill`."""

SUBAGENT_PARAMETERS = {
    "type": "object",
    "properties": {
        "description": {"type": "string", "description": "A short description of the work (3-10 words)."},
        "prompt": {"type": "string", "description": "The complete task for the subagent."},
        "model": {"type": "string", "description": "The model profile for the subagent. Omit to use yours."},
        "effort": {"type": "string", "description": "The reasoning effort for the subagent. Omit for the default."},
    },
    "required": ["description", "prompt"],
    "additionalProperties": False,
}

STARTED = (
    "Started subagent {id}. You are called again with its report when it finishes; you do not need to wait or "
    "check on it. Continue with other work, or end your turn if there is nothing else to do."
)
ID_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def execute_subagent(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    run = context.run
    description = arguments["description"].strip()[:200]
    if not description or not arguments["prompt"].strip():
        return ToolResult.error("Write the description and the prompt.")
    limit = settings.YORIKI_SUBAGENTS_PER_RUN
    if subagents.live_count(run) >= limit:
        return ToolResult.error(
            f"{limit} subagents are already running. Wait for one to finish, or stop one with subagent_kill."
        )
    resolved = subagents.resolve_model(run, arguments.get("model", "").strip(), arguments.get("effort", "").strip())
    if isinstance(resolved, str):
        return ToolResult.error(resolved)
    version, effort = resolved
    session = context.sandbox_session()
    jobs_running = session.started and bool(session.running_jobs(refresh=True))
    key = f"subagents/{run.uid}/{generate(ID_ALPHABET, 12)}.tar"
    try:
        stored, backend, baseline = session.copy_workspace(key)
    except SandboxUnavailable as exc:
        return ToolResult.error(f"The copy of the workspace could not be made ({exc}). Do the work yourself.")
    events = list(RunEvent.objects.filter(run=run, subagent__isnull=True, kind=RunEventKind.USER_MESSAGE))
    skills_text = subagents.instructed_skills_text(run, events)
    subagent = Subagent(
        tool_call_id=context.call.id,
        description=description,
        prompt=arguments["prompt"],
        model_version=version,
        effort=effort,
        copy_storage=backend if stored else "",
        copy_key=stored.key if stored else "",
        baseline=baseline,
    )
    try:
        with run.fenced(context.attempt) as current:
            # 数え直す（並べて呼ばれない Tool だが、別の経路で子が増えていないか）
            if subagents.live_count(current) >= limit:
                subagents.discard_copy_later(subagent)
                return ToolResult.error(f"{limit} subagents are already running. Wait for one to finish.")
            subagent.run = current
            subagent.save()
            text = subagents.first_message(subagent, skills_text, jobs_running=jobs_running)
            RunEvent.record(current, RunEventKind.USER_MESSAGE, {"text": text}, subagent=subagent)
            enqueue_subagent(subagent)
    except BaseException:
        if stored is not None:
            subagents.discard_copy(subagent)
        raise
    result = STARTED.format(id=subagent.uid)
    if jobs_running:
        result += " " + subagents.JOBS_RUNNING_NOTE
    return ToolResult.text(
        result,
        value={"subagent": subagent.uid},
        meta={"subagent": subagent.uid, "description": description},
    )


def _description() -> str:
    return SUBAGENT_DESCRIPTION.format(limit=settings.YORIKI_SUBAGENTS_PER_RUN)


SUBAGENT_KILL_DESCRIPTION = "Stop a subagent. Its work is discarded, and it returns nothing."
SUBAGENT_KILL_PARAMETERS = {
    "type": "object",
    "properties": {"id": {"type": "string", "description": "The ID returned by subagent."}},
    "required": ["id"],
    "additionalProperties": False,
}


def execute_subagent_kill(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    with context.run.fenced(context.attempt) as run:
        subagent = Subagent.objects.select_for_update().filter(run=run, uid=arguments["id"].strip()).first()
        if subagent is None:
            return ToolResult.error(f'No subagent with the ID "{arguments["id"]}" in this work.')
        if subagent.status not in LIVE_SUBAGENT_STATUSES:
            return ToolResult.text(f"Subagent {subagent.uid} has already ended ({subagent.get_status_display()}).")
        queued = subagent.status == SubagentStatus.QUEUED
        # 止めたことはこの結果で伝えるので、終わったことは知らせない。動いている子は次の区切りで止まる
        Subagent.objects.filter(pk=subagent.pk).update(
            status=SubagentStatus.CANCELLED, error="stopped by the parent", reported=True, finished=timezone.now()
        )
        if queued:
            subagents.discard_copy_later(subagent)
    return ToolResult.text(f"Stopped subagent {subagent.uid}.", value={"subagent": subagent.uid})


SUBAGENT = Tool("subagent", _description(), SUBAGENT_PARAMETERS, execute_subagent, Effect.WRITE_SANDBOX)
SUBAGENT_KILL = Tool(
    "subagent_kill", SUBAGENT_KILL_DESCRIPTION, SUBAGENT_KILL_PARAMETERS, execute_subagent_kill, Effect.WRITE_SANDBOX
)
SUBAGENT_TOOLS: tuple[Tool, ...] = (SUBAGENT, SUBAGENT_KILL)
