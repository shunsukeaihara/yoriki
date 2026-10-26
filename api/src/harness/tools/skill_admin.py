"""Skill管理のTool（harness.md §10.9、design.md §3.5・§3.9）。Skillの編集のRunにだけ出す。

対象のSkillはManifestに固定したもの（`harness/skill_edit.py`）で、引数では指定させない。検証と権限の判定は管理APIと
同じSerializer・Modelのメソッドを通し、呼ぶたびにサーバーで確かめる（エージェントに見せたことを根拠にしない）。
"""

import io
import json
import posixpath
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from django.conf import settings
from django.db import transaction
from rest_framework import exceptions as drf_exceptions

from resources import formats
from run.models import ToolInvocation
from skill.errors import DraftExists, EvaluationRequired, InvalidDraft, VersionConflict
from skill.models import (
    Skill,
    SkillAction,
    SkillAssetRole,
    SkillChange,
    SkillContent,
    SkillDraft,
    SkillRequirement,
    SkillScope,
)
from skill.serializers import ASSET_KEY_RE, AssetChecksSerializer, SkillCreateSerializer, SkillDraftSerializer
from user.models import Department
from utils.exceptions import Conflict

from ..sandbox import workspace_path
from ..skill_edit import access_label, edit_entry, edit_target
from ..skills import asset_path, draft_asset_path, scope_label
from . import ALL_TOOLS
from .base import Effect, Tool, ToolContext, ToolResult
from .files import call_helper, helper_error

SCOPES = {"personal": SkillScope.PERSONAL, "department": SkillScope.DEPARTMENT, "company": SkillScope.COMPANY}
REQUIREMENTS = {
    "tool_calling": SkillRequirement.TOOL_CALLING,
    "image_input": SkillRequirement.IMAGE_INPUT,
    "document_input": SkillRequirement.DOCUMENT_INPUT,
    "structured_output": SkillRequirement.STRUCTURED_OUTPUT,
}
ROLES = {
    "reference": SkillAssetRole.REFERENCE,
    "master": SkillAssetRole.MASTER,
    "template": SkillAssetRole.TEMPLATE,
    "script": SkillAssetRole.SCRIPT,
}
NO_TARGET = "No skill has been created in this work yet. Create it with create_skill first."
DRAFT_CHANGED = (
    "The draft was changed by someone else (now revision {revision}). Call get_skill to read it again, then apply "
    "your change to the latest draft."
)
NO_DRAFT = (
    "There is no draft now (it may have been published or discarded). Call get_skill, and use expected_revision 0 "
    "to open a new draft from the published version."
)


class _Abort(Exception):
    """Draftの変更を取りやめる（開いたDraftも残さない）"""

    def __init__(self, result: ToolResult):
        self.result = result


# --- 共通 ---


def _request(context: ToolContext) -> SimpleNamespace:
    """管理APIのSerializerに渡すrequestの代わり。権限の判定に使うのは利用者だけ"""
    return SimpleNamespace(user=context.run.owner)


def _target(context: ToolContext, needed: SkillAction = SkillAction.EDIT) -> tuple[Skill | None, ToolResult | None]:
    skill = edit_target(context.run)
    if skill is None:
        return None, ToolResult.error(NO_TARGET)
    if skill.deleted:
        return None, ToolResult.error("This skill was deleted. Stop and tell the user.")
    user = context.run.owner
    if not skill.can(user, SkillAction.EDIT):
        return None, ToolResult.error("The user can no longer edit this skill. Stop and tell the user.")
    if not skill.can(user, needed):
        return None, ToolResult.error(
            "Only a person with the publish role for this skill can do this. Tell the user that it was not done."
        )
    return skill, None


def change_value(
    skill: Skill,
    state: str,
    *,
    version: int | None = None,
    revision: int | None = None,
    summary: dict[str, Any] | None = None,
    asset_diffs: dict[str, dict[str, int]] | None = None,
) -> dict[str, Any]:
    """画面に出すSkillの変更（design.md §11.6）。state は created・draft_saved・published・rolled_back。
    asset_diffs は置き換えた資料の差分の数（資料のkeyごと）"""
    return {
        "skill": skill.uid,
        "name": skill.name,
        "scope": scope_label(skill),
        "state": state,
        "version": version,
        "revision": revision,
        "summary": summary,
        "asset_diffs": asset_diffs or {},
    }


