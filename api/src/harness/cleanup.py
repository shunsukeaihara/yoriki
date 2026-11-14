"""実行の残骸の清掃（design.md §8）。Q2のSchedule（run.tasks.cleanup_orphans）と、管理コマンド（cleanup_orphans）
から呼ぶ。

- コンテナ：終わったAttempt・leaseの切れたAttemptのSandbox（Sandboxを起動するときにも消す。sandbox.py）
- 作業領域：`YORIKI_WORKSPACES_DIR/attempts/` に残った、生きていないAttemptのディレクトリ
- 保存先：確定する前に落ちたsnapshotとサブエージェントへのコピーの実体（CheckpointにもSubagentにも記録がないobject）

コンテナと作業領域は、清掃を動かしたWorkerのものだけを見る（Workerサーバーが複数あれば、ほかのWorkerのものは、そこで
Sandboxを起動するときか、そこで管理コマンドを流したときに消える）。どれも、このDBにあるRunのものだけを扱い、他のDBの
もの・どのDBのものか分からないものは触らない。
"""

import logging
import os
import shutil
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from django.db.models import Exists, OuterRef
from django.utils import timezone

from artifact.storage import StorageBackend, StorageError, get_storage
from run.models import Checkpoint, ExecutionAttempt, Run, Subagent
from runtime.launcher import Launcher, SandboxUnavailable
from utils.fields import NANOID_ALPHABET, NANOID_SIZE

from .sandbox import attempt_dir_name, attempts_root, stale_sandboxes

logger = logging.getLogger(__name__)

# leaseが切れてからこの時間は、まだ動いているものとして扱う。leaseの確認の中で始めた書込み（Checkpointの作成等）が
# commitされる前に、その実体を消さないため
LEASE_GRACE = timedelta(hours=1)
# 実体を探す保存先のkeyの接頭辞。2つ目の階層がRunのuid（harness/sandbox.py の suspend()、harness/tools/subagent.py）
OBJECT_PREFIXES = ("snapshots/runs/", "subagents/")
BATCH = 500


@dataclass(frozen=True)
class OrphanCleanupResult:
    """消した数（dry-runでは対象の数）。`failed` は消そうとして消せなかった数"""

    sandboxes: int
    workspaces: int
    objects: int
    failed: int


def cleanup_orphans(
    *,
    dry_run: bool = False,
    launcher_factory: Callable[[], Launcher] | None = None,
    storage_factory: Callable[[], StorageBackend] | None = None,
) -> OrphanCleanupResult:
    sandboxes, failed_sandboxes = _cleanup_sandboxes(launcher_factory or Launcher, dry_run)
    workspaces, failed_workspaces = _cleanup_workspaces(dry_run)
    objects, failed_objects = _cleanup_objects(storage_factory or get_storage, dry_run)
    result = OrphanCleanupResult(
        sandboxes=sandboxes,
        workspaces=workspaces,
        objects=objects,
        failed=failed_sandboxes + failed_workspaces + failed_objects,
    )
    if not dry_run and (result.sandboxes or result.workspaces or result.objects or result.failed):
        logger.info(
            "orphan cleanup: %d sandboxes, %d workspaces, %d objects, %d failed",
            result.sandboxes,
            result.workspaces,
            result.objects,
            result.failed,
        )
    return result


def _known_runs(uids: Iterable[str], *, settled: bool = False) -> set[str]:
    """このDBにあるRunのuid。`settled` なら、生きているAttempt（親・サブエージェント）がないものだけ"""
    rows = Run.objects.all()
    if settled:
        live = ExecutionAttempt.objects.filter(
            run=OuterRef("pk"), ended__isnull=True, lease_expires__gt=timezone.now() - LEASE_GRACE
        )
        rows = rows.exclude(Exists(live))
    found: set[str] = set()
    uids = sorted(uids)
    for start in range(0, len(uids), BATCH):
        found.update(rows.filter(uid__in=uids[start : start + BATCH]).values_list("uid", flat=True))
    return found


def _is_uid(value: str) -> bool:
    return len(value) == NANOID_SIZE and all(c in NANOID_ALPHABET for c in value)


# --- コンテナ ---


