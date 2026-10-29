"""RunnerからSandboxを使う（harness.md §8、design.md §7.5・§7.9）。

SandboxはAttemptごとに1つで、最初にSandboxのToolが呼ばれた時点で起動し、最後のCheckpointからWorkspaceを戻す。
コマンドはすべてバックグラウンドのジョブとして始め、`shell` は終わりを待つだけにする。ジョブの状態の正本はSandboxの
外にあるもの（Dockerのexecの状態と、Worker側のファイルへ書いた出力）だけにする。Attemptを終えるときは、ジョブを
止めてからWorkspaceを取り出して保存し（Checkpoint）、Sandboxを消す。
"""

import hashlib
import logging
import os
import posixpath
import shutil
import tarfile
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, TypeVar

from django.conf import settings
from django.utils import timezone
from nanoid import generate

from artifact.models import CONNECTOR_RESULTS_DIR, Artifact, ArtifactKind, BlobObject
from artifact.storage import StorageBackend, StorageError, StoredObject, get_storage
from run.models import Checkpoint, ExecutionAttempt, Run
from runtime.launcher import (
    ATTEMPT_LABEL,
    MASTER_DIR,
    RUN_LABEL,
    WORK_DIR,
    Exec,
    Launcher,
    Sandbox,
    SandboxSpec,
    SandboxUnavailable,
)
from skill.models import Skill, SkillEvaluation

from .skills import asset_path, content_key, draft_asset_path, load_contents, loaded_entries

logger = logging.getLogger(__name__)
T = TypeVar("T")

WORKSPACE = "/workspace"
# snapshotに入れるもの。input・masterは固定の版から、envはイメージと追加のpackageから作り直す（design.md §7.9）
SNAPSHOT_PATHS = ["work", "output"]
# 1回に回収する成果物（/workspace/output のファイル）の数の上限（design.md §7.4）
MAX_OUTPUT_FILES = 200
# 同時に動かせるジョブの数（harness.md §8.3）
MAX_JOBS = 4
# ジョブを止めるときに、TERMの後でKILLを送るまで待つ時間（秒）
KILL_GRACE = 5
# Worker側に書くコマンドの出力の上限（バイト）。1つのジョブと、1つのAttemptの合計。超えた分は捨てる
JOB_OUTPUT_LIMIT = 64 * 1024 * 1024
ATTEMPT_OUTPUT_LIMIT = 512 * 1024 * 1024
# コマンドが終わった後、出力を受け取り終えるのを待つ上限（秒）。受け取りが止まったときに、待ち続けないため
OUTPUT_DRAIN_SECONDS = 60
# 長いSandboxの操作（Workspaceの取り出し・戻し）の間に、leaseを延ばす間隔（秒）
HEARTBEAT_INTERVAL = 15
JOB_ID_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"

# Sandboxがメモリの上限を超えて止まったAttemptの失敗の理由（ExecutionAttempt.failure の先頭）
OUT_OF_MEMORY_FAILURE = "sandbox: out of memory"
OUT_OF_MEMORY_NOTICE = (
    "[The sandbox was stopped because the commands in it used more memory than its limit ({memory_mb} MB), so the "
    "previous attempt was interrupted. Use less memory: process large files in parts or stream them instead of "
    "loading everything at once, and run fewer processes at the same time.]"
)
WORKSPACE_LOST = (
    "[The files saved from the previous work could not be restored on this worker, so /workspace/work and "
    "/workspace/output are empty. Check which files you need and recreate them before continuing.]"
)


def new_job_id() -> str:
    return "job_" + generate(JOB_ID_ALPHABET, 8)


def command_env() -> dict[str, str]:
    """呼出しごとに渡す環境変数（sandbox/CONTRACT.md §3）。これだけを渡し、コマンドの中の export は次へ残らない"""
    return {
        "HOME": f"{WORK_DIR}/.home",
        "PATH": "/workspace/env/bin:/opt/yoriki/bin:/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "TZ": settings.TIME_ZONE,
        "PAGER": "cat",
        "GIT_PAGER": "cat",
        "TERM": "dumb",
        "NO_COLOR": "1",
        "PYTHONUNBUFFERED": "1",
        "MPLBACKEND": "Agg",
        "MPLCONFIGDIR": f"{WORK_DIR}/.cache/matplotlib",
        "XDG_CACHE_HOME": f"{WORK_DIR}/.cache",
        "PIP_CACHE_DIR": f"{WORK_DIR}/.cache/pip",
    }