def _errors_text(errors: Any) -> str:
    if isinstance(errors, dict):
        return "; ".join(f"{field}: {_errors_text(value)}" for field, value in errors.items())
    if isinstance(errors, list):
        return " ".join(_errors_text(e) for e in errors)
    return str(errors)


def _invalid(errors: Any) -> ToolResult:
    return ToolResult.error(f"The change was not saved: {_errors_text(errors)}. Fix it and call again.")


def _with_draft(skill: Skill, expected: int, actor, change: Callable[[SkillDraft, int], ToolResult]) -> ToolResult:
    """Draftを変える。Draftがなく `expected` が0なら、公開中の版から開いてから変える。`change` が誤りを返したら、
    開いたDraftも残さない"""
    try:
        with transaction.atomic():
            draft = SkillDraft.objects.filter(skill=skill).first()
            if draft is None:
                if expected != 0:
                    return ToolResult.error(NO_DRAFT)
                draft = SkillDraft.open(skill, actor=actor)
                expected = draft.revision
            elif draft.revision != expected:
                return ToolResult.error(DRAFT_CHANGED.format(revision=draft.revision))
            result = change(draft, expected)
            if result.is_error:
                raise _Abort(result)
            return result
    except _Abort as abort:
        return abort.result
    except (VersionConflict, DraftExists, Conflict):
        latest = SkillDraft.objects.filter(skill=skill).values_list("revision", flat=True).first()
        return ToolResult.error(DRAFT_CHANGED.format(revision=latest) if latest else NO_DRAFT)


def _operation_key(context: ToolContext) -> str:
    """公開・切戻しの重複を防ぐkey。同じ呼出しのやり直しでは同じ値になる（harness.md §11.1）"""
    key = f"run-tool:{context.invocation}"
    ToolInvocation.objects.filter(uid=context.invocation).update(operation_key=key)
    return key


# --- get_skill ---


def _content_lines(content: SkillContent, assets: list[tuple[str, Any]]) -> list[str]:
    requires = [name for name, value in REQUIREMENTS.items() if value in content.requires]
    lines = [
        f"<description>{content.description}</description>",
        f"<instructions>\n{content.instructions}\n</instructions>",
        f"<tools>{', '.join(content.tools) or 'none'}</tools>",
        f"<requires>{', '.join(requires) or 'none'}</requires>",
    ]
    for name in ("input_schema", "output_schema"):
        value = getattr(content, name)
        lines.append(f"<{name}>{json.dumps(value, ensure_ascii=False) if value is not None else 'none'}</{name}>")
    if assets:
        lines.append("<assets>")
        for path, asset in assets:
            resource = asset.resource_version
            role = next(name for name, value in ROLES.items() if value == asset.role)
            description = f" — {asset.description}" if asset.description else ""
            lines.append(
                f'  key="{asset.key}" role={role} file version {resource.number}, {resource.size} bytes: '
                f"{path}{description}"
            )
            if asset.checks:
                lines.append(f"    checks: {json.dumps(asset.checks, ensure_ascii=False)}")
            if asset.validation:
                lines.append(f"    validation: {formats.describe(asset.validation)}")
        lines.append("</assets>")
    else:
        lines.append("<assets>none</assets>")
    return lines


GET_SKILL_DESCRIPTION = """Read the skill you are editing: the published version and the draft, with their files.

Call this first, and again after the draft may have changed. The skill's files are placed read-only under \
/workspace/master: the published version's files under skills/, the draft's files under drafts/. To change a file, \
copy it to /workspace/work, edit the copy, and register it with stage_skill_asset. Use the draft's revision as \
expected_revision when you change the draft.

A draft may already hold changes someone else made and has not published. Keep them, and before you publish, tell \
the user that they will be published together with your change."""

GET_SKILL_PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}


