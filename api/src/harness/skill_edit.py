"""Skillの編集のRun（harness.md §10.9、design.md §11.6）。

編集のRunは対象のSkillを1つだけ持ち、Run開始時にManifestの `fixed.skill_edit` へ固定する（新しいSkillを作る依頼では
空で始め、`create_skill` で作ったSkillを追記する）。Skill管理のToolは引数にSkillを取らず、対象のSkillにだけ働く。
"""

from typing import Any

from django.utils.translation import gettext as _

from run.models import Run, RunKind
from skill.connectors import skill_is_sensitive
from skill.models import Skill, SkillAction, SkillScope

from .skills import scope_label


def edit_entry(run: Run) -> dict[str, Any] | None:
    """編集のRunの対象（{"skill", "name"}。新しいSkillを作る依頼でまだ作っていなければ skill は空）。
    業務のRunならNone"""
    if run.kind != RunKind.SKILL_EDIT:
        return None
    manifest = run.manifest or {}
    entry = dict((manifest.get("fixed") or {}).get("skill_edit") or {})
    for appended in manifest.get("appended", []):
        if appended.get("kind") == "skill_edit":
            entry = {"skill": appended["skill"], "name": appended["name"]}
    return entry


def edit_target(run: Run) -> Skill | None:
    entry = edit_entry(run)
    if not entry or not entry.get("skill"):
        return None
    return Skill.objects.select_related("current_version", "department").filter(uid=entry["skill"]).first()


def editable_skills(user, *, sensitive: bool) -> list[Skill]:
    """利用者がDraftを変更できるSkill（個人・部門・全社の順。システムのSkillは会話で扱わない）。機密でないRunでは、
    機密のSkillを含めない（名前もモデルに見せない。harness.md §10.9）"""
    skills = Skill.objects.editable_by(user).in_conversation()
    found = list(skills.select_related("department", "current_version").order_by("scope", "name"))
    return found if sensitive else [s for s in found if not skill_is_sensitive(s)]


def labeled_name(skill: Skill) -> str:
    """範囲を添えた名前（「名寄せ (部門 経理部)」）。同じ名前のSkillを区別して指すのに使う"""
    return f"{skill.name} ({scope_label(skill)})"


def find_editable(user, name: str, *, sensitive: bool) -> tuple[Skill | None, str]:
    """名前（範囲を添えた名前も可）から、利用者が編集できるSkillを1つに決める。決まらなければ、モデル向けの理由"""
    name = name.strip()
    skills = editable_skills(user, sensitive=sensitive)
    matches = [s for s in skills if labeled_name(s) == name] or [s for s in skills if s.name == name]
    if len(matches) == 1:
        return matches[0], ""
    if matches:
        names = ", ".join(f'"{labeled_name(s)}"' for s in matches)
        return None, f"Several skills are named {name!r}: {names}. Call again with one of these names."
    if not skills:
        return None, (
            f"The user cannot edit any skill, including {name!r}. Tell the user that the change applies only to this "
            "work, and that a person who can edit the skill needs to change it."
        )
    names = ", ".join(f'"{labeled_name(s)}"' for s in skills[:30])
    return None, (
        f"The user cannot edit a skill named {name!r}. Skills the user can edit: {names}. Call again with one of "
        "them, or with an empty skill to create a new skill."
    )


def access_label(skill: Skill, user) -> str:
    return "publish" if skill.can(user, SkillAction.PUBLISH) else "edit"


def runtime_text(run: Run) -> str:
    """`<runtime_context>` の中の `<skill_edit>`。対象と、利用者がそのSkillでできること"""
    entry = edit_entry(run)
    if entry is None:
        return ""
    skill = edit_target(run)
    if skill is None:
        return '  <skill_edit new="true" />'
    return (
        f'  <skill_edit skill="{_escape(skill.name)}" scope="{_escape(scope_label(skill))}" '
        f'version="{skill.version_number}" your_access="{access_label(skill, run.owner)}" />'
    )


def confirmation_title(skill: Skill | None) -> str:
    """request_skill_edit の確認の文（画面に出すので日本語）"""
    if skill is None:
        return _("新しいSkillを作りますか？")
    if skill.scope == SkillScope.PERSONAL:
        return _("Skill「%(name)s」を編集しますか？") % {"name": skill.name}
    return _("Skill「%(name)s」（%(scope)s）を編集しますか？") % {"name": skill.name, "scope": scope_label(skill)}


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def revoked_edit(run: Run) -> str:
    """機密でない編集のRunで、対象のSkillが機密になった（Draftにフラグが付いた、宣言したConnectorが機密になった）
    ときの理由。読んだ中身を次のモデルの呼出しで送らないように、毎ステップの区切りで止める（harness.md §10.9）"""
    if run.handles_sensitive_data:
        return ""
    skill = edit_target(run)
    if skill is None or not skill_is_sensitive(skill):
        return ""
    return _(
        "Skill「%(name)s」が機密のSkillになったので、作業を止めました。個人情報・機密情報を扱う会話で編集してください。"
    ) % {"name": skill.name}
