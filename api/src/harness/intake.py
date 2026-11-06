"""動いているRunへの発言で指定したSkill・Connectorを、取り込むときに確かめる（harness.md §10.1・§10.2）。

発言を受け付けるときにも確かめる（conversation/dispatch.py）が、取り込むまでに権限や公開範囲が変わりうる。使えなく
なったものは文脈に入れず、そのことを画面に示す。Skillは `skill` の読込みと同じく、その時点の最新の公開版を読み込む。
Toolの一覧は変えないので、このRunにないToolを要するSkillは読み込まず、Runの後に指定し直すよう案内させる。
"""

from dataclasses import dataclass, field
from typing import Any

from django.utils.translation import gettext as _

from connector.models import Connector
from conversation.models import ConversationMessage
from run.models import Run
from skill.models import Skill, SkillVersion

from .connectors import DATASOURCE_TOOL_NAMES, ConnectorUnavailable
from .connectors import resolve_explicit as resolve_explicit_connectors
from .skills import loaded_entries, manifest_entry

UNUSABLE_NOTE = (
    "[Some skills or connectors the user chose cannot be used now, so they were not loaded. Continue without them, "
    "and tell the user.]"
)
MISSING_TOOLS_NOTE = (
    '[The user chose the skill "{name}", but it needs tools that are not available in this work: {tools}. It was not '
    "loaded. Continue without it, and tell the user to name it with / again after this work ends.]"
)


@dataclass
class ChosenSkill:
    skill: Skill
    version: SkillVersion
    entry: dict[str, Any]


@dataclass
class Chosen:
    """1つの発言で指定したもののうち、取り込むもの（`skills`・`connectors`）と、取り込まないものの知らせ"""

    skills: list[ChosenSkill] = field(default_factory=list)
    connectors: list[dict[str, Any]] = field(default_factory=list)
    unusable: list[str] = field(default_factory=list)  # 画面に示す（名前と理由）
    notes: list[str] = field(default_factory=list)  # モデルに知らせる

    def drop_skill(self, chosen: ChosenSkill, reason: str) -> None:
        self.skills.remove(chosen)
        self.unusable.append(_("%(name)s：%(reason)s") % {"name": chosen.skill.name, "reason": reason})

    def model_notes(self) -> list[str]:
        return [UNUSABLE_NOTE, *self.notes] if self.unusable else self.notes


def take_chosen(run: Run, message: ConversationMessage, exposed: frozenset[str]) -> Chosen:
    """発言で指定したSkill・Connectorを、依頼した人の今の権限で確かめる（DBへは書かない）"""
    chosen = Chosen()
    _take_skills(run, message, exposed, chosen)
    _take_connectors(run, message, exposed, chosen)
    return chosen


def _take_skills(run: Run, message: ConversationMessage, exposed: frozenset[str], chosen: Chosen) -> None:
    ids = [s["id"] for s in message.skills]
    skills = {
        s.uid: s
        for s in Skill.objects.filter(uid__in=ids, deleted=False).select_related("current_version", "department")
    }
    loaded = {e["skill"]: e for e in loaded_entries(run)}
    for choice in message.skills:
        skill = skills.get(choice["id"])
        reason = (
            skill.unusable_reason(run.owner, run.model_version, sensitive=run.handles_sensitive_data)
            if skill is not None
            else str(_("削除されました"))
        )
        if skill is None or reason:
            chosen.unusable.append(_("%(name)s：%(reason)s") % {"name": choice["name"], "reason": reason})
            continue
        assert skill.current_version_id is not None  # unusable_reason() が確かめた
        version = SkillVersion.objects.prefetch_related("assets__resource_version").get(pk=skill.current_version_id)
        if missing := [name for name in version.tools if name not in exposed]:
            chosen.notes.append(MISSING_TOOLS_NOTE.format(name=skill.name, tools=", ".join(missing)))
            continue
        entry = {**manifest_entry(skill, version), "chosen": True}
        previous = loaded.get(skill.uid)
        if previous is not None and previous.get("version") != version.pk and previous.get("number"):
            entry["replaces"] = previous["number"]
        chosen.skills.append(ChosenSkill(skill, version, entry))


def _take_connectors(run: Run, message: ConversationMessage, exposed: frozenset[str], chosen: Chosen) -> None:
    ids = [c["id"] for c in message.connectors]
    connectors = {
        c.uid: c
        for c in Connector.objects.filter(uid__in=ids, deleted=False).select_related(
            "connection", "current_version", "department"
        )
    }
    for choice in message.connectors:
        connector = connectors.get(choice["id"])
        if connector is None:
            reason = str(_("削除されました"))
        elif not set(DATASOURCE_TOOL_NAMES) & exposed:
            reason = str(_("この作業ではConnectorを使えません"))
        else:
            try:
                chosen.connectors += resolve_explicit_connectors(
                    [connector], run.owner, run.model_version, sensitive=run.handles_sensitive_data
                )
                continue
            except ConnectorUnavailable as exc:
                chosen.unusable.append(str(exc))
                continue
        chosen.unusable.append(_("%(name)s：%(reason)s") % {"name": choice["name"], "reason": reason})
