"""エージェントに見せるSkill・Connectorの一覧を作る（harness.md §10.1・§10.2）。

Run開始時と、発言を取り込むたびに、今の権限（会話は依頼した人、タスクはタスクの範囲）で作る。一覧を変えてもToolの定義は
変わらない（`skill`・`connector` の引数は文字列）。使うよう指示されたものは先に並べるだけで、範囲を変えない。
"""

from collections.abc import Sequence
from typing import Any

from connector.models import Connector
from run.models import Run, RunKind
from skill.models import Skill
from task import checks

from . import tasks
from .connectors import build_catalog as build_connector_catalog
from .connectors import catalog_of as connector_catalog_of
from .connectors import is_allowed_for
from .skills import (
    build_catalog,
    catalog_connectors,
    catalog_of,
    declared_connectors,
    loaded_entries,
)


def build_catalogs(
    user, model_version, *, sensitive: bool, list_skills: bool, explicit: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """（Skillの一覧、Connectorの一覧）。Toolを使えないモデルにはどちらも出さない。`explicit` は `@` で指定したものと
    Skillが宣言したもので、数の上限で外れないように先に並べる。一覧のSkillが宣言したものも同じ"""
    if not model_version.supports_tools:
        return None, None
    catalog = build_catalog(user, model_version, sensitive=sensitive) if list_skills else None
    connector_catalog = build_connector_catalog(
        user, sensitive=sensitive, explicit=[*explicit, *catalog_connectors(catalog)]
    )
    return catalog, connector_catalog


def _task_scope(task, launcher):
    """タスクのRunがアクセスできる（Skill、Connector）。`launcher` はチャットから起動した人で、共通部分を取る"""
    skills, connectors = checks.allowed_skills(task), checks.allowed_connectors(task)
    if launcher is not None:
        skills = skills.filter(pk__in=Skill.objects.usable_by(launcher).values("pk"))
        connectors = connectors.filter(pk__in=Connector.objects.usable_by(launcher).values("pk"))
    return skills, connectors


def build_task_catalogs(
    task, model_version, *, sensitive: bool, explicit: list[dict[str, Any]], launcher=None
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """タスクのRunの（Skillの一覧、Connectorの一覧）：タスクの範囲でアクセスできるもの（authorization.md §5.4）。
    機密のものは機密のRunだけ。`explicit` は定義で使うよう指示したものとSkillが宣言したもの。`launcher` はチャットから
    起動した人で、その人の今の権限との共通部分を取る（起動できる人なら、タスクの範囲と同じになる。
    authorization.md §6）"""
    if not model_version.supports_tools:
        return None, None
    skills, allowed_connectors = _task_scope(task, launcher)
    catalog = catalog_of(skills, model_version, sensitive=sensitive)
    connectors = [
        c
        for c in allowed_connectors.select_related("current_version").order_by("name", "pk")
        if c.current_version and is_allowed_for(c.current_version, sensitive=sensitive)
    ]
    connector_catalog = connector_catalog_of(connectors, [*explicit, *catalog_connectors(catalog)])
    return catalog, connector_catalog


def refreshes_catalogs(run: Run) -> bool:
    """発言を取り込むときに一覧を作り直すRunか。評価のRunには発言が届かない"""
    return run.kind != RunKind.EVALUATION


def lists_skills(run: Run) -> bool:
    """エージェントが探して使うSkillの一覧を出すRunか。評価のRun（1つのSkillを確かめる）のほかは、すべてのRunに出す"""
    return run.kind != RunKind.EVALUATION


def rebuild_catalogs(
    run: Run, *, connectors: Sequence[dict[str, Any]] = (), skills: Sequence[dict[str, Any]] = ()
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """動いているRunの一覧を、今の権限（会話は依頼した人、タスクはタスクの範囲）で作る。`connectors`・`skills` は、今
    取り込む発言で指定したもの。今の指定、前に `@` で指定したもの、Runが使うSkillが宣言したものの順に先に並べる
    （今の指定を一覧の上限で外さず、前の名前もToolで使い続けられるように）"""
    manifest = run.manifest or {}
    chosen = [
        *connectors,
        *((manifest.get("fixed") or {}).get("connectors") or []),
        *(e for e in manifest.get("appended", []) if e.get("kind") == "connector"),
    ]
    explicit = [*chosen, *declared_connectors([*loaded_entries(run), *skills])]
    if tasks.is_task_run(run):
        task = tasks.load_task(run)
        if task is None:  # 区切りで止める。一覧はそのまま
            return run.current_catalog("skill_catalog"), run.current_catalog("connector_catalog")
        return build_task_catalogs(
            task,
            run.model_version,
            sensitive=run.handles_sensitive_data,
            explicit=explicit,
            launcher=tasks.launcher(run),
        )
    return build_catalogs(
        run.owner,
        run.model_version,
        sensitive=run.handles_sensitive_data,
        list_skills=lists_skills(run),
        explicit=explicit,
    )


def _run_scope(run: Run):
    """Runがアクセスできる（Skill、Connector）のQuerySet。会話は依頼した人、タスクはタスクの範囲。使えるかの細かな判定
    （機密・停止・能力等）は、使うときに確かめる"""
    if tasks.is_task_run(run):
        task = tasks.load_task(run)
        if task is None:
            return Skill.objects.none(), Connector.objects.none()
        return _task_scope(task, tasks.launcher(run))
    return Skill.objects.usable_by(run.owner), Connector.objects.usable_by(run.owner)


def _only_uid(queryset, name: str) -> str | None:
    found = list(queryset.filter(name=name).values_list("uid", flat=True)[:2])
    return found[0] if len(found) == 1 else None


def unlisted_skill(run: Run, name: str) -> str | None:
    """一覧の量の上限で省いたSkillのID。一覧は見せ方で、アクセス範囲を狭めない（harness.md §10.1）"""
    scope = _run_scope(run)[0]
    # 一覧と同じ名前の割当てで引く（同じ名前のSkillは「名前 (個人)」等で区別する）
    named = catalog_of(scope, run.model_version, sensitive=run.handles_sensitive_data, trim=False)["skills"]
    found = next((e["skill"] for e in named if e["name"] == name), None)
    return found or _only_uid(scope, name)


def unlisted_connector(run: Run, name: str) -> str | None:
    """一覧の数の上限で省いたConnectorのID。一覧は見せ方で、アクセス範囲を狭めない（harness.md §10.2）"""
    return _only_uid(_run_scope(run)[1], name)
