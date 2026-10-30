"""業務結果を確定する（harness.md §10.5）。引数は、SkillかTask Definitionの業務結果のSchemaにする。

ファイルの項目（`format: file`。harness/result_files.py）があれば、指したファイルが `/workspace/output` にあるかを
Sandboxで確かめてから確定する（成果物はRunの完了の前に回収し、配信で出口へ渡す）
"""

from functools import partial
from typing import Any

from django.utils import timezone

from run.models import Run

from ..result_files import OUTPUT_DIR, file_fields, model_schema, output_path, result_files
from .base import Effect, Tool, ToolContext, ToolResult
from .files import call_helper, helper_error

NAME = "submit_result"

DESCRIPTION = """Submit the business result of this work, in the form the skill defines.

Call it once when the work is done and you have checked the result, before your final reply. Call it alone. If \
you call it again, the new result replaces the previous one. After it succeeds, {after}"""

AFTER = "write a short final reply to the user and do not call other tools."
# 通知Skillのあるタスクでは、確定した後に prepare_notification を呼ばせる（design.md §4.3）
AFTER_WITH_NOTIFICATION = (
    "call prepare_notification as the notification skill instructs, then write a short final reply to the user. Do "
    "not call other tools."
)

RECORDED = "The result was recorded. Now {after}"


def make_submit_result_tool(schema: dict[str, Any], *, notification: bool = False) -> Tool:
    after = AFTER_WITH_NOTIFICATION if notification else AFTER
    return Tool(
        NAME,
        DESCRIPTION.format(after=after),
        model_schema(schema),
        partial(execute, schema=schema, recorded=RECORDED.format(after=after)),
        Effect.READ,
        alone=True,
        replay_safe=True,
        needs_sandbox=bool(file_fields(schema)),
    )


def original_schema(tool: Tool) -> dict[str, Any] | None:
    """`make_submit_result_tool()` に渡した業務結果のSchema（モデルへ渡すものでなく、ファイルの項目の印があるもの）"""
    execute_fn = tool.execute
    return execute_fn.keywords.get("schema") if isinstance(execute_fn, partial) else None


def execute(
    arguments: dict[str, Any],
    context: ToolContext,
    *,
    schema: dict[str, Any] | None = None,
    recorded: str = RECORDED.format(after=AFTER),
) -> ToolResult:
    # 引数はRunnerが実行の前にSchemaで検証している
    if problem := _file_problem(schema, arguments, context):
        return problem
    # 通知の候補（prepare_notification）は前の業務結果から作ったものなので消す。作り直さなければ標準の形で送る
    with context.run.fenced(context.attempt) as run:
        Run.objects.filter(pk=run.pk).update(result=arguments, notification=None, updated=timezone.now())
    context.run.result = arguments
    context.run.notification = None
    return ToolResult.text(recorded, value={"result": arguments})


def _file_problem(schema: dict[str, Any] | None, arguments: dict[str, Any], context: ToolContext) -> ToolResult | None:
    """ファイルの項目が、`/workspace/output` にあるファイルを指しているか"""
    fields = file_fields(schema)
    if not fields:
        return None
    for name, is_list in fields.items():
        values = arguments.get(name)
        for value in (values or []) if is_list else ([] if values is None else [values]):
            if output_path(value) is None:
                return ToolResult.error(
                    f'{name}: "{value}" is not a file in {OUTPUT_DIR}. Save the file in {OUTPUT_DIR} '
                    "(not a hidden file) and pass its path."
                )
    for name, path in sorted(set(result_files(schema, arguments))):
        stat = call_helper(context, "stat", {"path": f"{OUTPUT_DIR}/{path}"})
        if not stat.get("ok"):
            return helper_error(stat)
        # symlinkは成果物として回収しない（harness/sandbox.py の collect_outputs()）。辿った先のパスは違う
        if not stat.get("exists") or stat.get("type") != "file" or stat.get("path") != f"{OUTPUT_DIR}/{path}":
            return ToolResult.error(
                f"{name}: {OUTPUT_DIR}/{path} does not exist or is not a regular file (symlinks are not "
                "collected). Check the path with glob."
            )
    return None
