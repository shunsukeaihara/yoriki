"""タスクのアクセス範囲（authorization.md §5.4）と、タスクのRunの実行許可（authorization.md §6）。

- 保存するとき（`content_errors()`）：定義で使うよう指示するSkill・Connectorがタスクの範囲の中にあるか、Skillが宣言した
  Connectorが範囲の中にあるか、機密の区分とモデル設定が合うか、プロンプトのテンプレートが入力の項目だけを使うか。
- 実行するとき（`skill_problem()`・`connector_permitted()`・`task_problem()`）：受付時、実行の区切りごと、Connectorの
  操作のたびに、今も有効でタスクの範囲の中にあるかを確かめ直す。定義で指示したかは見ない（指示はアクセス範囲ではない）。
  個人のタスクは本人の今の権限で判定する。
- チャットから起動したRun（`launcher_scope_problem()`）：起動した人が今も起動できる立場かだけを確かめる。起動できる人は
  タスクより狭い権限を持たない（§5.3）ので、Skill・Connectorは起動した人とは照らし合わせない。
"""

import re
from typing import Any, Protocol

from django.db.models import Q, QuerySet
from django.utils.translation import gettext as _

from connector.models import Connector, ConnectorAction, ConnectorScope
from delivery.models import Destination, NotifyContent, NotifyEvent
from skill import connectors as skill_connectors
from skill.models import Skill, SkillAction, SkillScope, SkillVersion
from user.models import Department, manageable_department_ids

from .models import TaskAction, TaskScope

# 1つのタスクに登録できるSkill・Connector・入力の項目の数
MAX_TASK_SKILLS = 5
MAX_TASK_CONNECTORS = 10
MAX_INPUTS = 30
# 入力の項目の名前。テンプレートの `{{ inputs.名前 }}` に使う
INPUT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,49}$")
PLACEHOLDER_RE = re.compile(r"\{\{\s*inputs\.([A-Za-z0-9_]+)\s*\}\}")

# タスクの範囲ごとに使えるSkill・Connectorの範囲（authorization.md §5.4）。個人のタスクは本人が使えるもの。
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
    """このタスクの範囲の中にあるSkillで、今も使える（公開中で停止していない）か"""
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
    """このタスクの範囲の中にあるConnectorで、今も有効か"""
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


def allowed_skills(task: TaskLike) -> QuerySet[Skill]:
    """範囲の中にあるSkill（`skill_allowed()` と同じ規則のSQL）。編集画面の候補とRunの一覧に使う"""
    if task.scope == TaskScope.PERSONAL:
        return Skill.objects.usable_by(task.owner) if task.owner is not None else Skill.objects.none()
    scopes = SCOPE_SKILLS.get(task.scope, ())
    condition = Q(scope__in=[s for s in scopes if s != SkillScope.DEPARTMENT])
    if SkillScope.DEPARTMENT in scopes:
        condition |= Q(scope=SkillScope.DEPARTMENT, department_id=task.department_id, department__deleted=False)
    return Skill.objects.filter(condition, deleted=False, suspended=False, current_version__isnull=False)


def allowed_connectors(task: TaskLike) -> QuerySet[Connector]:
    """範囲の中にあるConnector（`connector_allowed()` と同じ規則のSQL）。編集画面の候補とRunの一覧に使う"""
    if task.scope == TaskScope.PERSONAL:
        return Connector.objects.usable_by(task.owner) if task.owner is not None else Connector.objects.none()
    scopes = SCOPE_CONNECTORS.get(task.scope, ())
    condition = Q(scope__in=[s for s in scopes if s != ConnectorScope.DEPARTMENT])
    if ConnectorScope.DEPARTMENT in scopes:
        department = Q(scope=ConnectorScope.DEPARTMENT, department__deleted=False)
        if task.scope == TaskScope.DEPARTMENT:
            department &= Q(department_id=task.department_id)
        condition |= department
    return Connector.objects.in_service().filter(condition)


def skill_entries(revision: Any) -> list[tuple[str, dict[str, Any]]]:
    """版に登録したSkillの（項目の名前、`{"skill", "version"}`）。`revision` は版か版の中身の辞書"""
    skills = revision["skills"] if isinstance(revision, dict) else revision.skills
    return [("skills", entry) for entry in skills]


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
    skills = load_skills([entry for _field, entry in skill_entries(content)])
    _check_skills(task, content, skills, errors)
    _check_model(content, skills, errors)
    _check_notification(task, content, errors)
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


