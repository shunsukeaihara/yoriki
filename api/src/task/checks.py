"""タスクに登録できるもの（authorization.md §5.4）と、タスクのRunの実行許可（authorization.md §6）。

- 保存するとき（`content_errors()`）：Skill・Connectorがタスクの範囲で登録できるか、Skillが宣言したConnectorと操作が
  タスクに入っているか、機密の区分とモデル設定が合うか、プロンプトのテンプレートが入力の項目だけを使うか。
- 実行するとき（`skill_problem()`・`connector_permitted()`・`task_problem()`）：受付時、実行の区切りごと、Connectorの
  操作のたびに、固定したものが今も有効でタスクの範囲の中にあるかを確かめ直す。部門・全社・システムのタスクは、
  起動した人ではなくタスクの範囲で判定する（人が指示を出せない起動のため）。個人のタスクは本人の今の権限でも判定する。
"""

import re
from typing import Any, Protocol

from django.utils.translation import gettext as _

from connector.models import Connector, ConnectorAction, ConnectorScope, Operation
from skill import connectors as skill_connectors
from skill.models import Skill, SkillAction, SkillScope, SkillVersion

from .models import TaskScope

# 1つのタスクに登録できるSkill・Connector・入力の項目の数
MAX_TASK_SKILLS = 5
MAX_TASK_CONNECTORS = 10
MAX_INPUTS = 30
# 入力の項目の名前。テンプレートの `{{ inputs.名前 }}` に使う
INPUT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,49}$")
PLACEHOLDER_RE = re.compile(r"\{\{\s*inputs\.([A-Za-z0-9_]+)\s*\}\}")
DATASOURCE_TOOLS = set(Operation.values)

# タスクの範囲ごとに登録できるSkill・Connectorの範囲（authorization.md §5.4）。個人のタスクは本人が使えるもの。
# 部門のタスクの部門のものは、同じ部門のものだけ
SCOPE_SKILLS: dict[int, tuple[int, ...]] = {
    TaskScope.DEPARTMENT: (SkillScope.DEPARTMENT, SkillScope.COMPANY),
    TaskScope.COMPANY: (SkillScope.COMPANY,),
    TaskScope.SYSTEM: (SkillScope.SYSTEM,),
}
SCOPE_CONNECTORS: dict[int, tuple[int, ...]] = {
    TaskScope.DEPARTMENT: (ConnectorScope.DEPARTMENT, ConnectorScope.COMPANY),
    TaskScope.COMPANY: (ConnectorScope.COMPANY,),
    TaskScope.SYSTEM: (ConnectorScope.SYSTEM, ConnectorScope.DEPARTMENT, ConnectorScope.COMPANY),
}


class TaskLike(Protocol):
    """範囲の判定に使うタスクの項目（保存前のタスクでもよい）"""

    scope: int
    owner: Any
    department_id: int | None


def skill_allowed(task: TaskLike, skill: Skill) -> bool:
    """このタスクの範囲に登録できるSkillで、今も使える（公開中で停止していない）か"""
    if skill.deleted or skill.suspended or skill.current_version_id is None:
        return False
    if task.scope == TaskScope.PERSONAL:
        return task.owner is not None and skill.can(task.owner, SkillAction.USE)
    if skill.scope not in SCOPE_SKILLS.get(task.scope, ()):
        return False
    if skill.scope == SkillScope.DEPARTMENT and (skill.department is None or skill.department.deleted):
        return False  # 無効にした部門のものは新しく使わない（authorization.md §9）
    return not (skill.scope == SkillScope.DEPARTMENT and skill.department_id != task.department_id)


def connector_allowed(task: TaskLike, connector: Connector) -> bool:
    """このタスクの範囲に登録できるConnectorで、今も有効か"""
    if not connector.in_service():
        return False
    if task.scope == TaskScope.PERSONAL:
        return task.owner is not None and connector.can(task.owner, ConnectorAction.USE)
    if connector.scope not in SCOPE_CONNECTORS.get(task.scope, ()):
        return False
    if connector.scope == ConnectorScope.DEPARTMENT and (connector.department is None or connector.department.deleted):
        return False  # 無効にした部門のものは新しく使わない（authorization.md §9）
    if task.scope == TaskScope.DEPARTMENT and connector.scope == ConnectorScope.DEPARTMENT:
        return connector.department_id == task.department_id
    return True


def placeholders(template: str) -> list[str]:
    return list(dict.fromkeys(PLACEHOLDER_RE.findall(template)))


