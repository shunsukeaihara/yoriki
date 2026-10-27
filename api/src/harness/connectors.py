"""会話でConnectorを使う（harness.md §10.2、design.md §4.9.1）。

エージェントに見せるConnectorの一覧と、入力欄の `@` で指定したConnectorは、Run開始時に決めてManifestに固定する
（Toolの一覧と `connector` の引数のenumをRunの中で変えないため）。一覧は依頼した人が使えるもので、機密のフラグの
付いたものはローカルLLMのモデルのときだけ載せる。使えるか（範囲・所属・停止・機密）は、操作のたびに確かめ直す
（authorization.md §6）。
"""

import re
from collections.abc import Sequence
from typing import Any

from django.utils.translation import gettext as _

from connector.models import Connector, ConnectorAction, ConnectorVersion
from run.models import Run

# ConnectorのTool（harness/tools/datasource.py）の名前
DATASOURCE_TOOL_NAMES = ("datasource_describe", "datasource_query")
# 一覧に載せる数と、説明の長さ（文字）
MAX_CATALOG_CONNECTORS = 50
DESCRIPTION_LIMIT = 300
# 入力欄の `@` で1つのメッセージに指定できるConnectorの数
MAX_EXPLICIT_CONNECTORS = 5
# `@` で指定したConnectorの説明に載せる表の数
MAX_EXPLICIT_TABLES = 100


class ConnectorUnavailable(Exception):
    pass


def _one_line(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def is_allowed_for(version: ConnectorVersion, *, sensitive: bool) -> bool:
    """機密のConnectorは、機密（個人情報・機密情報を扱う）の会話・タスクでだけ使える（design.md §4.9.1）。
    機密の会話はローカルLLMのモデルだけを使い、機密をやめられないので、クラウドのモデルへは渡らない"""
    return not version.is_sensitive or sensitive


def _usable(user, *, sensitive: bool) -> list[Connector]:
    connectors = Connector.objects.usable_by(user).select_related("current_version").order_by("name", "pk")
    return [c for c in connectors if c.current_version and is_allowed_for(c.current_version, sensitive=sensitive)]


def usable_uids(user, *, sensitive: bool) -> set[str]:
    """このRunで使えるConnectorのID（一覧の数の上限で外す前）"""
    return {c.uid for c in _usable(user, sensitive=sensitive)}


def build_catalog(user, *, sensitive: bool, explicit: Sequence[dict[str, Any]] = ()) -> dict[str, Any] | None:
    """エージェントに見せる一覧：依頼した人が使えるConnector（部門・全社）の名前と説明。機密のConnectorは機密の
    Runのときだけ（`sensitive`）。なければNone。`@` で指定したものと `/` で指定したSkillが宣言したもの（`explicit`）は、
    数の上限で外れないように先に並べる"""
    usable = _usable(user, sensitive=sensitive)
    chosen = [e["connector"] for e in explicit]
    usable.sort(key=lambda c: chosen.index(c.uid) if c.uid in chosen else len(chosen))
    if not usable:
        return None
    entries = [
        {
            "connector": c.uid,
            "name": c.name,
            "description": _one_line(c.current_version.description, DESCRIPTION_LIMIT) if c.current_version else "",
        }
        for c in usable[:MAX_CATALOG_CONNECTORS]
    ]
    return {"connectors": entries, "omitted": len(usable) - len(entries)}


def fixed_catalog(connectors: Sequence[Connector]) -> dict[str, Any] | None:
    """タスクのRunの一覧：タスクに固定したConnector（使えるかは呼び出し側で確かめたもの）。なければNone。
    タスクのRunでは、エージェントはこの一覧の外のConnectorを探して使わない（authorization.md §5.4）"""
    if not connectors:
        return None
    entries = [
        {
            "connector": c.uid,
            "name": c.name,
            "description": _one_line(c.current_version.description, DESCRIPTION_LIMIT) if c.current_version else "",
        }
        for c in connectors
    ]
    return {"connectors": entries, "omitted": 0}


def catalog(run: Run) -> dict[str, Any]:
    return ((run.manifest or {}).get("fixed") or {}).get("connector_catalog") or {}


def catalog_names(run: Run) -> list[str]:
    return [e["name"] for e in catalog(run).get("connectors", [])]


def catalog_text(value: dict[str, Any]) -> str:
    """`<runtime_context>` の中の `<connectors>`"""
    lines = ["  <connectors>"]
    for entry in value["connectors"]:
        lines.append(f'    <connector name="{_escape(entry["name"])}">{_escape(entry["description"])}</connector>')
    if value.get("omitted"):
        lines.append(f"    ({value['omitted']} more connectors are not listed)")
    lines.append("  </connectors>")
    return "\n".join(lines)


def resolve_explicit(connectors: list[Connector], user, model_version, *, sensitive: bool) -> list[dict[str, Any]]:
    """入力欄の `@` で指定したConnectorを確かめて、Manifestに固定する内容にする。使えなければ ConnectorUnavailable"""
    if len(connectors) > MAX_EXPLICIT_CONNECTORS:
        raise ConnectorUnavailable(_("指定できるConnectorは%(limit)s個までです") % {"limit": MAX_EXPLICIT_CONNECTORS})
    if connectors and not model_version.supports_tools:
        raise ConnectorUnavailable(_("このモデルはToolを使えないので、Connectorを使えません"))
    entries = []
    for connector in connectors:
        if not connector.can(user, ConnectorAction.USE):
            raise ConnectorUnavailable(_("%(name)s：使う権限がないか、無効です") % {"name": connector.name})
        assert connector.current_version is not None  # can() が確かめた
        if not is_allowed_for(connector.current_version, sensitive=sensitive):
            raise ConnectorUnavailable(
                _("%(name)s：機密のConnectorは、個人情報・機密情報を扱う会話でだけ使えます") % {"name": connector.name}
            )
        version = connector.current_version
        # 説明は確かめた版から作る（作る間に別の版になっても、確かめていない版の中身を入れない）
        entries.append(
            {
                "connector": connector.uid,
                "name": connector.name,
                "version": version.pk,
            }
        )
    return entries


def explicit_text(entries: list[dict[str, Any]]) -> str:
    """`@` で指定したConnectorの説明（公開している表の名前と説明）。最初の発言に添える。列の型はDBから読むので
    ここには入れず、`datasource_describe` で確かめさせる（Run開始時に接続先へつながない）"""
    versions = ConnectorVersion.objects.in_bulk([e["version"] for e in entries])
    blocks = []
    for entry in entries:
        version = versions.get(entry["version"])
        if version is None:
            continue
        lines = [f'<connector name="{_escape(entry["name"])}">']
        if version.description.strip():
            lines.append(f"<description>{_escape(version.description.strip())}</description>")
        lines.append("<tables>")
        for table in version.tables[:MAX_EXPLICIT_TABLES]:
            name = f"{table['schema']}.{table['name']}" if table.get("schema") else table["name"]
            description = f" — {_one_line(table['description'], 200)}" if table.get("description") else ""
            lines.append(f"  {_escape(name)}{_escape(description)}")
        if len(version.tables) > MAX_EXPLICIT_TABLES:
            lines.append(f"  ({len(version.tables) - MAX_EXPLICIT_TABLES} more tables: see datasource_describe)")
        lines.append("</tables>")
        lines.append("</connector>")
        blocks.append("\n".join(lines))
    head = (
        "The user chose these connectors for this request. Check the columns with datasource_describe, then read "
        "the data with datasource_query:"
    )
    return head + "\n" + "\n\n".join(blocks)
