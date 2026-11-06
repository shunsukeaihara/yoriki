"""エージェントに見せるSkill・Connectorの一覧を作る（harness.md §10.1・§10.2）。

Run開始時と、発言を取り込むたびに、依頼した人の今の権限で作る。一覧を変えてもToolの定義は変わらない
（`skill`・`connector` の引数は文字列）。タスクのRunの一覧は、タスクに固定したものだけで変わらない。
"""

from typing import Any

from run.models import Run, RunKind

from . import tasks
from .connectors import build_catalog as build_connector_catalog
from .connectors import usable_uids as usable_connector_uids
from .skills import build_catalog, catalog_connectors, declared_connectors, loaded_entries, restrict_catalog


def build_catalogs(
    user, model_version, *, sensitive: bool, list_skills: bool, explicit: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """（Skillの一覧、Connectorの一覧）。Toolを使えないモデルにはどちらも出さない。`explicit` は `@` で指定したものと
    Skillが宣言したもので、数の上限で外れないように先に並べる。一覧のSkillが宣言したものも同じ。
    Skillの一覧には、宣言したConnectorをこのRunで使えるものだけを出す（design.md §4.9.1）"""
    if not model_version.supports_tools:
        return None, None
    catalog = (
        build_catalog(
            user, model_version, sensitive=sensitive, connectors=usable_connector_uids(user, sensitive=sensitive)
        )
        if list_skills
        else None
    )
    connector_catalog = build_connector_catalog(
        user, sensitive=sensitive, explicit=[*explicit, *catalog_connectors(catalog)]
    )
    if catalog is not None:
        catalog = restrict_catalog(catalog, {e["connector"] for e in (connector_catalog or {}).get("connectors", [])})
    return catalog, connector_catalog


def refreshes_catalogs(run: Run) -> bool:
    """発言を取り込むときに一覧を作り直すRunか。タスクのRunはタスクに固定したもの、タスクの編集のRunは使わない"""
    return run.kind in (RunKind.WORK, RunKind.SKILL_EDIT) and not tasks.is_task_run(run)


def lists_skills(run: Run) -> bool:
    """エージェントが探して使うSkillの一覧を出すRunか。`/` で指定して始めたRunと編集のRunには出さない"""
    fixed = (run.manifest or {}).get("fixed") or {}
    return run.kind == RunKind.WORK and not tasks.is_task_run(run) and not fixed.get("skills")


def rebuild_catalogs(
    run: Run, *, connectors: list[dict[str, Any]] = (), skills: list[dict[str, Any]] = ()
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """動いているRunの一覧を、依頼した人の今の権限で作る。`connectors`・`skills` は、今取り込む発言で指定したもの。
    `@` で指定したものと、Runが使うSkillが宣言したものを先に並べる（Connectorの名前をToolで使い続けられるように）"""
    manifest = run.manifest or {}
    chosen = [
        *((manifest.get("fixed") or {}).get("connectors") or []),
        *(e for e in manifest.get("appended", []) if e.get("kind") == "connector"),
        *connectors,
    ]
    return build_catalogs(
        run.owner,
        run.model_version,
        sensitive=run.handles_sensitive_data,
        list_skills=lists_skills(run),
        explicit=[*chosen, *declared_connectors([*loaded_entries(run), *skills])],
    )