def workspace_path(path: str, base: str = WORK_DIR) -> str | None:
    """`/workspace` の中の正規化した絶対パス。外ならNone。symlinkの先は補助コマンドが確かめる（CONTRACT.md §4）"""
    if not path:
        return None
    joined = posixpath.normpath(posixpath.join(base, path))
    if joined != WORKSPACE and not joined.startswith(WORKSPACE + "/"):
        return None
    return joined


@dataclass(frozen=True)
class CollectedOutput:
    """/workspace/output から回収して保存したファイル。Artifactの行はRunnerがleaseの確認の中で作る"""

    path: str
    stored: StoredObject


@dataclass(frozen=True)
class Suspended:
    """Attemptを終えるときに保存したもの"""

    snapshot: StoredObject
    backend_id: str
    # 保存の前に止めたジョブ（[{"job_id", "command"}]）
    stopped: list[dict[str, str]]
    outputs: list[CollectedOutput]
    # 保存したときに /workspace/output にあった（回収の対象の）ファイルの、パスごとの中身（SHA-256）。回収し直さなかった
    # 変わっていないファイルを含む。業務結果が指すファイルが完了時にあるかを確かめるのに使う
    present: dict[str, str] = field(default_factory=dict)


@dataclass
class Job:
    job_id: str
    command: str
    description: str
    workdir: str
    execution: Exec
    output_path: Path
    timeout_seconds: int
    started_at: float = field(default_factory=time.monotonic)
    # モデルに見せた出力の位置（job_output は前回の続きを返す）
    read_offset: int = 0
    exit_code: int | None = None
    finished: bool = False
    # job.finished を記録した
    reported: bool = False
    # 止めた理由（"timeout"・"killed"・"suspended"）
    stopped: str = ""
    # Worker側に書いてよい出力の上限（バイト）。動いている間は、この分をAttemptの合計の上限から予約する
    output_limit: int = 0
    # Dockerがコマンドの終了を報告した時刻。出力を受け取り終えるまで、終わりにしない
    exited_at: float | None = None

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def output_size(self) -> int:
        try:
            return self.output_path.stat().st_size
        except FileNotFoundError:
            return 0

    def read_output(self, start: int = 0, limit: int | None = None) -> str:
        """Worker側のファイルから読む。エージェントのコマンドが書き換えられるSandboxの中のファイルは使わない"""
        try:
            with self.output_path.open("rb") as f:
                f.seek(start)
                data = f.read() if limit is None else f.read(limit)
        except FileNotFoundError:
            return ""
        return data.decode(errors="replace")


