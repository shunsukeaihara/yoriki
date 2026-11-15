"""Sandboxの追加のPython環境（design.md §7.6・§7.7、harness.md §7.4の `install_python_packages`）。

Sandboxを起動するたびに会話（タスクのRunはそのRun、サブエージェントはその子）の環境の版を入れ直し、Toolで頼まれた
packageを取得して足す。Dockerと保存先の操作は別のスレッドでleaseを延ばしながら行い、DBはRunnerのスレッドで触る。
"""

import logging
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

from nanoid import generate

from run.models import ExecutionAttempt
from runtime import packages
from runtime.launcher import ATTEMPT_LABEL, DATABASE_LABEL, RUN_LABEL, Sandbox, SandboxSpec
from runtime.models import EnvironmentRevision, PythonWheel
from runtime.packages import PackageError, Requested

if TYPE_CHECKING:
    from .sandbox import SandboxSession

logger = logging.getLogger(__name__)

PACKAGES_MISSING = (
    "[These Python packages that were added earlier in this conversation could not be installed in this sandbox, so "
    "they are not available now: {names}. {reason}If you need them, call install_python_packages again, or work "
    "without them.]"
)
NO_LONGER_IN_CATALOG = "They are no longer in the package catalog. "
NOT_IN_CATALOG = (
    "Ask a platform administrator to add the package to the package catalog (or to allow the version), or do the work "
    "with the libraries that are already installed. Do not try to install it in another way."
)


@dataclass(frozen=True)
class Added:
    revision: EnvironmentRevision
    added: list[packages.LocalWheel]
    # 頼んだもののうち、イメージかそれまでの追加ですでに入っていたもの（名前）
    already: list[str]


def _labels(session: "SandboxSession") -> dict[str, str]:
    from .sandbox import database_label  # noqa: PLC0415  sandbox がこのモジュールを使うため

    return {RUN_LABEL: session.run.uid, ATTEMPT_LABEL: str(session.attempt.pk), DATABASE_LABEL: database_label()}


def _record_attempt(session: "SandboxSession", revision: EnvironmentRevision | None) -> None:
    session.environment = revision
    with session.run.fenced(session.attempt):
        ExecutionAttempt.objects.filter(pk=session.attempt.pk).update(environment=revision)


def _install(session: "SandboxSession", sandbox: Sandbox, spec: SandboxSpec, wheels: list[PythonWheel]) -> None:
    sources = packages.sources(wheels)
    session._keep_lease(
        lambda: packages.install(sandbox, sources, spec.tmp_mb, storage_factory=session.storage_factory)
    )


def _fetch(
    session: "SandboxSession", spec: SandboxSpec, lines: list[str], base: list[tuple[str, str]]
) -> tuple[packages.FetchResult, list[PythonWheel]]:
    """取得して確かめ、保存して記録する"""
    workdir = session.dir / "packages" / generate(size=8)
    name = f"yoriki-fetch-{session._name()}-{generate('0123456789abcdefghijklmnopqrstuvwxyz', 6)}"
    try:
        result = session._keep_lease(
            lambda: packages.fetch(
                session.launcher(),
                spec,
                _labels(session),
                requirement_lines=lines,
                base=base,
                workdir=workdir,
                name=name,
            )
        )
        known = set(
            PythonWheel.objects.filter(sha256__in=[w.sha256 for w in result.wheels]).values_list("sha256", flat=True)
        )
        written = session._keep_lease(lambda: packages.write_wheels(result.wheels, known))
        return result, packages.register_wheels(result.wheels, written)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def restore(session: "SandboxSession", sandbox: Sandbox, spec: SandboxSpec) -> str:
    """起動したSandboxへ環境の版を入れ直す。入れられなかったpackageがあれば、モデルへの知らせを返す。

    イメージ・platformが版と違うとき（Runtime Profileの版が変わった、別のarchitectureのWorker）と、直接頼んだ
    packageがカタログで停止・削除されたときは、残すpackageを同じ版で取り直して新しい版にする（design.md §7.7）
    """
    revision = packages.current_revision(session.run, session.subagent)
    if revision is None:
        return ""
    keep = [r for r in revision.requested if packages.allowed(r["name"], r["version"])]
    dropped = [r["name"] for r in revision.requested if r not in keep]
    if revision.image == spec.image and revision.platform == spec.platform and not dropped:
        return _reinstall(session, sandbox, spec, revision)
    notice = PACKAGES_MISSING.format(names=", ".join(dropped), reason=NO_LONGER_IN_CATALOG) if dropped else ""
    if not keep:
        _record_attempt(session, None)
        return notice
    return _rebuild(session, sandbox, spec, revision, keep) or notice