def execute_get_skill(_arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    skill, error = _target(context)
    if skill is None:
        return error or ToolResult.error(NO_TARGET)
    session = context.sandbox_session()
    access = access_label(skill, context.run.owner)
    lines = [f'<skill name="{skill.name}" scope="{scope_label(skill)}" your_access="{access}">']
    version = (
        skill.versions.prefetch_related("assets__resource_version__blob").filter(pk=skill.current_version_id).first()
        if skill.current_version_id is not None
        else None
    )
    if version is not None:
        session.place_skill_assets(skill, version)
        assets = [(asset_path(skill.uid, a.key, a.resource_version.name), a) for a in version.assets.all()]
        state = " suspended" if skill.suspended else ""
        lines.append(f'<published version="{version.number}"{state}>')
        lines += _content_lines(version, sorted(assets, key=lambda pair: pair[1].key))
        lines.append("</published>")
    else:
        lines.append("<published>none (not published yet)</published>")
    draft = SkillDraft.objects.prefetch_related("assets__resource_version__blob").filter(skill=skill).first()
    if draft is not None:
        session.place_draft_assets(skill, draft)
        draft_assets = [
            (draft_asset_path(skill.uid, draft.revision, a.key, a.resource_version.name), a) for a in draft.assets.all()
        ]
        base = draft.base_version.number if draft.base_version else "none"
        lines.append(f'<draft revision="{draft.revision}" base_version="{base}">')
        lines += _content_lines(draft, sorted(draft_assets, key=lambda pair: pair[1].key))
        lines.append("</draft>")
    else:
        lines.append(
            "<draft>none. update_skill_draft or stage_skill_asset with expected_revision 0 opens a draft from the "
            "published version.</draft>"
        )
    lines.append("</skill>")
    return ToolResult.text(
        "\n".join(lines),
        value={"skill": skill.uid, "version": skill.version_number, "revision": draft.revision if draft else None},
        meta={"skill": skill.name},
    )


GET_SKILL = Tool(
    "get_skill",
    GET_SKILL_DESCRIPTION,
    GET_SKILL_PARAMETERS,
    execute_get_skill,
    Effect.READ,
    replay_safe=True,
    needs_sandbox=True,
)


# --- create_skill ---

CREATE_SKILL_DESCRIPTION = """Create the new skill this work is about, with its first draft. Only once per work.

Use the personal scope unless the user asked for a department or the whole company; a department or company skill \
needs the user's role there. Write the description so that the agent and employees can tell when to use the skill, \
and write general instructions: do not copy customer data from this conversation into the skill. Then publish it \
with publish_skill_draft."""

CREATE_SKILL_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 100, "description": "Short name, e.g. 請求書の照合."},
        "scope": {"type": "string", "enum": list(SCOPES), "description": "Who can use the skill."},
        "department": {"type": "string", "maxLength": 100, "description": "Department name, for the department scope."},
        "description": {"type": "string", "minLength": 1, "maxLength": 2000, "description": "When to use the skill."},
        "instructions": {"type": "string", "minLength": 1, "maxLength": 100000, "description": "The procedure."},
    },
    "required": ["name", "scope", "description", "instructions"],
    "additionalProperties": False,
}


