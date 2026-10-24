"""Skillを読み込む（harness.md §10.1）。引数の名前は、Run開始時に固定した一覧（Manifest）のenumにする"""

from typing import Any

from django.utils.translation import gettext as _

from run.models import RunEvent, RunEventKind
from skill.models import Skill, SkillAccess, SkillVersion

from ..skills import loaded_entries, manifest_entry, render
from .base import Effect, Tool, ToolContext, ToolResult

NAME = "skill"

DESCRIPTION = """Load a skill: the business procedure, reference files and rules for a kind of work.

The available skills are listed in <skills> in the runtime context, with their descriptions. When the request names \
a skill or clearly matches a skill's description, load it before you start the work, and follow its instructions. \
If several match, load the smallest set that covers the request. The list shows summaries only: do not guess a \
skill's procedure without loading it. If no skill matches, work without one.

The result contains the instructions, the paths of the skill's files (read-only under /workspace/master), the tools \
the skill uses, and the result schema if it has one. Loading the same skill again in the same work returns the same \
version."""


def make_skill_tool(names: list[str]) -> Tool:
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "enum": names, "description": "The skill name, exactly as listed in <skills>."}
        },
        "required": ["name"],
        "additionalProperties": False,
    }
    # 読み込みはManifestへの追記とRunEventの記録を伴うので、Runnerのスレッドで1つずつ実行する（並べない）
    return Tool(NAME, DESCRIPTION, parameters, execute, Effect.READ, replay_safe=True)


def execute(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    run = context.run
    catalog = ((run.manifest or {}).get("fixed") or {}).get("skill_catalog") or {}
    entry = next((e for e in catalog.get("skills", []) if e["name"] == arguments["name"]), None)
    if entry is None:
        return ToolResult.error(f'No skill named "{arguments["name"]}" is available. Use a name listed in <skills>.')
    skill = Skill.objects.select_related("current_version", "department").filter(uid=entry["skill"]).first()
    if skill is None or skill.deleted:
        return ToolResult.error("This skill was deleted. Continue without it, and tell the user.")
    user, model_version = run.owner, run.model_version

    loaded = next((e for e in loaded_entries(run) if e["skill"] == skill.uid), None)
    if loaded is not None:
        return _load_again(skill, loaded, entry["name"], user)
    if reason := skill.unusable_reason(user, model_version):
        return _unusable(reason)
    assert skill.current_version_id is not None  # unusable_reason() が確かめた
    version = SkillVersion.objects.prefetch_related("assets__resource_version").get(pk=skill.current_version_id)
    missing = [name for name in version.tools if name not in context.exposed]
    if missing:
        return ToolResult.error(
            f"This skill needs tools that are not available in this work: {', '.join(missing)}. Do not load it now. "
            "Tell the user to start a new request that names the skill with /."
        )
    # 資料を置けたことを確かめてから追記する。置けなければ追記しない（harness.md §10.1）。
    # Sandboxをまだ起動していなければ、起動するときにManifestの全Skillの資料を置く
    if context.session is not None and context.session.started:
        context.session.place_skill_assets(skill, version)
    value = manifest_entry(skill, version, entry["name"])
    with run.fenced(context.attempt) as current:
        current.append_manifest("skill", value)
        current.save(update_fields=["manifest", "updated"])
        RunEvent.record(current, RunEventKind.SKILL_LOADED, value, comment=skill.name)
        run.manifest = current.manifest
    return _loaded(skill, version, entry["name"])


def _load_again(skill: Skill, loaded: dict[str, Any], name: str, user) -> ToolResult:
    """同じRunでは追記した版を返す（途中で公開し直されても切り替えない）。停止や権限の失効は確かめ直す"""
    if skill.access_of(user) < SkillAccess.USE or skill.suspended:
        return _unusable(_("このSkillは使えなくなりました"))
    version = SkillVersion.objects.prefetch_related("assets__resource_version").get(pk=loaded["version"])
    return _loaded(skill, version, name)


def _loaded(skill: Skill, version: SkillVersion, name: str) -> ToolResult:
    return ToolResult.text(
        render(skill, version, name=name),
        value={"skill": skill.uid, "version": version.number},
        meta={"skill": skill.name, "version": version.number},
    )


def _unusable(reason: str) -> ToolResult:
    return ToolResult.error(f"This skill cannot be used now ({reason}). Continue without it, and tell the user.")
