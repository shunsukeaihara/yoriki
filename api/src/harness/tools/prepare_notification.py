"""通知の候補を決める（design.md §4.3）。通知の書き方のあるタスクのRunにだけ出す。

通知の文章（`message`）と、出口へ渡すファイルの絞り込み（`files`。業務結果のファイルの項目で指したものの中からだけ）を
Runに記録する。送るのはRunが完了したときで、完了の通知の内容に載せる（delivery/payload.py）。呼ばなくても、標準の形で
通知する。宛先・送り方・通知するイベントは変えられず、渡せるファイルも増やせない。
"""

from typing import Any

from django.utils import timezone

from run.models import Run

from .. import tasks
from ..result_files import OUTPUT_DIR, file_fields, output_path, result_files
from .base import Effect, Tool, ToolContext, ToolResult

NAME = "prepare_notification"
MAX_MESSAGE = 2000

DESCRIPTION = f"""Prepare the notification that is sent when this work completes, as the notification \
instructions of the task say.

Write `message`: a short text for the people who receive the notification, in the language the instructions say. \
If the work has a result to submit, call submit_result first. `files` chooses which of the result files to send: \
paths in {OUTPUT_DIR} that the submitted result points to. Omit it to send all of them, or pass [] to send none. \
You cannot add other files, and you cannot choose where or when it is sent. Calling it again replaces the previous \
one. Nothing is sent now."""

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "message": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_MESSAGE,
            "description": "The text of the notification.",
        },
        "files": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 50,
            "description": f"Result files to send (paths in {OUTPUT_DIR}). Omit to send all the result files.",
        },
    },
    "required": ["message"],
    "additionalProperties": False,
}

RECORDED = "The notification was prepared. It is sent when this work completes."


def execute(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    message = arguments["message"].strip()
    if not message:
        return ToolResult.error("message is empty. Write the text of the notification.")
    schema = tasks.output_schema(context.run)
    if schema and context.run.result is None:
        return ToolResult.error("Call submit_result first, and then prepare_notification.")
    candidate: dict[str, Any] = {"message": message}
    if "files" in arguments:
        paths = arguments["files"]
        if paths and not file_fields(schema):
            return ToolResult.error("This work has no result files to send. Omit files.")
        sendable = {path for _field, path in result_files(schema, context.run.result)}
        chosen = []
        for value in paths:
            path = output_path(value)
            if path is None or path not in sendable:
                listed = ", ".join(f"{OUTPUT_DIR}/{p}" for p in sorted(sendable)) or "(none)"
                return ToolResult.error(
                    f'"{value}" is not a file that the submitted result points to. Choose from: {listed}'
                )
            chosen.append(path)
        candidate["files"] = list(dict.fromkeys(chosen))
    with context.run.fenced(context.attempt) as run:
        Run.objects.filter(pk=run.pk).update(notification=candidate, updated=timezone.now())
    context.run.notification = candidate
    return ToolResult.text(RECORDED, value={"notification": candidate})


PREPARE_NOTIFICATION = Tool(NAME, DESCRIPTION, PARAMETERS, execute, Effect.READ, replay_safe=True)