def skill_tools(skills: dict[str, tuple[Skill, SkillVersion | None]]) -> set[str]:
    """定義で指示したSkillが使うToolを合わせたもの（モデルがToolを使えるかの確かめに使う）"""
    return {t for _s, v in skills.values() if v is not None for t in v.tools}


def _check_skills(
    task: TaskLike, content: dict[str, Any], skills: dict[str, tuple[Skill, SkillVersion | None]], errors: Errors
) -> None:
    schemas = 0
    for field, entry in skill_entries(content):
        skill, version = skills.get(entry["skill"], (None, None))
        if skill is None or skill.deleted:
            errors.add(field, _("登録したSkillが削除されました"))
        elif version is None:
            errors.add(
                field,
                _("Skill「%(name)s」に版%(number)sはありません") % {"name": skill.name, "number": entry["version"]},
            )
        elif not skill_allowed(task, skill):
            errors.add(field, _("Skill「%(name)s」は、このタスクでは使えません") % {"name": skill.name})
        else:
            schemas += 1 if version.output_schema else 0
            _check_declarations(task, skill, version, content, errors, field=field)
    if schemas > 1 and not content["output_schema"]:
        errors.add(
            "output_schema", _("業務結果の形を決めているSkillが複数あります。タスクの業務結果のSchemaを決めてください")
        )


def _check_declarations(
    task: TaskLike, skill: Skill, version: SkillVersion, content: dict[str, Any], errors: Errors, *, field: str
) -> None:
    """Skillが宣言したConnectorがタスクの範囲の中にあり、宣言した操作・表が境界の中にあるか（③bの宣言）"""
    declared = skill_connectors.load([version])
    if not content["handles_sensitive_data"] and skill_connectors.is_sensitive(version, declared):
        errors.add(
            field,
            _("Skill「%(name)s」は機密なので、個人情報・機密情報を扱うタスクにしてください") % {"name": skill.name},
        )
    for declaration in version.connectors or []:
        connector = declared.get(declaration["connector"])
        names = {"skill": skill.name, "name": connector.name if connector else declaration["connector"]}
        if connector is None or not connector_allowed(task, connector):
            errors.add(field, _("Skill「%(skill)s」が使うConnector「%(name)s」は、このタスクでは使えません") % names)
        elif skill_connectors.out_of_bounds(declaration, connector):
            errors.add(
                field, _("Skill「%(skill)s」が使う表・操作を、Connector「%(name)s」が今は公開していません") % names
            )


def _check_notification(task: TaskLike, content: dict[str, Any], errors: Errors) -> None:
    """結果の通知の出口が、タスクの範囲で使えて、機密の区分に合うか（authorization.md §5.4・§7）"""
    destination: Destination | None = content["destination"]
    if content["notification_instructions"] and (
        destination is None
        or NotifyEvent.COMPLETED not in content["notify_events"]
        or content["notify_content"] == NotifyContent.STATUS
    ):
        # 通知の書き方で決めた文章とファイルは、完了の通知の業務結果と一緒に送る（design.md §4.3）
        errors.add(
            "notification_instructions",
            _(
                "通知の書き方を使うには、通知先と完了（run.completed）の通知を選び、通知の内容に業務結果を"
                "含めてください"
            ),
        )
    if destination is None:
        if content["notify_events"]:
            errors.add("destination", _("通知するイベントを選んだときは、通知先を選んでください"))
        return
    if not content["notify_events"]:
        errors.add("notify_events", _("通知するイベントを選んでください"))
    if problem := destination.problem_for(task, sensitive=content["handles_sensitive_data"]):
        errors.add(
            "destination",
            _("通知先「%(name)s」は使えません（%(reason)s）") % {"name": destination.name, "reason": problem},
        )


def notifies_failure(revision: Any) -> bool:
    """失敗を知らせる先がある（Webhookで起動するタスクに必須。design.md §4.2）。`revision` は版か版の中身の辞書"""
    if isinstance(revision, dict):
        return revision["destination"] is not None and NotifyEvent.FAILED in revision["notify_events"]
    return revision.destination_id is not None and NotifyEvent.FAILED in revision.notify_events


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
    elif content["notification_instructions"] and not version.supports_tools:
        errors.add("model_profile", _("通知の書き方を決めたタスクには、Toolを使えるモデルを選んでください"))
    elif skill_tools(skills) and not version.supports_tools:
        errors.add("model_profile", _("このモデルはToolを使えません。Toolを使わないか、別のモデルを選んでください"))
    for _skill, skill_version in skills.values():
        if skill_version is not None and (reason := skill_version.unsupported_by(version)):
            errors.add("model_profile", reason)


