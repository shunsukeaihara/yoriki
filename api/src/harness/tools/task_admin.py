"""タスク管理のTool（harness.md §10.10、design.md §11.11）。修正チャットのタスクの編集のRunにだけ出す。

対象のタスクはManifestに固定したもの（`harness/task_edit.py`）で、引数では指定させない。直せるのは文章だけ（依頼文・
通知の書き方・入力の項目の説明）で、保存は画面の保存と同じSerializerを通す。権限は依頼した人がタスクを管理できるかを、
呼ぶたびにサーバーで確かめる（エージェントに見せたことを根拠にしない）。
"""

import json
from types import SimpleNamespace
from typing import Any

from django.db import transaction
from rest_framework import exceptions as drf_exceptions

from skill.models import Skill
from task import checks
from task.models import TaskDefinition, TaskDefinitionRevision
from task.serializers import MAX_NOTIFICATION_INSTRUCTIONS, MAX_PROMPT_TEMPLATE, TaskDefinitionUpdateSerializer
from utils.exceptions import Conflict

from ..task_edit import edit_target, requester, scope_label, target_problem
from .base import Effect, Tool, ToolContext, ToolResult

# 直せる項目（画面の項目の名前）
EDITABLE_FIELDS = ("prompt_template", "notification_instructions", "input_descriptions")
NO_TARGET = "The task of this fix chat is not available. Stop and tell the user."
VERSION_CHANGED = (
    "The task was changed by someone else (now version {version}). Call get_task to read it again, then apply your "
    "change to the latest version."
)


def _target(context: ToolContext) -> tuple[TaskDefinition | None, ToolResult | None]:
    task = edit_target(context.run)
    if task is None:
        return None, ToolResult.error(NO_TARGET)
    if problem := target_problem(context.run, task):
        return None, ToolResult.error(f"{problem}. Stop and tell the user.")
    if task.current_revision is None:
        return None, ToolResult.error("The task has no version. Stop and tell the user to edit it on the task screen.")
    return task, None


# --- get_task ---

GET_TASK_DESCRIPTION = """Read the task of this fix chat: its current version, the prompt template, the notification \
instructions, the input fields, and what it uses.

Call this before you change the task, and again after the task may have changed. Use its version as \
expected_version in update_task."""

GET_TASK_PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}


def _names(revision: TaskDefinitionRevision) -> tuple[list[str], list[str]]:
    """固定したSkill（版つき）とConnectorの名前"""
    entries = [entry for _field, entry in checks.skill_entries(revision)]
    skills = {s.uid: s.name for s in Skill.objects.filter(uid__in=[e["skill"] for e in entries]).only("uid", "name")}
    skill_names = [f"{skills.get(e['skill'], '(deleted skill)')} (version {e['version']})" for e in entries]
    connectors = checks.load_connectors(revision.connectors)
    connector_names = [c.name if (c := connectors.get(uid)) else "(deleted connector)" for uid in revision.connectors]
    return skill_names, connector_names


def execute_get_task(_arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    task, error = _target(context)
    if task is None:
        return error or ToolResult.error(NO_TARGET)
    revision = task.current_revision
    assert revision is not None  # _target() が確かめた
    skills, connectors = _names(revision)
    destination = revision.destination.name if revision.destination else "none"
    inputs = [
        {
            "name": i["name"],
            "label": i["label"],
            "type": i["type"],
            "required": i.get("required", False),
            "description": i.get("description", ""),
            **({"choices": i["choices"]} if i.get("choices") else {}),
        }
        for i in revision.inputs
    ]
    state = " suspended" if task.suspended else ""
    lines = [
        f'<task name="{task.name}" scope="{scope_label(task)}" version="{revision.number}"{state}>',
        f"<description>{revision.description or 'none'}</description>",
        f"<prompt_template>\n{revision.prompt_template}\n</prompt_template>",
        f"<notification_instructions>{revision.notification_instructions or 'none'}</notification_instructions>",
        f"<inputs>{json.dumps(inputs, ensure_ascii=False)}</inputs>",
        f"<skills>{', '.join(skills) or 'none'}</skills>",
        f"<connectors>{', '.join(connectors) or 'none'}</connectors>",
        f"<destination>{destination}</destination>",
        f"<model_profile>{revision.model_profile.name}</model_profile>",
        "</task>",
        "You can change only the prompt template, the notification instructions and the descriptions of the input "
        "fields. The skills, connectors, destination, model and the shape of the inputs are changed on the task "
        "screen.",
    ]
    return ToolResult.text("\n".join(lines), value={"task": task.uid, "version": revision.number})


GET_TASK = Tool("get_task", GET_TASK_DESCRIPTION, GET_TASK_PARAMETERS, execute_get_task, Effect.READ, replay_safe=True)


# --- update_task ---

UPDATE_TASK_DESCRIPTION = """Save a new version of the task of this fix chat with changed text. The new version is \
used from the next run; runs already started keep their version.

Change only what the user asked for, and pass only the fields you change. In prompt_template, keep the \
{{ inputs.name }} placeholders of the input fields. Do not copy customer data from this conversation into the task \
unless the user asked for it."""

UPDATE_TASK_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "expected_version": {
            "type": "integer",
            "minimum": 1,
            "description": "The version you read with get_task.",
        },
        "change_reason": {
            "type": "string",
            "minLength": 1,
            "maxLength": 500,
            "description": "A short summary of the change, in the user's language.",
        },
        "prompt_template": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_PROMPT_TEMPLATE,
            "description": "The whole new prompt template.",
        },
        "notification_instructions": {
            "type": "string",
            "maxLength": MAX_NOTIFICATION_INSTRUCTIONS,
            "description": "The whole new notification instructions. An empty string removes them.",
        },
        "input_descriptions": {
            "type": "object",
            "additionalProperties": {"type": "string", "maxLength": 500},
            "description": "New descriptions of input fields, by field name.",
        },
    },
    "required": ["expected_version", "change_reason"],
    "additionalProperties": False,
}


