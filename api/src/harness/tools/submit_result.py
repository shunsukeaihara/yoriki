"""業務結果を確定する（harness.md §10.5）。引数は、SkillかTask Definitionの業務結果のSchemaにする"""

from typing import Any

from django.utils import timezone

from run.models import Run

from .base import Effect, Tool, ToolContext, ToolResult

NAME = "submit_result"

DESCRIPTION = """Submit the business result of this work, in the form the skill defines.

Call it once when the work is done and you have checked the result, before your final reply. Call it alone. If \
you call it again, the new result replaces the previous one. After it succeeds, write a short final reply to the \
user and do not call other tools."""

RECORDED = "The result was recorded. Now write a short final reply to the user, and do not call other tools."


def make_submit_result_tool(schema: dict[str, Any]) -> Tool:
    return Tool(NAME, DESCRIPTION, schema, execute, Effect.READ, alone=True, replay_safe=True)


def execute(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    # 引数はRunnerが実行の前にSchemaで検証している
    with context.run.fenced(context.attempt) as run:
        Run.objects.filter(pk=run.pk).update(result=arguments, updated=timezone.now())
    context.run.result = arguments
    return ToolResult.text(RECORDED, value={"result": arguments})