class SandboxSession:
    def __init__(
        self,
        run: Run,
        attempt: ExecutionAttempt,
        *,
        launcher_factory: Callable[[], Launcher] = Launcher,
        storage_factory: Callable[..., StorageBackend] = get_storage,
        heartbeat: Callable[[], None] = lambda: None,
    ):
        self.run = run
        self.attempt = attempt
        self.launcher_factory = launcher_factory
        self.storage_factory = storage_factory
        self.heartbeat = heartbeat
        self.sandbox: Sandbox | None = None
        self.jobs: dict[str, Job] = {}
        # Attemptの作業領域（ジョブの出力）。Sandboxには見せない
        self.dir = Path(settings.YORIKI_WORKSPACES_DIR) / "attempts" / f"{run.uid}-{attempt.number}"
        # 戻したCheckpoint。このRunのものなら、その時点までの読んだファイルの版の記録を使える（harness.md §11.1）
        self.restored: Checkpoint | None = None
        # /workspace/input に置いた添付（ArtifactのID）と、/workspace/master に置いたSkillの中身（content_key()）
        self.inputs: set[int] = set()
        self.skill_versions: set[str] = set()
        # 最初のSandboxのToolの結果に添える知らせ
        self.notice = ""
        # DBに触ってよいスレッド（Runner）
        self.owner_thread = threading.get_ident()

    @property
    def started(self) -> bool:
        return self.sandbox is not None

    # --- 起動と復元 ---

    def ensure(self) -> Sandbox:
        if self.sandbox is not None:
            return self.sandbox
        version = self.run.runtime_version
        if version is None:
            raise SandboxUnavailable("this run has no runtime profile", retryable=False)
        launcher = self.launcher_factory()
        remove_stale_sandboxes(launcher)
        platform = launcher.platform()
        self.sync_inputs()
        self.sync_skill_assets()
        spec = SandboxSpec.for_version(
            version,
            platform,
            labels={RUN_LABEL: self.run.uid, ATTEMPT_LABEL: str(self.attempt.pk)},
            input_dir=str(self.input_dir),
            master_dir=str(self.master_dir),
        )
        with self.run.fenced(self.attempt):
            ExecutionAttempt.objects.filter(pk=self.attempt.pk).update(
                sandbox_platform=platform, sandbox_image=spec.image
            )
        name = f"yoriki-sandbox-{self.run.uid}-{self.attempt.number}"
        # イメージのpullで、Attemptのleaseより長くかかることがある
        self.sandbox = self._keep_lease(lambda: launcher.start(spec, name), on_abandon=Sandbox.remove)
        self._restore(self.sandbox)
        return self.sandbox

    @property
    def input_dir(self) -> Path:
        return self.dir / "input"

    @property
    def master_dir(self) -> Path:
        return self.dir / "master"

    def sync_skill_assets(self) -> None:
        """Manifestに固定・追記した全Skillの資料を、/workspace/master にmountするWorkerのディレクトリに置く
        （Sandboxを起動する前に呼ぶ。design.md §3.8）"""
        self.master_dir.mkdir(parents=True, exist_ok=True)
        self.master_dir.chmod(0o755)
        run = Run.objects.only("manifest").get(pk=self.run.pk)
        entries = [e for e in loaded_entries(run) if content_key(e) not in self.skill_versions]
        if not entries:
            return
        skills = {s.uid: s for s in Skill.objects.filter(uid__in=[e["skill"] for e in entries])}
        contents = load_contents(entries)
        for entry in entries:
            self.place_skill_assets(skills[entry["skill"]], contents[content_key(entry)])

    def place_skill_assets(self, skill, version) -> None:
        """Skillの版の資料を /workspace/master/skills/<Skill>/<key>/ に置く。bind mountなので、動いているSandboxにも
        そのまま見える（harness.md §10.1）。Draftの評価のRunでは、評価に写したDraftの資料を同じ場所に置く
        （design.md §3.7.1）。置けなければ SandboxUnavailable"""
        key = f"evaluation:{version.pk}" if isinstance(version, SkillEvaluation) else f"version:{version.pk}"
        if key in self.skill_versions:
            return
        self._place_assets(
            self.master_dir / "skills" / skill.uid,
            [(asset_path(skill.uid, a.key, a.resource_version.name), a.resource_version) for a in version.assets.all()],
        )
        self.skill_versions.add(key)

    def place_draft_assets(self, skill, draft) -> None:
        """SkillのDraftの資料を /workspace/master/drafts/<Skill>/r<revision>/<key>/ に置く（Skillの編集のRun。
        harness.md §10.9）。前のrevisionの資料は残さない"""
        self._place_assets(
            self.master_dir / "drafts" / skill.uid,
            [
                (draft_asset_path(skill.uid, draft.revision, a.key, a.resource_version.name), a.resource_version)
                for a in draft.assets.all()
            ],
        )

    def _place_assets(self, root: Path, files: list[tuple[str, Any]]) -> None:
        """資料の版（ResourceVersion）を、/workspace/master の中のパスに置く。`root` の下の前の中身は消す"""
        self.master_dir.mkdir(parents=True, exist_ok=True)
        self.master_dir.chmod(0o755)
        if root.exists():
            # 同じSkillの別の版（版の切り替え）。前の版の資料を残さない
            shutil.rmtree(root)
        for sandbox_path, resource in files:
            path = self.master_dir / posixpath.relpath(sandbox_path, MASTER_DIR)
            if self.master_dir not in path.parents:
                logger.warning("run %s: skipping skill asset %s with a bad name", self.run.uid, resource.pk)
                continue
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                for parent in path.relative_to(self.master_dir).parents:
                    (self.master_dir / parent).chmod(0o755)
                with resource.blob.open() as src, tempfile.NamedTemporaryFile(dir=self.dir, delete=False) as tmp:
                    shutil.copyfileobj(src, tmp, 1024 * 1024)
                os.chmod(tmp.name, 0o644)
                os.replace(tmp.name, path)
            except (OSError, StorageError) as exc:
                raise SandboxUnavailable(f"cannot prepare the skill file {resource.uid}: {exc}") from exc

    def read_file(self, path: str, limit: int) -> bytes:
        """Sandboxの中のファイル（/workspace の下の正規化した絶対パス）の中身を、そのままのbytesで読む。
        Sandboxの中のtarで取り出し、通常のファイルでなければ（symlink・ディレクトリ）FileNotFoundError、
        `limit` バイトを超えれば ValueError"""
        sandbox = self.ensure()
        relative = posixpath.relpath(path, WORKSPACE)

        def fetch() -> bytes:
            with tempfile.TemporaryFile(dir=self.dir) as archive:
                sandbox.archive([relative], archive)
                archive.seek(0)
                with tarfile.open(fileobj=archive) as tar:
                    member = tar.next()
                    if member is None or not member.isfile() or member.name.rstrip("/") != relative:
                        raise FileNotFoundError(path)
                    if member.size > limit:
                        raise ValueError(f"{path} is larger than {limit} bytes")
                    src = tar.extractfile(member)
                    assert src is not None  # 通常のファイル
                    return src.read()

        self.dir.mkdir(parents=True, exist_ok=True)
        if threading.get_ident() == self.owner_thread:
            return self._keep_lease(fetch)
        return fetch()

    def sync_inputs(self) -> None:
        """会話の添付（取り込んだ依頼のもの）とConnectorの結果を、Workerのディレクトリ（Sandboxの /workspace/input に
        読取り専用でmountする）に置く。まだ置いていないものだけを足す。bind mountなので、動いているSandboxにも
        そのまま見える。Connectorの結果は connectors/<呼出し>/ の下に置く（harness.md §10.2）"""
        conversation_id = self.run.conversation_id
        # 保持期限の切れたConnectorの結果は置かない（会話で照会を重ねても、置くものが増え続けないように）
        results = Artifact.objects.connector_results(conversation_id).filter(expires__gt=timezone.now())
        inputs = (
            (Artifact.objects.sent_inputs(conversation_id) | results)
            .exclude(pk__in=self.inputs)
            .select_related("blob")
            .order_by("pk")
        )
        self.input_dir.mkdir(parents=True, exist_ok=True)
        # userns-remap・gVisorのSandboxの利用者からも読めるようにする（書けるのはWorkerだけ）
        self.input_dir.chmod(0o755)
        for artifact in inputs:
            path = self.input_dir / artifact.path
            if not self._input_path_ok(artifact, path):
                logger.warning("run %s: skipping input %s with a bad name", self.run.uid, artifact.uid)
                continue
            try:
                for parent in reversed(path.relative_to(self.input_dir).parents[:-1]):
                    (self.input_dir / parent).mkdir(exist_ok=True)
                    (self.input_dir / parent).chmod(0o755)
                with artifact.blob.open() as src, tempfile.NamedTemporaryFile(dir=self.dir, delete=False) as tmp:
                    shutil.copyfileobj(src, tmp, 1024 * 1024)
                os.chmod(tmp.name, 0o644)
                os.replace(tmp.name, path)
            except (OSError, StorageError) as exc:
                raise SandboxUnavailable(f"cannot prepare the attachment {artifact.uid}: {exc}") from exc
            self.inputs.add(artifact.pk)

    def _input_path_ok(self, artifact: Artifact, path: Path) -> bool:
        """添付は /workspace/input の直下、Connectorの結果は connectors/ の下だけに置く。Connectorの結果の
        ディレクトリと同じ名前の添付（その名前を避けるようにする前に送ったもの）は置かない"""
        if artifact.kind == ArtifactKind.CONNECTOR_RESULT:
            parts = Path(artifact.path).parts
            return (
                len(parts) == 3
                and parts[0] == CONNECTOR_RESULTS_DIR
                and all(p not in ("", ".", "..") for p in parts)
                and self.input_dir in path.parents
            )
        return path.parent == self.input_dir and artifact.path != CONNECTOR_RESULTS_DIR

    def _restore(self, sandbox: Sandbox) -> None:
        checkpoint = Checkpoint.latest_for(self.run)
        if checkpoint is None:
            return
        # 記録した保存先から読む（既定の保存先を変えた後も、以前の保存先の設定があれば読める）
        stored = None
        try:
            storage = self.storage_factory(checkpoint.storage)
            stored = storage.stat(checkpoint.key)
        except StorageError:
            logger.warning("run %s: cannot read checkpoint %s", self.run.uid, checkpoint.uid, exc_info=True)
        if stored is None or stored.sha256 != checkpoint.sha256:
            # 別のWorkerのローカルディスクに保存した等。空のWorkspaceで続け、モデルに知らせる
            logger.warning("run %s: checkpoint %s is not available here", self.run.uid, checkpoint.uid)
            self.notice = WORKSPACE_LOST
            return
        with storage.open_read(checkpoint.key) as src:
            self._keep_lease(lambda: sandbox.restore(src))
        self.restored = checkpoint

    def take_notice(self) -> str:
        notice, self.notice = self.notice, ""
        return notice

    # --- コマンド ---

    def running_jobs(self, *, refresh: bool = False) -> list[Job]:
        if refresh:
            for job in list(self.jobs.values()):
                self.refresh(job)
        return [job for job in self.jobs.values() if not job.finished]

    def put_job_output(self, job: Job, path: str, start: int, end: int) -> bool:
        """ジョブの出力（Worker側のログの `start` から `end` まで）を、Sandboxの `path` へそのまま書く"""
        sandbox = self.ensure()
        with tempfile.TemporaryFile(dir=self.dir) as copy:
            with job.output_path.open("rb") as log:
                log.seek(start)
                remaining = end - start
                while remaining > 0 and (chunk := log.read(min(remaining, 1024 * 1024))):
                    copy.write(chunk)
                    remaining -= len(chunk)
            copy.seek(0)
            if threading.get_ident() == self.owner_thread:
                return self._keep_lease(lambda: sandbox.put_file(path, copy))
            return sandbox.put_file(path, copy)

    def helper(self, command: str, request: dict[str, Any]) -> dict[str, Any]:
        """補助コマンドを呼ぶ。Runnerのスレッドからなら、その間もleaseを延ばす（大きいPDFの read 等）。
        並べて実行するToolの別のスレッドからなら、Runnerのスレッドが結果を待つ間にleaseを延ばす"""
        sandbox = self.ensure()
        if threading.get_ident() == self.owner_thread:
            return self._keep_lease(lambda: sandbox.helper(command, request))
        return sandbox.helper(command, request)

    def start_job(self, command: str, workdir: str, description: str) -> Job:
        """呼び出し側で、動いているジョブの数を確かめておくこと"""
        sandbox = self.ensure()
        job_id = new_job_id()
        path = self.dir / "jobs" / f"{job_id}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        output = path.open("wb")
        # 終わったジョブは実際の大きさ、動いているジョブは割り当てた上限を数える（同時に動くジョブで上限を分け合う）
        used = sum(job.output_size() if job.finished else job.output_limit for job in self.jobs.values())
        limit = max(0, min(JOB_OUTPUT_LIMIT, ATTEMPT_OUTPUT_LIMIT - used))
        request = {"command": command, "cwd": workdir, "env": command_env()}
        execution = sandbox.start_exec(request, output, output_limit=limit)
        job = Job(
            job_id=job_id,
            command=command,
            description=description,
            workdir=workdir,
            execution=execution,
            output_path=path,
            timeout_seconds=self.run.runtime_version.job_timeout_seconds if self.run.runtime_version else 1800,
            output_limit=limit,
        )
        self.jobs[job_id] = job
        execution.started.wait(timeout=10)
        return job

    def wait(self, job: Job, seconds: float, *, interrupted: Callable[[], bool] = lambda: False) -> bool:
        """ジョブの終わりを `seconds` まで待つ。待つ間もleaseを延ばす。終わったらTrue"""
        deadline = time.monotonic() + seconds
        last_beat = time.monotonic()
        while not self.refresh(job):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or interrupted():
                return False
            job.execution.done.wait(min(remaining, 1.0))
            if time.monotonic() - last_beat >= 15:
                self.heartbeat()
                last_beat = time.monotonic()
        return True

    def refresh(self, job: Job) -> bool:
        """ジョブが終わったかを、Dockerのexecの状態で確かめる。実行時間の上限を過ぎていれば止める"""
        if job.finished:
            return True
        if job.execution.error:
            raise SandboxUnavailable(f"lost the connection to the command: {job.execution.error}")
        running, exit_code = self._sandbox().exec_state(job.execution.exec_id)
        if not running:
            # 出力を受け取り終えてから終わりにする（途中までの出力を、終わった結果として返さない）
            job.exited_at = job.exited_at or time.monotonic()
            drained = job.execution.done.wait(timeout=1)
            if not drained and time.monotonic() - job.exited_at < OUTPUT_DRAIN_SECONDS:
                return False
            if not drained:
                logger.warning("run %s: output of %s did not finish draining", self.run.uid, job.job_id)
            job.finished, job.exit_code = True, exit_code
            return True
        if job.elapsed > job.timeout_seconds:
            return self.kill(job, reason="timeout")
        return False

    def finished_unreported(self) -> list[Job]:
        """終わったが、まだ job.finished を記録していないジョブ"""
        done = []
        for job in self.jobs.values():
            if not job.reported and self.refresh(job):
                done.append(job)
        return done

    def kill(self, job: Job, *, reason: str = "killed") -> bool:
        """プロセスグループへTERM、終わらなければKILLを送る。Dockerが終了を確かめたときだけ止めたことにし、Trueを返す。
        プロセスグループがまだ分からない・止まらないジョブは、動いているままにする"""
        if job.finished:
            return True
        if job.execution.pgid is None:
            job.execution.started.wait(timeout=KILL_GRACE)
        pgid = job.execution.pgid
        if pgid is not None:
            self.helper("kill", {"pgid": pgid, "signal": "TERM"})
            if not job.execution.done.wait(timeout=KILL_GRACE):
                self.helper("kill", {"pgid": pgid, "signal": "KILL"})
                job.execution.done.wait(timeout=KILL_GRACE)
        return self._mark_stopped(job, reason)

    def _mark_stopped(self, job: Job, reason: str) -> bool:
        running, exit_code = self._sandbox().exec_state(job.execution.exec_id)
        if running:
            return False
        job.execution.done.wait(timeout=KILL_GRACE)
        job.stopped = reason
        job.finished, job.exit_code = True, exit_code
        return True

    def out_of_memory(self) -> bool:
        """Sandboxがメモリの上限を超えて止まったか"""
        return self.sandbox is not None and self.sandbox.out_of_memory()

    def _sandbox(self) -> Sandbox:
        if self.sandbox is None:
            raise SandboxUnavailable("the sandbox is not started")
        return self.sandbox

    # --- Attemptの終わり ---

    def suspend(self) -> Suspended | None:
        """動いているものを止め、Workspaceを取り出して保存し、/workspace/output のファイルを成果物として回収する。
        Sandboxを起動していなければNone。Checkpoint・Artifactの行はRunnerがleaseの確認の中で作る
        """
        if self.sandbox is None:
            return None
        # 直前に終わったジョブを、止めたジョブとして記録しない
        running = self.running_jobs(refresh=True)
        for job in running:
            self.kill(job, reason="suspended")
        # setsid 等でグループを離れたもの、プロセスグループの分からなかったジョブも残さない（CONTRACT.md §4.2）
        self.helper("kill", {"all": True, "signal": "KILL"})
        for job in running:
            if not job.finished and not self._mark_stopped(job, "suspended"):
                raise SandboxUnavailable(f"job {job.job_id} did not stop")
        stopped = [{"job_id": job.job_id, "command": job.command[:200]} for job in running]
        storage = self.storage_factory()
        self.dir.mkdir(parents=True, exist_ok=True)
        key = f"snapshots/runs/{self.run.uid}/{self.attempt.number}-{generate(JOB_ID_ALPHABET, 8)}.tar"
        sandbox = self.sandbox
        known = self._known_outputs()
        present: dict[str, str] = {}

        def save() -> tuple[StoredObject, list[CollectedOutput]]:
            with tempfile.TemporaryFile(dir=self.dir) as archive:
                sandbox.archive(SNAPSHOT_PATHS, archive)
                archive.seek(0)
                try:
                    snapshot = storage.put_immutable(key, archive)
                    archive.seek(0)
                    return snapshot, collect_outputs(archive, storage, known, present)
                except (OSError, StorageError, tarfile.TarError) as exc:
                    raise SandboxUnavailable(f"cannot save the workspace: {exc}") from exc

        snapshot, outputs = self._keep_lease(save)
        return Suspended(snapshot, storage.backend_id, stopped, outputs, present)

    def _known_outputs(self) -> dict[str, str]:
        """会話の成果物の、パスごとの最新の中身（SHA-256）。変わっていないファイルは回収し直さない"""
        known = {}
        rows = (
            # 削除した成果物は既知にしない（同じ中身のファイルを回収し直す。業務結果のファイルの確認で見つかるように）
            Artifact.objects.filter(conversation_id=self.run.conversation_id, kind=ArtifactKind.OUTPUT, deleted=False)
            .order_by("created", "pk")
            .values_list("path", "blob__sha256")
        )
        for path, sha256 in rows:
            known[path] = sha256
        return known

    def _keep_lease(self, operation: Callable[[], T], *, on_abandon: Callable[[T], object] | None = None) -> T:
        """時間のかかる操作を別のスレッドで行い、その間このスレッドでleaseを延ばす（DBに触るのはこのスレッドだけ）。

        leaseを失ったら、操作の終わりを待ってから LeaseLost で抜ける。操作が結果を返していれば `on_abandon` に渡す
        （起動し終えたSandboxを消す等）
        """
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="sandbox-io") as pool:
            future = pool.submit(operation)
            while True:
                try:
                    return future.result(timeout=HEARTBEAT_INTERVAL)
                except FutureTimeout:
                    try:
                        self.heartbeat()
                    except BaseException:
                        if on_abandon is not None and future.exception() is None:
                            on_abandon(future.result())
                        raise

    def close(self) -> None:
        """Sandboxを消し、Attemptの作業領域を消す。どの終わり方でも呼ぶ"""
        if self.sandbox is not None:
            self.sandbox.remove()
            self.sandbox = None
        shutil.rmtree(self.dir, ignore_errors=True)


