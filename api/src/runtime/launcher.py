"""Sandbox Launcher（design.md §7.3、sandbox/CONTRACT.md §2）。

Dockerの操作はここに集め、Runnerは決まった操作（補助コマンド、コマンドの実行、Workspaceの取り出し・戻し、削除）だけを
呼ぶ。起動の制限は契約より緩めず、起動した後にinspectで確かめ直して、違えば使わずに消す（design.md §7.2の対策の1）。
Dockerの操作はDockerのSDK（docker-py）で行い、apiのイメージにdocker CLIを入れない。
"""

import contextlib
import json
import logging
import socket
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import IO, Any

import docker
from django.conf import settings
from docker.errors import DockerException, ImageNotFound, NotFound
from docker.utils.socket import STDERR, STDOUT, frames_iter

from .contract import CONTRACT_LABEL, SUPPORTED_CONTRACTS

logger = logging.getLogger(__name__)

HELPER = "/opt/yoriki/bin/yoriki-sandbox"
SANDBOX_USER = "10001:10001"
WORK_DIR = "/workspace/work"
# Launcherが起動したコンテナに付けるラベル。残ったコンテナを探して消すときに使う
SANDBOX_LABEL = "org.yoriki.sandbox"
ATTEMPT_LABEL = "org.yoriki.attempt"
RUN_LABEL = "org.yoriki.run"
# 補助コマンドの応答を待つ時間（秒）。grep等は補助コマンドの中で時間を区切る
HELPER_TIMEOUT = 120
# Workspaceの取り出し・戻しを待つ時間（秒）
ARCHIVE_TIMEOUT = 600
CHUNK = 64 * 1024
OUTPUT_DISCARDED = b"\n[output beyond %d bytes was discarded; write large output to a file instead]\n"


class SandboxUnavailable(Exception):
    """Sandboxを起動できない・失われた・補助コマンドが壊れている。retryableなら、Runは別のAttemptでやり直す"""

    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class SandboxSpec:
    """起動するSandboxのイメージと資源の上限。Runが固定したRuntime Profileの版から作る"""

    image: str
    platform: str
    memory_mb: int
    cpus: Decimal
    pids_limit: int
    tmp_mb: int
    work_mb: int
    output_mb: int
    env_mb: int
    labels: dict[str, str] = field(default_factory=dict)

    @classmethod
    def for_version(cls, version, platform: str, *, labels: dict[str, str]) -> "SandboxSpec":
        """Workerのplatformに合うmanifestのdigestを選ぶ。合うものがなければ起動を断る（design.md §7.3）"""
        image = settings.YORIKI_SANDBOX_IMAGE_OVERRIDE
        if not image:
            digest = version.platform_digests.get(platform)
            if not digest:
                raise SandboxUnavailable(
                    f"the runtime profile has no image for this worker's platform ({platform})", retryable=False
                )
            image = f"{version.image_repository}@{digest}"
        return cls(
            image=image,
            platform=platform,
            memory_mb=version.memory_mb,
            cpus=version.cpus,
            pids_limit=version.pids_limit,
            tmp_mb=version.tmp_mb,
            work_mb=version.work_mb,
            output_mb=version.output_mb,
            env_mb=version.env_mb,
            labels={SANDBOX_LABEL: "1", **labels},
        )

    def tmpfs(self) -> dict[str, str]:
        """書ける場所はすべて大きさを決めたtmpfsにする（CONTRACT.md §2）。workとenvはスクリプトを実行できる"""
        owned = "uid=10001,gid=10001,mode=0700"
        return {
            "/tmp": f"size={self.tmp_mb}m,mode=1777",
            "/workspace/work": f"size={self.work_mb}m,{owned},exec",
            "/workspace/output": f"size={self.output_mb}m,{owned}",
            "/workspace/env": f"size={self.env_mb}m,{owned},exec",
        }

    def host_config(self) -> dict[str, Any]:
        """`create_host_config` に渡す制限。`--init` は付けない（イメージの init がPID 1。CONTRACT.md §2）"""
        memory = f"{self.memory_mb}m"
        return {
            "network_mode": "none",
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "pids_limit": self.pids_limit,
            "mem_limit": memory,
            "memswap_limit": memory,
            "nano_cpus": int(self.cpus * 1_000_000_000),
            "tmpfs": self.tmpfs(),
            "privileged": False,
        }


