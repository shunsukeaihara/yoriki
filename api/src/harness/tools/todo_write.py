"""作業の計画と進み具合（harness.md §7.4）。画面の「現在の作業」に出す（design.md §11.2）"""

from typing import Any

from .base import Effect, Tool, ToolContext, ToolResult

DESCRIPTION = """Record and update the task list for the current request. The user sees it as your working plan.

Use it when the request has 3 or more distinct steps, or the user gives several tasks. Skip it for a single simple \
task or a purely conversational request.

- Send the full list on every call; it replaces the previous one.
- Mark an item `in_progress` when you start it and `completed` as soon as it is actually done. Keep only one \
item `in_progress` at a time.
- Do not repeat the list in your reply; the user already sees it."""

PARAMETERS = {
    "type": "object",
    "properties": {
        "todos": {
            "type": "array",
            "maxItems": 30,
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "minLength": 1, "maxLength": 200, "description": "The step."},
                    "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                },
                "required": ["content", "status"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["todos"],
    "additionalProperties": False,
}


def execute(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    todos = arguments["todos"]
    if sum(1 for t in todos if t["status"] == "in_progress") > 1:
        return ToolResult.error("Only one item can be in_progress at a time. Fix the list and call again.")
    return ToolResult.text("Task list updated.", value={"todos": todos})


TODO_WRITE = Tool("todo_write", DESCRIPTION, PARAMETERS, execute, Effect.READ)