# --- 実行するとき ---


def task_problem(task, *, allow_suspended: bool = False) -> str:
    """タスクのRunを続けられない理由（停止・削除）。なければ空文字。修正チャットのRunは停止中かを見ない
    （`allow_suspended`。出口へ送らないので、本番の受付を止めたまま直せる。design.md §11.11）"""
    if task is None or task.deleted:
        return _("タスクが削除されました")
    if task.suspended and not allow_suspended:
        return _("タスクが停止されました")
    if task.scope == TaskScope.DEPARTMENT and (task.department is None or task.department.deleted):
        return _("タスクの部門が無効にされました")  # 無効にした部門のタスクは起動しない（authorization.md §9）
    if task.scope == TaskScope.PERSONAL and (task.owner is None or not task.owner.is_active):
        return _("タスクの持ち主のアカウントが無効です")
    return ""


def skill_problem(task, skill: Skill | None, version: Any, *, sensitive: bool) -> str:
    """タスクのRunがこのSkillを今も使えない理由（使えれば空文字）。宣言したConnectorも、今も有効で範囲の中にあり、
    境界の中にあることを確かめる"""
    if skill is None or skill.deleted:
        return _("削除されました")
    if skill.suspended:
        return _("停止中です")
    if not skill_allowed(task, skill):
        return _("このタスクでは使えなくなりました")
    return declaration_problem(task, version, sensitive=sensitive) if version else ""


def declaration_problem(task, version: Any, *, sensitive: bool) -> str:
    declared = skill_connectors.load([version])
    if not sensitive and skill_connectors.is_sensitive(version, declared):
        return _("機密のSkillは、個人情報・機密情報を扱うタスクでだけ使えます")
    for declaration in version.connectors or []:
        connector = declared.get(declaration["connector"])
        if connector is None or not connector_allowed(task, connector):
            return _("このSkillが使うConnectorを使えません")
        if skill_connectors.out_of_bounds(declaration, connector):
            return _("このSkillが使う表・操作を、Connector「%(name)s」が今は公開していません") % {
                "name": connector.name
            }
    return ""


def connector_permitted(task, connector: Connector, *, allow_suspended: bool = False) -> bool:
    """タスクのRunがこのConnectorを今使えるか：今も有効で、タスクの範囲の中にある。修正チャットのRunは停止中かを
    見ない（`allow_suspended`。task_problem() と同じ）"""
    return task_problem(task, allow_suspended=allow_suspended) == "" and connector_allowed(task, connector)


# --- チャットから起動したRun（authorization.md §6） ---


def launcher_inactive(user) -> str:
    """起動した人が今は無効なら理由。Workerでは、確かめ直すたびにDBから読んだユーザーを渡す"""
    return "" if user is not None and user.is_active else _("起動した人のアカウントが無効です")


def launcher_scope_problem(user, run: Any) -> str:
    """起動した人が、Runを実行したときのタスクの範囲と部門のタスクを、今もチャットから起動できる立場か（印・停止は
    見ない。印を外しても起動済みのRunは止めない）。できなければ理由"""
    if reason := launcher_inactive(user):
        return reason
    if run.task_scope == TaskScope.PERSONAL:
        allowed = run.owner_id == user.pk
    elif run.task_scope == TaskScope.DEPARTMENT:
        allowed = Department.objects.joined_by(user).filter(
            pk=run.task_department_id
        ).exists() or run.task_department_id in manageable_department_ids(user)
    else:
        allowed = run.task_scope == TaskScope.COMPANY
    return "" if allowed else _("起動した人がこのタスクを起動できる部門から外れました")


def fixer_problem(user, task) -> str:
    """修正チャットでタスクを動かす・直す人が、今もそのタスクを管理できるか（authorization.md §6）。Workerでは、
    確かめ直すたびにDBから読んだユーザーとタスクを渡す。できなければ理由"""
    if user is None or not user.is_active:
        return _("修正チャットの持ち主のアカウントが無効です")
    if task is None or not task.can(user, TaskAction.MANAGE):
        return _("修正チャットの持ち主がこのタスクを管理できなくなりました")
    return ""