def verify_container(info: dict[str, Any], spec: SandboxSpec) -> list[str]:
    """起動したコンテナに契約の制限がかかっているかを、Dockerのinspectの結果で確かめる。違う点の一覧を返す"""
    host = info.get("HostConfig") or {}
    config = info.get("Config") or {}
    problems = []
    memory = spec.memory_mb * 1024 * 1024
    expected = {
        "NetworkMode": "none",
        "Privileged": False,
        "ReadonlyRootfs": True,
        "PidsLimit": spec.pids_limit,
        "Memory": memory,
        "MemorySwap": memory,
        "NanoCpus": int(spec.cpus * 1_000_000_000),
    }
    for key, value in expected.items():
        if host.get(key) != value:
            problems.append(f"HostConfig.{key}={host.get(key)!r} (expected {value!r})")
    if "ALL" not in (host.get("CapDrop") or []):
        problems.append("capabilities are not dropped")
    if host.get("CapAdd"):
        problems.append(f"capabilities are added: {host['CapAdd']}")
    security = host.get("SecurityOpt") or []
    if "no-new-privileges:true" not in security:
        problems.append("no-new-privileges is not set")
    if any(opt.endswith("=unconfined") for opt in security):
        problems.append(f"a security profile is disabled: {security}")
    for key in ("PidMode", "IpcMode", "UTSMode", "UsernsMode", "CgroupnsMode"):
        if host.get(key) == "host":
            problems.append(f"HostConfig.{key} is host")
    if host.get("Init"):
        problems.append("--init is set (the image's init must be PID 1)")
    if host.get("Binds") or host.get("Devices") or host.get("Mounts") or info.get("Mounts"):
        problems.append("host directories or devices are mounted")
    if (host.get("Tmpfs") or {}) != spec.tmpfs():
        problems.append(f"tmpfs mounts differ: {host.get('Tmpfs')}")
    if config.get("User") != SANDBOX_USER:
        problems.append(f"Config.User={config.get('User')!r}")
    return problems


@dataclass
class Exec:
    """Sandboxの中のコマンドの実行（Dockerのexec）。出力はWorker側のファイルへ書く（harness.md §8.3）"""

    exec_id: str
    # `yoriki-sandbox exec` の知らせ（started の pgid、exited）
    events: list[dict[str, Any]] = field(default_factory=list)
    started: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    error: str = ""
    # 出力の上限を超えて、残りを捨てた
    truncated: bool = False

    @property
    def pgid(self) -> int | None:
        return next((e["pgid"] for e in self.events if e.get("event") == "started"), None)