def _reinstall(session: "SandboxSession", sandbox: Sandbox, spec: SandboxSpec, revision: EnvironmentRevision) -> str:
    """同じ条件のSandboxへ、版のwheelをそのまま入れる"""
    try:
        _install(session, sandbox, spec, list(revision.wheels.all()))
    except PackageError as exc:
        logger.warning("run %s: cannot reinstall environment %s: %s", session.run.uid, revision.uid, exc)
        session._keep_lease(lambda: packages.reset_env(sandbox))
        _record_attempt(session, None)
        return PACKAGES_MISSING.format(names=_names(revision.requested), reason="")
    _record_attempt(session, revision)
    return ""


def _rebuild(
    session: "SandboxSession",
    sandbox: Sandbox,
    spec: SandboxSpec,
    revision: EnvironmentRevision,
    keep: list[dict[str, str]],
) -> str:
    """残すpackageを同じ版で、このSandboxの条件向けに取り直して入れ、新しい版にする。入れられなければ知らせを返す"""
    lines = [f"{r['name']}=={r['version']}" for r in keep]
    try:
        result, wheels = _fetch(session, spec, lines, [])
        _install(session, sandbox, spec, wheels)
    except PackageError as exc:
        logger.warning("run %s: cannot rebuild environment %s: %s", session.run.uid, revision.uid, exc)
        session._keep_lease(lambda: packages.reset_env(sandbox))
        _record_attempt(session, None)
        return PACKAGES_MISSING.format(names=_names(keep), reason="")
    rebuilt = packages.record_revision(
        session.run,
        session.subagent,
        parent=revision,
        spec=spec,
        python_version=result.python_version,
        requested=keep,
        added=wheels,
        base=[],
        bundle_requested=[],
    )
    _record_attempt(session, rebuilt)
    return ""


def add(session: "SandboxSession", requests: list[Requested]) -> Added:
    """頼まれたpackageを取得してSandboxへ入れ、新しい環境の版にする。失敗したら環境を前の版に戻して PackageError"""
    sandbox = session.ensure()
    spec = session.spec
    assert spec is not None
    base = session.environment
    base_wheels = list(base.wheels.all()) if base is not None else []
    names = {r.name for r in requests}
    # 頼んだpackageは、それまでの版に縛らない（版を変える依頼もある）
    pinned = [(w.name, w.version) for w in base_wheels if w.name not in names]
    result, wheels = _fetch(session, spec, packages.requirements(requests), pinned)
    outside = [
        f"{w.name} {w.version}" for w in result.wheels if w.requested and not packages.allowed(w.name, w.version)
    ]
    if outside:
        raise PackageError(f"Outside the package catalog: {', '.join(outside)}. {NOT_IN_CATALOG}")
    if wheels:
        try:
            _install(session, sandbox, spec, wheels)
        except PackageError:
            # 半端に入った状態で続けない。前の版を入れ直す（design.md §7.6）
            session._keep_lease(lambda: packages.reset_env(sandbox))
            if base_wheels:
                try:
                    _install(session, sandbox, spec, base_wheels)
                except PackageError:
                    session._keep_lease(lambda: packages.reset_env(sandbox))
                    _record_attempt(session, None)
            raise
    added_names = {w.name for w in result.wheels}
    already = [r.name for r in requests if r.name not in added_names]
    revision = packages.record_revision(
        session.run,
        session.subagent,
        parent=base,
        spec=spec,
        python_version=result.python_version,
        requested=packages.merge_requested(base.requested if base is not None else [], result.wheels, result.installed),
        added=wheels,
        base=base_wheels,
        bundle_requested=[{"name": r.name, "version": r.version} for r in requests],
    )
    _record_attempt(session, revision)
    return Added(revision, result.wheels, already)


def _names(requested: list[dict[str, str]]) -> str:
    return ", ".join(f"{r['name']} {r['version']}" for r in requested)