def _cleanup_sandboxes(launcher_factory: Callable[[], Launcher], dry_run: bool) -> tuple[int, int]:
    try:
        launcher = launcher_factory()
    except SandboxUnavailable as exc:
        # Dockerのないサーバーで動いた（Sandboxを起動しないので、残ったコンテナもない）
        logger.info("skipping the sandbox cleanup: %s", exc)
        return 0, 0
    stale = stale_sandboxes(launcher)
    if dry_run:
        return len(stale), 0
    for container_id, attempt_id in stale:
        logger.info("removing a stale sandbox of attempt %s", attempt_id)
        launcher.remove(container_id)
    # 消せなかったものは Sandbox.remove() がログに残す。次の清掃で数え直す
    left = {container_id for container_id, _ in stale_sandboxes(launcher)}
    failed = sum(1 for container_id, _ in stale if container_id in left)
    return len(stale) - failed, failed


# --- 作業領域 ---


def _parse_dir_name(name: str) -> tuple[str, str | None, int] | None:
    """`attempt_dir_name()` の逆。Runのuid、サブエージェントのuid、試行番号"""
    parts = name.split("-")
    if len(parts) not in (2, 3) or not parts[-1].isdigit() or not all(_is_uid(p) for p in parts[:-1]):
        return None
    return parts[0], parts[1] if len(parts) == 3 else None, int(parts[-1])


def _cleanup_workspaces(dry_run: bool) -> tuple[int, int]:
    root = attempts_root()
    if root.is_symlink() or not root.is_dir():
        return 0, 0
    found: dict[str, Path] = {}
    with os.scandir(root) as entries:
        for entry in entries:
            # 専用rootの直下のディレクトリだけ。symlinkは辿らない（design.md §8）
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                continue
            if _parse_dir_name(entry.name) is not None:
                found[entry.name] = Path(entry.path)
    if not found:
        return 0, 0
    known = _known_runs({name.split("-")[0] for name in found})
    alive = {
        attempt_dir_name(run_uid, subagent_uid, number)
        for run_uid, subagent_uid, number in ExecutionAttempt.objects.filter(
            run__uid__in=known, ended__isnull=True, lease_expires__gt=timezone.now() - LEASE_GRACE
        ).values_list("run__uid", "subagent__uid", "number")
    }
    targets = [path for name, path in found.items() if name not in alive and name.split("-")[0] in known]
    if dry_run:
        return len(targets), 0
    removed = failed = 0
    for path in targets:
        try:
            shutil.rmtree(path, onexc=_ignore_missing)
        except OSError:
            logger.warning("cannot remove the workspace %s", path.name, exc_info=True)
            failed += 1
        else:
            removed += 1
    return removed, failed


def _ignore_missing(function, path, exc: BaseException) -> None:
    # Workerが同時に消している（Attemptの終わりの close()）
    if not isinstance(exc, FileNotFoundError):
        raise exc


# --- 保存先 ---


def _cleanup_objects(storage_factory: Callable[[], StorageBackend], dry_run: bool) -> tuple[int, int]:
    """新しく保存する先（既定の保存先）だけを見る。記録のない実体は保存するときにしかできず、保存するのは既定の保存先"""
    storage = storage_factory()
    by_run: dict[str, list[str]] = defaultdict(list)
    try:
        for prefix in OBJECT_PREFIXES:
            for key in storage.list_keys(prefix):
                parts = key.split("/")
                run_uid = parts[prefix.count("/")] if len(parts) > prefix.count("/") + 1 else ""
                if _is_uid(run_uid):
                    by_run[run_uid].append(key)
    except StorageError:
        logger.warning("cannot list the objects of %s; skipping the object cleanup", storage.backend_id, exc_info=True)
        return 0, 0
    # 生きているAttemptがないことを確かめてから記録を読む（逆だと、その間に確定したCheckpointの実体を消しうる）
    settled = _known_runs(by_run, settled=True)
    keys = [key for run_uid in sorted(settled) for key in by_run[run_uid]]
    targets: list[str] = []
    for start in range(0, len(keys), BATCH):
        batch = keys[start : start + BATCH]
        referenced = set(
            Checkpoint.objects.filter(storage=storage.backend_id, key__in=batch).values_list("key", flat=True)
        ) | set(
            Subagent.objects.filter(copy_storage=storage.backend_id, copy_key__in=batch).values_list(
                "copy_key", flat=True
            )
        )
        targets.extend(key for key in batch if key not in referenced)
    if dry_run:
        return len(targets), 0
    removed = failed = 0
    for key in targets:
        try:
            storage.delete(key)
        except StorageError:
            logger.warning("cannot delete the orphaned object %s", key, exc_info=True)
            failed += 1
        else:
            logger.info("removed an orphaned object %s", key)
            removed += 1
    return removed, failed