def collect_outputs(
    archive: IO[bytes], storage: StorageBackend, known: dict[str, str], present: dict[str, str] | None = None
) -> list[CollectedOutput]:
    """Workspaceのtarから /workspace/output の通常のファイルを取り出して保存する（design.md §7.4）。
    symlink・特殊なファイル・隠しファイル・変わっていないファイルは回収しない。数が多すぎれば、先頭から上限まで。
    `present` には、回収の対象のファイル（変わっていないものを含み、上限を超えた分は含まない）の中身のSHA-256を入れる"""
    collected: list[CollectedOutput] = []
    with tarfile.open(fileobj=archive, mode="r:") as tar:
        for member in tar:
            if not member.isfile() or not member.name.startswith("output/"):
                continue
            path = posixpath.normpath(member.name.removeprefix("output/"))
            if path.startswith(("/", "..")) or any(part.startswith(".") for part in path.split("/")):
                continue
            if len(collected) >= MAX_OUTPUT_FILES:
                logger.warning("too many files in /workspace/output; collected the first %d", MAX_OUTPUT_FILES)
                break
            src = tar.extractfile(member)
            if src is None:
                continue
            digest = hashlib.sha256()
            while chunk := src.read(1024 * 1024):
                digest.update(chunk)
            if present is not None:
                present[path] = digest.hexdigest()
            if known.get(path) == digest.hexdigest():
                continue
            src.seek(0)
            collected.append(CollectedOutput(path, storage.put_immutable(BlobObject.new_key(), src)))
    return collected


def remove_stale_sandboxes(launcher: Launcher) -> None:
    """終わったAttemptのSandboxが残っていれば消す（Workerが落ちた等）。leaseの切れたAttemptのものも消す"""
    containers = launcher.containers()
    ids = {}
    for container in containers:
        value = (container.get("Labels") or {}).get(ATTEMPT_LABEL, "")
        if value.isdigit():
            ids[container["Id"]] = int(value)
    if not ids:
        return
    alive = set(
        ExecutionAttempt.objects.filter(
            pk__in=set(ids.values()), ended__isnull=True, lease_expires__gt=timezone.now()
        ).values_list("pk", flat=True)
    )
    for container_id, attempt_id in ids.items():
        if attempt_id not in alive:
            logger.info("removing a stale sandbox of attempt %s", attempt_id)
            launcher.remove(container_id)