def execute_create_skill(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    run = context.run
    if (edit_entry(run) or {}).get("skill"):
        return ToolResult.error("The skill for this work already exists. Edit it with update_skill_draft.")
    data: dict[str, Any] = {
        "scope": SCOPES[arguments["scope"]],
        "name": arguments["name"],
        "description": arguments["description"],
        "instructions": arguments["instructions"],
    }
    if arguments["scope"] == "department":
        name = arguments.get("department", "").strip()
        department = Department.objects.active().filter(name=name).first() if name else None
        if department is None:
            names = ", ".join(f'"{d.name}"' for d in Department.objects.manageable_by(run.owner).order_by("name"))
            return ToolResult.error(
                f"No department named {name!r}. Departments where the user can manage skills: {names or 'none'}. "
                "Call again with one of them."
            )
        data["department"] = department.uid
    serializer = SkillCreateSerializer(data=data, context={"request": _request(context)})
    try:
        if not serializer.is_valid():
            return _invalid(serializer.errors)
    except drf_exceptions.PermissionDenied as exc:
        return ToolResult.error(f"{exc.detail} Use a scope where the user can create skills, such as personal.")
    with transaction.atomic():
        skill: Skill = serializer.save(actor=run.owner)
        with run.fenced(context.attempt) as current:
            current.append_manifest("skill_edit", {"skill": skill.uid, "name": skill.name})
            current.save(update_fields=["manifest", "updated"])
            run.manifest = current.manifest
    revision = SkillDraft.objects.filter(skill=skill).values_list("revision", flat=True).first()
    return ToolResult.text(
        f'Created the skill "{skill.name}" ({scope_label(skill)}) with a draft at revision {revision}. It is not '
        "published yet: add files if needed, then call publish_skill_draft.",
        value=change_value(skill, "created", revision=revision),
        meta={"skill": skill.name},
    )


CREATE_SKILL = Tool(
    "create_skill", CREATE_SKILL_DESCRIPTION, CREATE_SKILL_PARAMETERS, execute_create_skill, Effect.WRITE_EXTERNAL
)


# --- update_skill_draft ---

UPDATE_DRAFT_DESCRIPTION = """Change the draft of the skill you are editing. Only the fields you pass are changed.

- expected_revision: the draft's revision from get_skill. Use 0 when there is no draft; a draft is opened from the \
published version. If the draft changed meanwhile, the call fails: call get_skill and apply your change again.
- Change only what the user asked for, and keep the rest of the text as it is: pass the whole new text of a field.
- input_schema and output_schema are JSON Schemas written as a JSON string; an empty string clears them.
- Changing tools needs the publish role."""

UPDATE_DRAFT_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "expected_revision": {"type": "integer", "minimum": 0},
        "description": {"type": "string", "maxLength": 2000},
        "instructions": {"type": "string", "maxLength": 100000},
        "tools": {"type": "array", "items": {"type": "string", "enum": [t.name for t in ALL_TOOLS]}, "maxItems": 50},
        "requires": {"type": "array", "items": {"type": "string", "enum": list(REQUIREMENTS)}, "maxItems": 10},
        "input_schema": {"type": "string", "maxLength": 50000},
        "output_schema": {"type": "string", "maxLength": 50000},
    },
    "required": ["expected_revision"],
    "additionalProperties": False,
}


