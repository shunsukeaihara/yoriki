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


def is_allowed_for_model(version: ConnectorVersion, model_version) -> bool:
    """機密のConnectorは、ローカルLLMのモデルのときだけ使える（design.md §4.9.1）"""
    return not version.is_sensitive or model_version.is_local


def build_catalog(user, model_version, *, explicit: Sequence[dict[str, Any]] = ()) -> dict[str, Any] | None:
    """エージェントに見せる一覧：依頼した人が使えるConnector（部門・全社）の名前と説明。なければNone。
    `@` で指定したもの（`explicit`）は、数の上限で外れないように先に並べる"""
    connectors = Connector.objects.usable_by(user).select_related("current_version").order_by("name", "pk")
    usable = [c for c in connectors if c.current_version and is_allowed_for_model(c.current_version, model_version)]
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


def resolve_explicit(connectors: list[Connector], user, model_version) -> list[dict[str, Any]]:
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
        if not is_allowed_for_model(connector.current_version, model_version):
            raise ConnectorUnavailable(
                _("%(name)s：機密のConnectorは、ローカルLLMのモデルでだけ使えます") % {"name": connector.name}
            )
        entries.append({"connector": connector.uid, "name": connector.name})
    return entries


def explicit_text(entries: list[dict[str, Any]]) -> str:
    """`@` で指定したConnectorの説明（公開している表の名前と説明）。最初の発言に添える。列の型はDBから読むので
    ここには入れず、`datasource_describe` で確かめさせる（Run開始時に接続先へつながない）"""
    found = {
        c.uid: c
        for c in Connector.objects.filter(uid__in=[e["connector"] for e in entries]).select_related("current_version")
    }
    blocks = []
    for entry in entries:
        connector = found.get(entry["connector"])
        version = connector.current_version if connector else None
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