def load_skills(entries: list[dict[str, Any]]) -> dict[str, tuple[Skill, SkillVersion | None]]:
    """版に登録したSkill（IDごと。削除されたものも含む）と、固定した版"""
    skills = {
        s.uid: s
        for s in Skill.objects.filter(uid__in=[e["skill"] for e in entries]).select_related(
            "owner", "department", "current_version"
        )
    }
    versions = {
        (v.skill_id, v.number): v
        for v in SkillVersion.objects.filter(skill__in=skills.values(), number__in=[e["version"] for e in entries])
        .select_related("skill")
        .prefetch_related("assets__resource_version")
    }
    return {
        uid: (skill, versions.get((skill.pk, entry["version"])))
        for entry in entries
        if (skill := skills.get(uid := entry["skill"])) is not None
    }


def load_connectors(uids: list[str]) -> dict[str, Connector]:
    connectors = Connector.objects.filter(uid__in=uids).select_related("connection", "current_version", "department")
    return {c.uid: c for c in connectors}


class Errors(dict[str, list[str]]):
    """項目ごとの誤り"""

    def add(self, field: str, message: str) -> None:
        self.setdefault(field, []).append(message)


def content_errors(task: TaskLike, content: dict[str, Any]) -> dict[str, list[str]]:
    """版の中身を、タスクの範囲と今のSkill・Connector・モデル設定で確かめる。項目ごとの誤り（なければ空）"""
    errors = Errors()
    _check_connectors(task, content, errors)
    skills = load_skills(content["skills"])
    _check_skills(task, content, skills, errors)
    _check_model(content, skills, errors)
    names = {i["name"] for i in content["inputs"]}
    if unknown := [name for name in placeholders(content["prompt_template"]) if name not in names]:
        errors.add(
            "prompt_template", _("入力の項目にない名前を使っています：%(names)s") % {"names": "、".join(unknown)}
        )
    return errors


def _check_connectors(task: TaskLike, content: dict[str, Any], errors: Errors) -> None:
    sensitive = content["handles_sensitive_data"]
    connectors = load_connectors(content["connectors"])
    for uid in content["connectors"]:
        connector = connectors.get(uid)
        if connector is None or connector.deleted:
            errors.add("connectors", _("登録したConnectorが削除されました"))
        elif not connector_allowed(task, connector):
            errors.add("connectors", _("Connector「%(name)s」は、このタスクでは使えません") % {"name": connector.name})
        elif connector.current_version is not None and connector.current_version.is_sensitive and not sensitive:
            errors.add(
                "connectors",
                _("Connector「%(name)s」は機密なので、個人情報・機密情報を扱うタスクにしてください")
                % {"name": connector.name},
            )
    tools = set(content["tools"])
    if content["connectors"] and not tools & DATASOURCE_TOOLS:
        errors.add("tools", _("Connectorを使うには、使ってよいToolにConnectorの操作を加えてください"))
    if tools & DATASOURCE_TOOLS and not content["connectors"]:
        errors.add("connectors", _("Connectorの操作を使うには、使うConnectorを選んでください"))


def _check_skills(
    task: TaskLike, content: dict[str, Any], skills: dict[str, tuple[Skill, SkillVersion | None]], errors: Errors
) -> None:
    schemas = 0
    for entry in content["skills"]:
        skill, version = skills.get(entry["skill"], (None, None))
        if skill is None or skill.deleted:
            errors.add("skills", _("登録したSkillが削除されました"))
        elif version is None:
            errors.add(
                "skills",
                _("Skill「%(name)s」に版%(number)sはありません") % {"name": skill.name, "number": entry["version"]},
            )
        elif not skill_allowed(task, skill):
            errors.add("skills", _("Skill「%(name)s」は、このタスクでは使えません") % {"name": skill.name})
        else:
            schemas += 1 if version.output_schema else 0
            _check_declarations(skill, version, content, errors)
    if schemas > 1 and not content["output_schema"]:
        errors.add(
            "output_schema", _("業務結果の形を決めているSkillが複数あります。タスクの業務結果のSchemaを決めてください")
        )