def execute_update_skill_draft(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    skill, error = _target(context)
    if skill is None:
        return error or ToolResult.error(NO_TARGET)
    data: dict[str, Any] = {}
    for name in ("description", "instructions", "tools"):
        if name in arguments:
            data[name] = arguments[name]
    if "requires" in arguments:
        data["requires"] = [REQUIREMENTS[name] for name in arguments["requires"]]
    for name in ("input_schema", "output_schema"):
        if name in arguments:
            text = arguments[name].strip()
            try:
                data[name] = json.loads(text) if text else None
            except json.JSONDecodeError as exc:
                return ToolResult.error(f"{name} is not valid JSON ({exc}). Pass a JSON Schema as a JSON string.")
    if not data:
        return ToolResult.error("Pass at least one field to change.")
    actor = context.run.owner

    def change(draft: SkillDraft, expected: int) -> ToolResult:
        serializer = SkillDraftSerializer(
            draft, data={**data, "revision": expected}, partial=True, context={"request": _request(context)}
        )
        try:
            if not serializer.is_valid():
                return _invalid(serializer.errors)
            serializer.save(actor=actor)
        except drf_exceptions.PermissionDenied as exc:
            return ToolResult.error(f"{exc.detail} Tell the user that the tools were not changed.")
        return ToolResult.text(
            f"Saved the draft (now revision {draft.revision}). Changed: {', '.join(data)}. It is not published yet.",
            value=change_value(skill, "draft_saved", revision=draft.revision, summary={"fields": list(data)}),
            meta={"skill": skill.name},
        )

    return _with_draft(skill, arguments["expected_revision"], actor, change)


UPDATE_SKILL_DRAFT = Tool(
    "update_skill_draft",
    UPDATE_DRAFT_DESCRIPTION,
    UPDATE_DRAFT_PARAMETERS,
    execute_update_skill_draft,
    Effect.WRITE_EXTERNAL,
)


# --- stage_skill_asset ---

STAGE_ASSET_DESCRIPTION = """Add a file in the sandbox to the draft as a skill file, or replace the file with the \
same key. The bytes are stored exactly as they are.

- To change a file, copy it from /workspace/master to /workspace/work, edit it, and stage the edited copy.
- To use a file the user attached as it is (e.g. "use this CSV as the master from now on"), stage it directly from \
/workspace/input. Do not regenerate it.
- key: the file's stable name in the skill (lowercase letters, digits, - and _). Use the existing key to replace a \
file; a new key adds a file. role is required for a new file.
- expected_revision: as in update_skill_draft (0 opens a draft).
- The file is checked by its format, and the result shows the errors and the changes from the published version \
(MD: lines, CSV: rows by the key columns, PPTX: text and structure per slide). Check that the changes are what the \
user asked for. A draft with errors in a changed file cannot be published: fix the file and stage it again.
- CSV: keep the encoding, delimiter and header. Read and write every value as a string (in pandas, dtype=str and \
keep_default_na=False) so that codes keep their leading zeros, and change only the rows asked for.
- PPTX: edit a copy of the original with python-pptx and change text only. Pictures, charts, SmartArt, media and \
layouts cannot be changed by editing; ask the user to upload the finished file instead.
- checks: the CSV checks (required columns, key columns, column types, encoding, delimiter). Changing them needs \
the publish role; omit it to keep the current checks."""

STAGE_ASSET_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "expected_revision": {"type": "integer", "minimum": 0},
        "key": {"type": "string", "minLength": 1, "maxLength": 63, "description": "e.g. vendor-master."},
        "path": {"type": "string", "description": "Absolute path of the file, e.g. /workspace/work/vendor-master.csv."},
        "role": {"type": "string", "enum": list(ROLES)},
        "description": {"type": "string", "maxLength": 500, "description": "What the file is for."},
        "checks": {
            "type": "object",
            "properties": {
                "encoding": {"type": "string", "enum": ["", *formats.CSV_ENCODINGS], "description": "Empty: detect."},
                "delimiter": {"type": "string", "enum": ["", *formats.CSV_DELIMITERS], "description": "Empty: detect."},
                "required_columns": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
                "key_columns": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
                "column_types": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "column": {"type": "string"},
                            "type": {"type": "string", "enum": list(formats.COLUMN_TYPES)},
                        },
                        "required": ["column", "type"],
                        "additionalProperties": False,
                    },
                    "maxItems": 100,
                },
            },
            "additionalProperties": False,
        },
    },
    "required": ["expected_revision", "key", "path"],
    "additionalProperties": False,
}


def _read_sandbox_file(given: str, context: ToolContext) -> tuple[str, bytes] | ToolResult:
    """Sandboxの中のファイルを、そのままのbytesで読む（symlinkは辿った先）。読めなければ誤りの結果"""
    path = workspace_path(given)
    if path is None:
        return ToolResult.error(f"{given} is outside /workspace. Use an absolute path inside /workspace.")
    stat = call_helper(context, "stat", {"path": path})
    if not stat.get("ok"):
        return helper_error(stat)
    if not stat.get("exists") or stat.get("type") != "file":
        return ToolResult.error(f"{path} is not a file. Check the path with glob.")
    limit = settings.YORIKI_UPLOAD_MAX_MB * 1024 * 1024
    if stat.get("size", 0) > limit:
        return ToolResult.error(f"{path} is too large for a skill file (up to {settings.YORIKI_UPLOAD_MAX_MB} MB).")
    try:
        return stat["path"], context.sandbox_session().read_file(stat["path"], limit)
    except (FileNotFoundError, ValueError) as exc:
        return ToolResult.error(f"{path} could not be read as a file ({exc}).")