class Sandbox:
    def __init__(self, client: docker.DockerClient, container_id: str, name: str):
        self.client = client
        self.api = client.api
        self.container_id = container_id
        self.name = name

    # --- 補助コマンド（CONTRACT.md §4） ---

    def helper(self, command: str, request: dict[str, Any], *, timeout: int = HELPER_TIMEOUT) -> dict[str, Any]:
        """`{"ok": ...}` の結果を返す。0以外の終了コードや壊れた出力は、補助コマンドの不具合としてRunの障害にする"""
        stdout, stderr, exit_code = self._run([HELPER, command], json.dumps(request).encode(), timeout=timeout)
        if exit_code != 0:
            raise SandboxUnavailable(f"yoriki-sandbox {command} exited {exit_code}: {stderr[-2000:]!r}")
        try:
            result = json.loads(stdout)
        except ValueError as exc:
            raise SandboxUnavailable(f"yoriki-sandbox {command} returned invalid JSON: {stdout[:500]!r}") from exc
        if not isinstance(result, dict) or "ok" not in result:
            raise SandboxUnavailable(f"yoriki-sandbox {command} returned an unexpected value")
        return result

    def start_exec(self, request: dict[str, Any], output: IO[bytes], *, output_limit: int) -> Exec:
        """`yoriki-sandbox exec` を始め、出力を別のスレッドで `output` へ書く。終わりは `exec_state` で確かめる。

        Workerのディスクを埋めないよう、`output_limit` バイトを超えた出力は書かずに捨てる（コマンドは止めない）
        """
        exec_id = self._create_exec([HELPER, "exec"])
        execution = Exec(exec_id)

        def pump() -> None:
            pending = b""
            written = 0
            try:
                with self._attach(exec_id, json.dumps(request).encode(), timeout=None) as frames:
                    for stream, chunk in frames:
                        if stream == STDOUT:
                            if execution.truncated:
                                continue
                            room = output_limit - written
                            data = chunk
                            if len(data) > room:
                                data = data[: max(room, 0)] + OUTPUT_DISCARDED % output_limit
                                execution.truncated = True
                            output.write(data)
                            output.flush()
                            written += len(data)
                        elif stream == STDERR:
                            # 知らせは1行ずつのJSON。断片の境目で行が切れることがある
                            lines, _sep, pending = (pending + chunk).rpartition(b"\n")
                            execution.events += _events(lines)
                            if execution.pgid is not None:
                                execution.started.set()
            except (OSError, DockerException) as exc:
                execution.error = str(exc)
            finally:
                execution.events += _events(pending)
                output.close()
                execution.started.set()
                execution.done.set()

        threading.Thread(target=pump, name=f"sandbox-exec-{exec_id[:12]}", daemon=True).start()
        return execution

    def exec_state(self, exec_id: str) -> tuple[bool, int | None]:
        """（実行中か、終了コード）。終了と終了コードの正本はDockerのexecの状態（CONTRACT.md §4.1）"""
        try:
            info = self.api.exec_inspect(exec_id)
        except NotFound as exc:
            raise SandboxUnavailable("the sandbox was lost") from exc
        except DockerException as exc:
            raise SandboxUnavailable(f"cannot inspect the command: {exc}") from exc
        return bool(info.get("Running")), info.get("ExitCode")

    # --- Workspaceの取り出し・戻し（design.md §7.9） ---

    def archive(self, paths: list[str], dest: IO[bytes]) -> None:
        """`/workspace` の下の `paths` をtarにして `dest` へ書く。Sandboxの中のtarで作る
        （tmpfsはdocker cpで読めない）"""
        stdout_sink = dest.write
        _stdout, stderr, exit_code = self._run(
            ["tar", "--create", "--file=-", "--directory=/workspace", "--", *paths],
            b"",
            timeout=ARCHIVE_TIMEOUT,
            stdout_sink=stdout_sink,
        )
        if exit_code != 0:
            raise SandboxUnavailable(f"cannot archive the workspace (exit {exit_code}): {stderr[-1000:]!r}")

    def restore(self, src: IO[bytes]) -> None:
        """`archive` で作ったtarを `/workspace` の下へ戻す。Sandboxの利用者の権限で、書けるtmpfsの中にだけ展開される。
        GNU tarは絶対パスの先頭の `/` と `..` を含む名前を外して展開する"""
        _stdout, stderr, exit_code = self._run(
            ["tar", "--extract", "--file=-", "--directory=/workspace", "--no-same-owner", "--no-overwrite-dir"],
            src,
            timeout=ARCHIVE_TIMEOUT,
        )
        if exit_code != 0:
            raise SandboxUnavailable(f"cannot restore the workspace (exit {exit_code}): {stderr[-1000:]!r}")

    def remove(self) -> None:
        """`docker rm -f`（SIGKILL）。`docker stop` のSIGTERMは init が無視する（CONTRACT.md §2）"""
        try:
            self.api.remove_container(self.container_id, force=True)
        except NotFound:
            pass
        except DockerException:
            logger.exception("cannot remove sandbox %s", self.name)

    # --- Dockerのexec ---

    def _create_exec(self, command: list[str]) -> str:
        try:
            return self.api.exec_create(self.container_id, command, stdin=True, user=SANDBOX_USER)["Id"]
        except NotFound as exc:
            raise SandboxUnavailable("the sandbox was lost") from exc
        except DockerException as exc:
            raise SandboxUnavailable(f"cannot start a command in the sandbox: {exc}") from exc

    def _run(
        self,
        command: list[str],
        stdin: bytes | IO[bytes],
        *,
        timeout: int,
        stdout_sink: Callable[[bytes], Any] | None = None,
    ) -> tuple[bytes, bytes, int]:
        exec_id = self._create_exec(command)
        stdout, stderr = bytearray(), bytearray()
        try:
            with self._attach(exec_id, stdin, timeout=timeout) as frames:
                for stream, chunk in frames:
                    if stream == STDOUT:
                        if stdout_sink is None:
                            stdout += chunk
                        else:
                            stdout_sink(chunk)
                    elif stream == STDERR:
                        stderr += chunk
        except (OSError, DockerException) as exc:
            raise SandboxUnavailable(f"the command in the sandbox did not finish: {exc}") from exc
        running, exit_code = self.exec_state(exec_id)
        if running or exit_code is None:
            raise SandboxUnavailable("the command in the sandbox did not finish")
        return bytes(stdout), bytes(stderr), exit_code

    def _attach(self, exec_id: str, stdin: bytes | IO[bytes], *, timeout: int | None):
        """execを始め、標準入力を書いて閉じ、標準出力・標準エラーの断片を読む文脈を返す"""
        return _Attached(self.api, exec_id, stdin, timeout)


