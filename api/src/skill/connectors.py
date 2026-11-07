"""Skillが宣言するConnectorと、機密のSkill（design.md §4冒頭・§4.9.1、authorization.md §5.1）。

Skillの中身（Draft・版・評価に写したDraft）の `connectors` は、使うConnectorと操作・表の宣言で、
`[{"connector": "<ConnectorのID>", "operations": ["datasource_query"], "tables": ["public.vendors"]}]` の形にする。
表を省けば、そのConnectorの公開する表のどれでも使う。宣言は使用の指示で、Skillを選べるかにも、使える範囲にも関わらない。
Connectorを使えるか、表と操作の境界は、操作のたびにセッションの権限とConnectorのその時点の版で判定する。

機密のSkillは、機密のフラグを付けたもの。フラグはSkill自身のもので、宣言したConnectorからは決めない。
"""

from collections.abc import Iterable
from typing import Any

from django.utils.translation import gettext as _

from connector.models import Connector, ConnectorAction, ConnectorScope

from .models import SkillScope

# 1つのSkillが宣言できるConnectorの数と、1つのConnectorで宣言できる表の数
MAX_DECLARED_CONNECTORS = 5
MAX_DECLARED_TABLES = 100

# Skillの範囲ごとに宣言できるConnectorの範囲（authorization.md §5.4のタスクと同じ）。個人のSkillは本人が使えるもの
SCOPE_CONNECTORS: dict[int, tuple[int, ...]] = {
    SkillScope.DEPARTMENT: (ConnectorScope.DEPARTMENT, ConnectorScope.COMPANY),  # 部門のものは同じ部門だけ
    SkillScope.COMPANY: (ConnectorScope.COMPANY,),
    SkillScope.SYSTEM: (ConnectorScope.SYSTEM, ConnectorScope.DEPARTMENT, ConnectorScope.COMPANY),
}


def table_key(table: dict[str, Any]) -> str:
    """表の名前（スキーマがあれば `スキーマ.表`）。Connectorの版の `tables` と宣言で同じ書き方にする"""
    return f"{table['schema']}.{table['name']}" if table.get("schema") else table["name"]


def declared_uids(content: Any) -> list[str]:
    return [d["connector"] for d in content.connectors or []]


def load(contents: Iterable[Any]) -> dict[str, Connector]:
    """中身が宣言したConnector（IDごと。削除されたものも含む）"""
    uids = {uid for content in contents for uid in declared_uids(content)}
    if not uids:
        return {}
    connectors = Connector.objects.filter(uid__in=uids).select_related("connection", "current_version", "department")
    return {c.uid: c for c in connectors}


def is_sensitive(content: Any) -> bool:
    """機密のSkillか（Skill自身のフラグ）"""
    return bool(content.is_sensitive)


def out_of_bounds(declaration: dict[str, Any], connector: Connector) -> list[str]:
    """宣言した操作・表のうち、Connectorの今の版の境界の外にあるもの"""
    version = connector.current_version
    exposed = {table_key(t) for t in version.tables} if version is not None else set()
    operations = [o for o in declaration["operations"] if o not in connector.operations]
    return operations + [t for t in declaration.get("tables", []) if t not in exposed]


def declaration_problems(content: Any, skill, actor) -> list[str]:
    """宣言の気になる点（Connectorが無効、権限がない、範囲に合わない、公開していない等）。画面で知らせるだけで、公開も実行も
    止めない。宣言は使用の指示で、使えるかは操作のときにセッションの権限で判定するため（design.md §4.9.1）"""
    errors: list[str] = []
    connectors = load([content])
    for declaration in content.connectors or []:
        connector = connectors.get(declaration["connector"])
        if connector is None or connector.deleted:
            errors.append(_("宣言したConnectorが削除されました"))
            continue
        name = connector.name
        if not connector.in_service():
            errors.append(_("Connector「%(name)s」は無効です") % {"name": name})
            continue
        if not usable_by_declarer(connector, actor):
            errors.append(_("Connector「%(name)s」を使う権限がありません") % {"name": name})
            continue
        if not scope_allows(skill, connector):
            errors.append(
                _("Connector「%(name)s」は、このSkillの範囲（%(scope)s）では使えません")
                % {"name": name, "scope": skill.get_scope_display()}
            )
            continue
        operations = set(connector.operations)
        for operation in declaration["operations"]:
            if operation not in operations:
                errors.append(
                    _("Connector「%(name)s」には操作「%(operation)s」がありません")
                    % {"name": name, "operation": operation}
                )
            elif operation not in content.tools:
                errors.append(
                    _("Connector「%(name)s」の操作「%(operation)s」を使うには、使うToolに加えてください")
                    % {"name": name, "operation": operation}
                )
        assert connector.current_version is not None  # in_service() が確かめた
        exposed = {table_key(t) for t in connector.current_version.tables}
        missing = [t for t in declaration.get("tables", []) if t not in exposed]
        if missing:
            errors.append(
                _("Connector「%(name)s」は、表「%(tables)s」を公開していません")
                % {"name": name, "tables": "、".join(missing)}
            )
    return errors


def usable_by_declarer(connector: Connector, actor) -> bool:
    """宣言する人・公開する人が使えるConnectorか。システムのConnectorは会話では使わないので、見る権限を持つ人
    （システムのSkillの管理者）が宣言する（authorization.md §5.4）"""
    if connector.scope == ConnectorScope.SYSTEM:
        return connector.in_service() and actor.has_perm("connector.view_connector")
    return connector.can(actor, ConnectorAction.USE)


def scope_allows(skill, connector: Connector) -> bool:
    """Skillの範囲で宣言できるConnectorか（authorization.md §5.4のタスクの表と同じ）"""
    if skill.scope == SkillScope.PERSONAL:  # 本人が使えるもの（使えるかは公開する人＝本人で別に確かめる）
        return connector.scope != ConnectorScope.SYSTEM
    if connector.scope not in SCOPE_CONNECTORS.get(skill.scope, ()):
        return False
    if skill.scope == SkillScope.DEPARTMENT and connector.scope == ConnectorScope.DEPARTMENT:
        return connector.department_id == skill.department_id
    return True


def skill_is_sensitive(skill) -> bool:
    """Skillの公開中の版かDraftが機密か。機密のSkillは、機密の会話の編集のRunでだけ扱う（harness.md §10.9）"""
    contents: list[Any] = list(skill.drafts.all())
    if skill.current_version is not None:
        contents.append(skill.current_version)
    return any(is_sensitive(c) for c in contents)