def execute_stage_skill_asset(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    skill, error = _target(context)
    if skill is None:
        return error or ToolResult.error(NO_TARGET)
    key = arguments["key"]
    if not ASSET_KEY_RE.match(key):
        return ToolResult.error(
            "key must use lowercase letters, digits, - and _, and start with a letter or digit, e.g. vendor-master."
        )
    read = _read_sandbox_file(arguments["path"], context)
    if isinstance(read, ToolResult):
        return read
    path, body = read
    filename = posixpath.basename(path)
    role = ROLES[arguments["role"]] if "role" in arguments else None
    description = arguments.get("description")
    checks: dict[str, Any] | None = None
    if "checks" in arguments:
        serializer = AssetChecksSerializer(data=arguments["checks"])
        if not serializer.is_valid():
            return _invalid(serializer.errors)
        checks = dict(serializer.validated_data)
    # 添付（/workspace/input）からはそのままの置換、それ以外はエージェントが編集した候補として検証する
    edited = not path.startswith("/workspace/input/")
    actor = context.run.owner

    def change(draft: SkillDraft, expected: int) -> ToolResult:
        existing = draft.assets.filter(key=key).first()
        # 外した資料を同じkeyで加え直すときは、元の版の条件と比べる
        base = draft.base_asset(key) if existing is None else None
        current_checks = formats.normalize_checks(existing.checks if existing else base.checks if base else None)
        if checks is not None and checks != current_checks and not skill.can(actor, SkillAction.PUBLISH):
            return ToolResult.error(
                "Changing the checks needs the publish role for this skill. Call again without checks."
            )
        try:
            if existing is not None:
                asset = draft.change_asset(
                    existing,
                    actor=actor,
                    expected_revision=expected,
                    role=role,
                    description=description,
                    src=io.BytesIO(body),
                    filename=filename,
                    checks=checks,
                    edited=edited,
                )
                action = "Replaced"
            else:
                if role is None:
                    return ToolResult.error("role is required for a new file. Call again with role.")
                asset = draft.add_asset(
                    key=key,
                    role=role,
                    description=description or "",
                    src=io.BytesIO(body),
                    filename=filename,
                    actor=actor,
                    expected_revision=expected,
                    checks=checks,
                    edited=edited,
                )
                action = "Added"
        except InvalidDraft as exc:
            return ToolResult.error(str(exc))
        resource = asset.resource_version
        errors = asset.validation.get("errors")
        status = (
            "It has errors, so the draft cannot be published until the file is fixed and staged again."
            if errors
            else "It is not published yet."
        )
        return ToolResult.text(
            f'{action} the file "{key}" ({filename}, file version {resource.number}, {resource.size} bytes) in the '
            f"draft (now revision {draft.revision}). {status}\n"
            f"Check: {formats.describe(asset.validation)}{_diff_lines(asset.validation)}",
            value=change_value(
                skill,
                "draft_saved",
                revision=draft.revision,
                summary={"assets_replaced" if existing else "assets_added": [key]},
                asset_diffs={key: counts} if (counts := formats.diff_counts(asset.validation)) else None,
            ),
            meta={"skill": skill.name, "path": path},
        )

    return _with_draft(skill, arguments["expected_revision"], actor, change)


def _diff_lines(validation: dict[str, Any], limit: int = 40) -> str:
    """差分の中身の一部（エージェントが意図した変更かを確かめるため）"""
    diff = validation.get("diff") or {}
    lines: list[str] = list(diff.get("text") or [])
    for row in diff.get("rows") or []:
        cells = ", ".join(f"{c['column']}: {c['before']!r} -> {c['after']!r}" for c in row["cells"])
        lines.append(f"{row['change']} {row['key'] or ''} {cells}".strip())
    for slide in diff.get("slides") or []:
        lines.append(f"slide {slide['slide']} {slide['change']}")
        lines += [f"  {t['shape']}: {t['before']!r} -> {t['after']!r}" for t in slide["texts"]]
        lines += [f"  {e['kind']}: {e['before']} -> {e['after']}" for e in slide["elements"]]
    if not lines:
        return ""
    more = "\n..." if len(lines) > limit or diff.get("truncated") else ""
    return "\n<diff>\n" + "\n".join(lines[:limit]) + more + "\n</diff>"


STAGE_SKILL_ASSET = Tool(
    "stage_skill_asset",
    STAGE_ASSET_DESCRIPTION,
    STAGE_ASSET_PARAMETERS,
    execute_stage_skill_asset,
    Effect.WRITE_EXTERNAL,
    needs_sandbox=True,
)


# --- remove_skill_asset ---

REMOVE_ASSET_DESCRIPTION = """Remove a file from the draft of the skill you are editing. The published version is \
not changed until the draft is published. expected_revision: as in update_skill_draft (0 opens a draft)."""

REMOVE_ASSET_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "expected_revision": {"type": "integer", "minimum": 0},
        "key": {"type": "string", "minLength": 1, "maxLength": 63},
    },
    "required": ["expected_revision", "key"],
    "additionalProperties": False,
}