def _check_declarations(skill: Skill, version: SkillVersion, content: dict[str, Any], errors: Errors) -> None:
    """Skillが宣言したConnectorと操作が、タスクのConnectorとToolに入っていて、境界の中にあるか（③bの宣言）"""
    declared = skill_connectors.load([version])
    if not content["handles_sensitive_data"] and skill_connectors.is_sensitive(version, declared):
        errors.add(
            "skills",
            _("Skill「%(name)s」は機密なので、個人情報・機密情報を扱うタスクにしてください") % {"name": skill.name},
        )
    for declaration in version.connectors or []:
        connector = declared.get(declaration["connector"])
        names = {"skill": skill.name, "name": connector.name if connector else declaration["connector"]}
        if declaration["connector"] not in content["connectors"]:
            errors.add(
                "connectors",
                _("Skill「%(skill)s」が使うConnector「%(name)s」を、使うConnectorに加えてください") % names,
            )
        elif connector is not None and skill_connectors.out_of_bounds(declaration, connector):
            errors.add(
                "skills", _("Skill「%(skill)s」が使う表・操作を、Connector「%(name)s」が今は公開していません") % names
            )
        if missing := [o for o in declaration["operations"] if o not in content["tools"]]:
            errors.add(
                "tools",
                _("Skill「%(skill)s」が使う操作（%(operations)s）を、使ってよいToolに加えてください")
                % {"skill": skill.name, "operations": "、".join(missing)},
            )


def _check_model(content: dict[str, Any], skills: dict[str, tuple[Skill, SkillVersion | None]], errors: Errors) -> None:
    profile = content["model_profile"]
    version = profile.current_version
    if profile.deleted or not profile.is_active or version is None:
        errors.add("model_profile", _("このモデル設定は使えません"))
        return
    if content["handles_sensitive_data"] and not version.is_local:
        errors.add("model_profile", _("個人情報や機密情報を扱うタスクでは、ローカルLLMのモデルを選んでください"))
    if content["effort"] and content["effort"] not in version.effort_values:
        errors.add("effort", _("このモデルが受け付けるeffortの値から選んでください"))
    schema = content["output_schema"] or any(v is not None and v.output_schema for _s, v in skills.values())
    if schema and not version.supports_tools:
        # 業務結果は submit_result で確定するので、Toolを使えないモデルでは確定できない（harness.md §10.5）
        errors.add("model_profile", _("業務結果のSchemaがあるタスクには、Toolを使えるモデルを選んでください"))
    elif content["tools"] and not version.supports_tools:
        errors.add("model_profile", _("このモデルはToolを使えません。Toolを使わないか、別のモデルを選んでください"))
    for _skill, skill_version in skills.values():
        if skill_version is not None and (reason := skill_version.unsupported_by(version)):
            errors.add("model_profile", reason)


# --- 実行するとき ---


def task_problem(task) -> str:
    """タスクのRunを続けられない理由（停止・削除）。なければ空文字"""
    if task is None or task.deleted:
        return _("タスクが削除されました")
    if task.suspended:
        return _("タスクが停止されました")
    if task.scope == TaskScope.DEPARTMENT and (task.department is None or task.department.deleted):
        return _("タスクの部門が無効にされました")  # 無効にした部門のタスクは起動しない（authorization.md §9）
    if task.scope == TaskScope.PERSONAL and (task.owner is None or not task.owner.is_active):
        return _("タスクの持ち主のアカウントが無効です")
    return ""


def skill_problem(task, skill: Skill | None, version: Any, *, connectors: list[str], sensitive: bool) -> str:
    """タスクのRunがこのSkillを今も使えない理由（使えれば空文字）。`connectors` はタスクに固定したConnectorのID。
    宣言したConnectorも、固定したものに入っていて、今も有効で範囲の中にあり、境界の中にあることを確かめる"""
    if skill is None or skill.deleted:
        return _("削除されました")
    if skill.suspended:
        return _("停止中です")
    if not skill_allowed(task, skill):
        return _("このタスクでは使えなくなりました")
    return declaration_problem(task, version, connectors=connectors, sensitive=sensitive) if version else ""


def declaration_problem(task, version: Any, *, connectors: list[str], sensitive: bool) -> str:
    declared = skill_connectors.load([version])
    if not sensitive and skill_connectors.is_sensitive(version, declared):
        return _("機密のSkillは、個人情報・機密情報を扱うタスクでだけ使えます")
    for declaration in version.connectors or []:
        connector = declared.get(declaration["connector"])
        if connector is None or declaration["connector"] not in connectors or not connector_allowed(task, connector):
            return _("このSkillが使うConnectorを使えません")
        if skill_connectors.out_of_bounds(declaration, connector):
            return _("このSkillが使う表・操作を、Connector「%(name)s」が今は公開していません") % {
                "name": connector.name
            }
    return ""


def connector_permitted(task, connector: Connector, *, connectors: list[str]) -> bool:
    """タスクのRunがこのConnectorを今使えるか：タスクに固定したもので、今も有効で範囲の中にある"""
    return connector.uid in connectors and task_problem(task) == "" and connector_allowed(task, connector)