def _errors_text(errors: Any) -> str:
    if isinstance(errors, dict):
        return "; ".join(f"{field}: {_errors_text(value)}" for field, value in errors.items())
    if isinstance(errors, list):
        return " ".join(_errors_text(e) for e in errors)
    return str(errors)


def _changes(before: TaskDefinitionRevision, after: TaskDefinitionRevision) -> list[dict[str, Any]]:
    """変えた項目の変更の前後（画面に出す）。入力の項目の説明は、項目ごとに出す"""
    changes: list[dict[str, Any]] = [
        {"field": name, "input": "", "before": getattr(before, name), "after": getattr(after, name)}
        for name in ("prompt_template", "notification_instructions")
        if getattr(before, name) != getattr(after, name)
    ]
    old = {i["name"]: i.get("description", "") for i in before.inputs}
    for item in after.inputs:
        if old.get(item["name"], "") != item.get("description", ""):
            changes.append(
                {
                    "field": "input_description",
                    "input": item.get("label") or item["name"],
                    "before": old.get(item["name"], ""),
                    "after": item.get("description", ""),
                }
            )
    return changes


def change_value(task: TaskDefinition, version: int, changes: list[dict[str, Any]]) -> dict[str, Any]:
    """画面に出すタスクの変更（design.md §11.11）"""
    return {"task": task.uid, "name": task.name, "scope": scope_label(task), "version": version, "changes": changes}


def _update_data(arguments: dict[str, Any], before: TaskDefinitionRevision) -> dict[str, Any] | ToolResult:
    """画面の更新のAPIに送る形にする。直せない・何もない指定なら誤り"""
    data: dict[str, Any] = {"version": arguments["expected_version"], "change_reason": arguments["change_reason"]}
    for name in ("prompt_template", "notification_instructions"):
        if name in arguments:
            data[name] = arguments[name]
    if descriptions := arguments.get("input_descriptions"):
        names = {i["name"] for i in before.inputs}
        if unknown := [name for name in descriptions if name not in names]:
            return ToolResult.error(
                f"The task has no input fields named {', '.join(unknown)}. Its input fields: "
                f"{', '.join(sorted(names)) or 'none'}. Adding or removing input fields is done on the task screen."
            )
        data["inputs"] = [
            {**i, "description": descriptions.get(i["name"], i.get("description", ""))} for i in before.inputs
        ]
    if len(data) == 2:
        return ToolResult.error(f"Pass at least one of the fields to change: {', '.join(EDITABLE_FIELDS)}.")
    return data


def execute_update_task(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    task, error = _target(context)
    if task is None:
        return error or ToolResult.error(NO_TARGET)
    before = task.current_revision
    assert before is not None  # _target() が確かめた
    # 版の照合は、画面の保存と同じくSerializerで行う（Conflict）
    data = _update_data(arguments, before)
    if isinstance(data, ToolResult):
        return data
    user = requester(context.run)
    serializer = TaskDefinitionUpdateSerializer(
        instance=task, data=data, partial=True, context={"request": SimpleNamespace(user=user)}
    )
    try:
        with transaction.atomic():
            serializer.is_valid(raise_exception=True)
            saved = serializer.save(actor=user)
    except Conflict:
        latest = TaskDefinition.objects.filter(pk=task.pk).select_related("current_revision").first()
        return ToolResult.error(VERSION_CHANGED.format(version=latest.version_number if latest else "unknown"))
    except drf_exceptions.ValidationError as exc:
        return ToolResult.error(f"The change was not saved: {_errors_text(exc.detail)}. Fix it and call again.")
    after = saved.current_revision
    assert after is not None  # revise() が作った
    if after.pk == before.pk:
        return ToolResult.text(
            f"Nothing changed: the content is the same as version {before.number}. No new version was saved."
        )
    changes = _changes(before, after)
    fields = ", ".join(dict.fromkeys(c["field"] for c in changes))
    return ToolResult.text(
        f'Saved version {after.number} of the task "{saved.name}". Changed: {fields}. It is used from the next run.',
        value=change_value(saved, after.number, changes),
        meta={"task": saved.name},
    )


UPDATE_TASK = Tool(
    "update_task", UPDATE_TASK_DESCRIPTION, UPDATE_TASK_PARAMETERS, execute_update_task, Effect.WRITE_EXTERNAL
)

TASK_ADMIN_TOOLS: tuple[Tool, ...] = (GET_TASK, UPDATE_TASK)