class _Attached:
    """execの接続（hijackしたソケット）。docker-pyの `exec_start(socket=True)` は応答のオブジェクトを返さず、
    閉じた後のGCで警告が出るので、同じ手順を内部のメソッドで行い、応答ごと閉じる"""

    def __init__(self, api, exec_id: str, stdin: bytes | IO[bytes], timeout: int | None):
        self.api = api
        self.exec_id = exec_id
        self.stdin = stdin
        self.timeout = timeout
        self.response: Any = None

    def __enter__(self) -> Iterator[tuple[int, bytes]]:
        self.response = self.api._post_json(
            self.api._url("/exec/{0}/start", self.exec_id),
            headers={"Connection": "Upgrade", "Upgrade": "tcp"},
            data={"Tty": False, "Detach": False},
            stream=True,
        )
        self.api._raise_for_status(self.response)
        sock = self.api._get_raw_response_socket(self.response)
        raw = getattr(sock, "_sock", sock)
        raw.settimeout(self.timeout)
        if isinstance(self.stdin, bytes):
            raw.sendall(self.stdin)
        else:
            while chunk := self.stdin.read(CHUNK):
                raw.sendall(chunk)
        raw.shutdown(socket.SHUT_WR)
        return frames_iter(sock, tty=False)

    def __exit__(self, *exc_info) -> None:
        if self.response is not None:
            self.response.close()


def _events(chunk: bytes) -> list[dict[str, Any]]:
    events = []
    for line in chunk.decode(errors="replace").splitlines():
        if line.startswith("{"):
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    return events


# userns-remapかrootlessでないことを、プロセスごとに1回だけ警告する
_isolation_checked = False