def execute_remove_skill_asset(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    skill, error = _target(context)
    if skill is None:
        return error or ToolResult.error(NO_TARGET)
    key = arguments["key"]
    actor = context.run.owner

    def change(draft: SkillDraft, expected: int) -> ToolResult:
        asset = draft.assets.filter(key=key).first()
        if asset is None:
            keys = ", ".join(draft.assets.values_list("key", flat=True)) or "none"
            return ToolResult.error(f'The draft has no file "{key}". Its files: {keys}.')
        draft.remove_asset(asset, actor=actor, expected_revision=expected)
        return ToolResult.text(
            f'Removed the file "{key}" from the draft (now revision {draft.revision}). It is not published yet.',
            value=change_value(skill, "draft_saved", revision=draft.revision, summary={"assets_removed": [key]}),
            meta={"skill": skill.name},
        )

    return _with_draft(skill, arguments["expected_revision"], actor, change)


REMOVE_SKILL_ASSET = Tool(
    "remove_skill_asset",
    REMOVE_ASSET_DESCRIPTION,
    REMOVE_ASSET_PARAMETERS,
    execute_remove_skill_asset,
    Effect.WRITE_EXTERNAL,
)


# --- publish_skill_draft ---

PUBLISH_DESCRIPTION = """Publish the draft of the skill you are editing as a new version. New requests use it from \
now on; work already running keeps the version it started with.

If the user does not have the publish role, the draft is kept as a proposal for a publisher and nothing is \
published; tell the user so. expected_revision is the draft's revision from get_skill. change_reason is a short \
summary of the change, shown in the skill's history."""

PUBLISH_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "expected_revision": {"type": "integer", "minimum": 1},
        "change_reason": {"type": "string", "minLength": 1, "maxLength": 500},
    },
    "required": ["expected_revision", "change_reason"],
    "additionalProperties": False,
}


def _done(skill: Skill, key: str) -> SkillChange | None:
    return SkillChange.objects.filter(skill=skill, operation_key=key).select_related("version").first()


def _published(skill: Skill, change: SkillChange, state: str) -> ToolResult:
    assert change.version is not None
    number = change.version.number
    verb = "Published" if state == "published" else "Rolled back the skill as"
    return ToolResult.text(
        f'{verb} version {number} of "{skill.name}" ({scope_label(skill)}). New requests use it from now on; work '
        "already running keeps its version.",
        value=change_value(
            skill,
            state,
            version=number,
            summary={k: v for k, v in change.summary.items() if k != "asset_diffs"},
            asset_diffs=change.summary.get("asset_diffs"),
        ),
        meta={"skill": skill.name},
    )


def _saved_only(skill: Skill, expected: int) -> ToolResult:
    """公開の役割がないときは、公開せずにDraftのまま残す（design.md §3.6）"""
    draft = SkillDraft.objects.filter(skill=skill).first()
    if draft is None:
        return ToolResult.error(NO_DRAFT)
    if draft.revision != expected:
        return ToolResult.error(DRAFT_CHANGED.format(revision=draft.revision))
    return ToolResult.text(
        f"Not published: the user does not have the publish role for this skill. The change is saved as a draft "
        f"(revision {draft.revision}) for a person with the publish role. Tell the user that it is saved as a "
        "proposal and not used yet.",
        value=change_value(skill, "draft_saved", revision=draft.revision),
        meta={"skill": skill.name},
    )