class Launcher:
    """Workerのdockerdで、Sandboxを契約の制限つきで起動する"""

    def __init__(self, client: docker.DockerClient | None = None):
        try:
            self.client = client or docker.from_env()
        except DockerException as exc:
            raise SandboxUnavailable(f"cannot connect to Docker: {exc}") from exc
        self.api = self.client.api

    def platform(self) -> str:
        """Workerのplatform（`linux/amd64` 等）。Runtime Profileのplatformごとのdigestから選ぶのに使う"""
        try:
            version = self.api.version()
        except DockerException as exc:
            raise SandboxUnavailable(f"cannot connect to Docker: {exc}") from exc
        return f"{version['Os']}/{version['Arch']}"

    def check_isolation(self) -> None:
        """コンテナの中のrootがホストのrootにならない構成か（design.md §7.2の対策の4、deployment.md §7.1）。
        起動は止めずに警告する"""
        global _isolation_checked  # noqa: PLW0603  プロセスごとに1回だけ確かめる
        if _isolation_checked:
            return
        _isolation_checked = True
        try:
            options = self.api.info().get("SecurityOptions") or []
        except DockerException:
            return
        if not any("name=userns" in o or "name=rootless" in o for o in options):
            logger.warning("Docker runs without userns-remap or rootless mode; see docs/deployment.md §7.1")

    def start(self, spec: SandboxSpec, name: str) -> Sandbox:
        self.check_isolation()
        try:
            self._ensure_image(spec.image)
            self._check_contract(spec.image)
            self._remove_by_name(name)
            container = self.api.create_container(
                spec.image,
                name=name,
                user=SANDBOX_USER,
                labels=spec.labels,
                host_config=self.api.create_host_config(**spec.host_config()),
                network_disabled=True,
            )
            container_id = container["Id"]
            sandbox = Sandbox(self.client, container_id, name)
            problems = verify_container(self.api.inspect_container(container_id), spec)
            if problems:
                sandbox.remove()
                logger.error("sandbox %s: restrictions are not applied: %s", name, problems)
                raise SandboxUnavailable("the sandbox restrictions could not be applied", retryable=False)
            self.api.start(container_id)
        except DockerException as exc:
            raise SandboxUnavailable(f"cannot start the sandbox: {exc}") from exc
        result = sandbox.helper("init-env", {})
        if not result.get("ok"):
            sandbox.remove()
            raise SandboxUnavailable(f"cannot prepare the sandbox: {result.get('error')}")
        return sandbox

    def containers(self) -> list[dict[str, Any]]:
        """Launcherが起動したコンテナ（止まったものも含む）"""
        try:
            return self.api.containers(all=True, filters={"label": f"{SANDBOX_LABEL}=1"})
        except DockerException:
            logger.exception("cannot list sandboxes")
            return []

    def remove(self, container_id: str) -> None:
        Sandbox(self.client, container_id, container_id).remove()

    def _ensure_image(self, image: str) -> None:
        try:
            self.api.inspect_image(image)
            return
        except ImageNotFound:
            pass
        if settings.YORIKI_SANDBOX_IMAGE_OVERRIDE:
            raise SandboxUnavailable(f"the override image {image} is not built on this worker", retryable=False)
        repository, digest = image.split("@", 1)
        logger.info("pulling sandbox image %s", image)
        self.api.pull(repository, tag=digest)

    def _check_contract(self, image: str) -> None:
        """登録のときに確かめた契約番号を、Workerのイメージでも確かめる（差し替えたイメージも同じ）"""
        labels = (self.api.inspect_image(image).get("Config") or {}).get("Labels") or {}
        contract = labels.get(CONTRACT_LABEL, "")
        if not contract.isdigit() or int(contract) not in SUPPORTED_CONTRACTS:
            raise SandboxUnavailable(f"the image has an unsupported sandbox contract: {contract!r}", retryable=False)

    def _remove_by_name(self, name: str) -> None:
        with contextlib.suppress(NotFound):
            self.api.remove_container(name, force=True)