def _publish(skill: Skill, arguments: dict[str, Any], context: ToolContext, key: str) -> ToolResult | None:
    """Draftを公開する。公開できなければ誤りの結果、公開したらNone"""
    draft = SkillDraft.objects.filter(skill=skill).first()
    if draft is None:
        return ToolResult.error(NO_DRAFT)
    try:
        skill.publish_draft(
            draft.uid,
            actor=context.run.owner,
            expected_revision=arguments["expected_revision"],
            change_reason=arguments["change_reason"],
            operation_key=key,
            run=context.run,
        )
    except VersionConflict:
        stale = draft.base_version_id != skill.current_version_id
        return ToolResult.error(
            "Another version was published after this draft was opened, so the draft cannot be published. Tell the "
            "user to review the draft on the skill's page."
            if stale
            else DRAFT_CHANGED.format(revision=draft.revision)
        )
    except EvaluationRequired as exc:
        names = ", ".join(f'"{name}"' for name in exc.cases)
        return ToolResult.error(
            f"The draft cannot be published: the required test cases {names} have not passed with the current draft. "
            "Test it with test_skill_draft, and fix the draft if a case fails."
        )
    except InvalidDraft as exc:
        return ToolResult.error(f"The draft cannot be published: {exc} Fix it with update_skill_draft.")
    return None


def execute_publish_skill_draft(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    skill, error = _target(context)
    if skill is None:
        return error or ToolResult.error(NO_TARGET)
    if not skill.can(context.run.owner, SkillAction.PUBLISH):
        return _saved_only(skill, arguments["expected_revision"])
    key = _operation_key(context)
    # 同じ呼出しのやり直しで、前の試みが公開を済ませていれば、その結果を返す
    if _done(skill, key) is None and (failed := _publish(skill, arguments, context, key)) is not None:
        return failed
    done = _done(skill, key)
    assert done is not None
    return _published(skill, done, "published")


PUBLISH_SKILL_DRAFT = Tool(
    "publish_skill_draft", PUBLISH_DESCRIPTION, PUBLISH_PARAMETERS, execute_publish_skill_draft, Effect.WRITE_EXTERNAL
)


# --- rollback_skill ---

ROLLBACK_DESCRIPTION = """Make a past version of the skill you are editing the published version again, by \
publishing a new version with the same content and files. Needs the publish role. expected_version is the \
published version from get_skill."""

ROLLBACK_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "number": {"type": "integer", "minimum": 1, "description": "The past version to go back to."},
        "expected_version": {"type": "integer", "minimum": 1},
        "change_reason": {"type": "string", "minLength": 1, "maxLength": 500},
    },
    "required": ["number", "expected_version", "change_reason"],
    "additionalProperties": False,
}


def execute_rollback_skill(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    skill, error = _target(context, SkillAction.PUBLISH)
    if skill is None:
        return error or ToolResult.error(NO_TARGET)
    run = context.run
    key = _operation_key(context)
    if (done := _done(skill, key)) is None:
        try:
            skill.rollback(
                arguments["number"],
                actor=run.owner,
                expected_version=arguments["expected_version"],
                change_reason=arguments["change_reason"],
                operation_key=key,
                run=run,
            )
        except VersionConflict:
            return ToolResult.error(
                f"The published version changed (now version {skill.version_number}). Call get_skill and check again."
            )
        except InvalidDraft as exc:
            return ToolResult.error(str(exc))
        done = _done(skill, key)
        assert done is not None
    return _published(skill, done, "rolled_back")


ROLLBACK_SKILL = Tool(
    "rollback_skill", ROLLBACK_DESCRIPTION, ROLLBACK_PARAMETERS, execute_rollback_skill, Effect.WRITE_EXTERNAL
)

SKILL_ADMIN_TOOLS: tuple[Tool, ...] = (
    GET_SKILL,
    UPDATE_SKILL_DRAFT,
    STAGE_SKILL_ASSET,
    REMOVE_SKILL_ASSET,
    PUBLISH_SKILL_DRAFT,
    ROLLBACK_SKILL,
)
