"""Launcherのテスト。制限の組立と確かめ直しは単体で、起動・コマンド・Workspaceの取り出しは実際のDockerで確かめる
（design.md §7.2の対策の1）"""

import io
import time
import uuid
from decimal import Decimal
from typing import Any, cast

import pytest

from runtime.factories import RuntimeProfileFactory
from runtime.launcher import Launcher, Sandbox, SandboxSpec, SandboxUnavailable, verify_container


def spec(**overrides: Any) -> SandboxSpec:
    values: dict[str, Any] = {
        "image": "img",
        "platform": "linux/arm64",
        "memory_mb": 2048,
        "cpus": Decimal("2.00"),
        "pids_limit": 256,
        "tmp_mb": 256,
        "work_mb": 512,
        "output_mb": 256,
        "env_mb": 512,
    }
    return SandboxSpec(**(values | overrides))


def good_inspect(s: SandboxSpec) -> dict:
    """契約どおりに起動したコンテナの inspect（必要な項目だけ）"""
    memory = s.memory_mb * 1024 * 1024
    return {
        "Config": {"User": "10001:10001"},
        "HostConfig": {
            "Runtime": s.runtime or "runc",
            "NetworkMode": "none",
            "Privileged": False,
            "ReadonlyRootfs": True,
            "PidsLimit": s.host_pids_limit,
            "Memory": memory,
            "MemorySwap": memory,
            "NanoCpus": 2_000_000_000,
            "CapDrop": ["ALL"],
            "CapAdd": None,
            "SecurityOpt": ["no-new-privileges:true"],
            "Tmpfs": s.tmpfs(),
            "Binds": None,
            "Devices": [],
            "Init": None,
            "PidMode": "",
        },
        "Mounts": (
            [{"Type": "bind", "Source": s.input_dir, "Destination": "/workspace/input", "RW": False}]
            if s.input_dir
            else []
        ),
    }


class TestSpec:
    @pytest.mark.django_db
    def test_uses_the_digest_for_the_worker_platform(self, settings):
        settings.YORIKI_SANDBOX_RUNTIME = "runsc"
        version = RuntimeProfileFactory.create().current_version
        assert version is not None
        s = SandboxSpec.for_version(version, "linux/arm64", labels={"org.yoriki.run": "r1"})
        assert s.image == f"{version.image_repository}@{version.platform_digests['linux/arm64']}"
        assert s.labels == {"org.yoriki.sandbox": "1", "org.yoriki.run": "r1"}
        assert s.runtime == "runsc"

    @pytest.mark.django_db
    def test_refuses_an_unsupported_platform(self):
        version = RuntimeProfileFactory.create(
            version__platforms=["linux/amd64"], version__platform_digests={"linux/amd64": "sha256:" + "b" * 64}
        ).current_version
        with pytest.raises(SandboxUnavailable) as exc:
            SandboxSpec.for_version(version, "linux/arm64", labels={})
        assert not exc.value.retryable

    @pytest.mark.django_db
    def test_override_only_replaces_the_image(self, settings):
        settings.YORIKI_SANDBOX_IMAGE_OVERRIDE = "yoriki-sandbox-docs:dev"
        version = RuntimeProfileFactory.create(version__memory_mb=1024).current_version
        s = SandboxSpec.for_version(version, "linux/riscv64", labels={})
        assert s.image == "yoriki-sandbox-docs:dev"
        assert s.memory_mb == 1024

    def test_host_config_follows_the_contract(self):
        """sandbox/CONTRACT.md §2 の制限"""
        config = spec().host_config()
        assert config["network_mode"] == "none"
        assert config["read_only"] is True
        assert config["cap_drop"] == ["ALL"]
        assert config["security_opt"] == ["no-new-privileges:true"]
        assert config["privileged"] is False
        assert config["mem_limit"] == config["memswap_limit"] == "2048m"
        assert config["pids_limit"] == 256
        assert "init" not in config
        assert set(config["tmpfs"]) == {"/tmp", "/workspace/work", "/workspace/output", "/workspace/env"}
        assert "exec" in config["tmpfs"]["/workspace/work"]
        assert "exec" not in config["tmpfs"]["/workspace/output"]
        assert "runtime" not in config

    def test_gvisor_converts_the_process_limit(self):
        """gVisorでは、Sandboxの中のプロセスごとにホストのプロセスが増えるので、Dockerの上限を換算する"""
        config = spec(runtime="runsc").host_config()
        assert config["runtime"] == "runsc"
        assert config["pids_limit"] == 256 * 2 + 64
        assert spec(runtime="runsc-debug").gvisor
        assert not spec(runtime="runc").gvisor
        assert spec(runtime="runc").host_config()["pids_limit"] == 256


class TestVerify:
    def test_accepts_the_contract(self):
        s = spec()
        assert verify_container(good_inspect(s), s) == []

    @pytest.mark.parametrize(
        ("section", "key", "value"),
        [
            ("HostConfig", "Privileged", True),
            ("HostConfig", "NetworkMode", "bridge"),
            ("HostConfig", "ReadonlyRootfs", False),
            ("HostConfig", "CapDrop", []),
            ("HostConfig", "CapAdd", ["SYS_ADMIN"]),
            ("HostConfig", "SecurityOpt", ["no-new-privileges:true", "seccomp=unconfined"]),
            ("HostConfig", "SecurityOpt", []),
            ("HostConfig", "PidMode", "host"),
            ("HostConfig", "Init", True),
            ("HostConfig", "Binds", ["/var/run/docker.sock:/var/run/docker.sock"]),
            ("HostConfig", "Devices", [{"PathOnHost": "/dev/kvm"}]),
            ("HostConfig", "Memory", 0),
            ("HostConfig", "MemorySwap", -1),
            ("HostConfig", "PidsLimit", None),
            ("HostConfig", "Tmpfs", {"/tmp": "size=256m"}),
            ("Config", "User", "root"),
        ],
    )
    def test_rejects_a_weaker_setting(self, section, key, value):
        s = spec()
        info = good_inspect(s)
        info[section][key] = value
        assert verify_container(info, s)

    def test_rejects_another_runtime(self):
        s = spec(runtime="runsc")
        assert verify_container(good_inspect(s), s) == []
        info = good_inspect(s)
        info["HostConfig"]["Runtime"] = "runc"
        assert verify_container(info, s)

    def test_rejects_host_mounts(self):
        s = spec()
        info = good_inspect(s)
        info["Mounts"] = [{"Type": "bind", "Source": "/", "Destination": "/host"}]
        assert verify_container(info, s)

    def test_attachments_are_mounted_read_only(self):
        s = spec(input_dir="/var/lib/yoriki/workspaces/attempts/r-1/input")
        [mount] = s.host_config()["mounts"]
        assert (mount["Target"], mount["Source"], mount["ReadOnly"]) == ("/workspace/input", s.input_dir, True)
        assert verify_container(good_inspect(s), s) == []
        writable = good_inspect(s)
        writable["Mounts"][0]["RW"] = True
        assert verify_container(writable, s)
        extra = good_inspect(s)
        extra["Mounts"].append({"Type": "bind", "Source": "/", "Destination": "/workspace/master", "RW": False})
        assert verify_container(extra, s)


@pytest.fixture(name="sandbox")
def fixture_sandbox(sandbox_image, settings):
    launcher = Launcher()
    s = spec(
        image=sandbox_image,
        platform=launcher.platform(),
        runtime=settings.YORIKI_SANDBOX_RUNTIME,
        labels={"org.yoriki.sandbox": "1"},
    )
    sandbox = launcher.start(s, f"yoriki-test-{uuid.uuid4().hex[:12]}")
    yield sandbox
    sandbox.remove()


@pytest.mark.sandbox
class TestSandbox:
    def test_restrictions_are_applied(self, sandbox, settings):
        info = sandbox.api.inspect_container(sandbox.container_id)
        s = spec(image=info["Config"]["Image"], runtime=settings.YORIKI_SANDBOX_RUNTIME)
        assert verify_container(info, s) == []
        assert sandbox.helper("version", {}) | {"python": ""} == {"ok": True, "contract": 1, "python": ""}

    def test_exec_writes_the_output_and_reports_the_exit_code(self, sandbox, tmp_path):
        path = tmp_path / "out.log"
        request = {"command": "echo out; echo err >&2; exit 3", "cwd": "/workspace/work", "env": {"PATH": "/bin"}}
        execution = sandbox.start_exec(request, path.open("wb"), output_limit=1024)
        assert execution.done.wait(timeout=30)
        assert execution.pgid is not None
        assert sandbox.exec_state(execution.exec_id) == (False, 3)
        assert path.read_text() == "out\nerr\n"

    def test_output_beyond_the_limit_is_discarded(self, sandbox, tmp_path):
        path = tmp_path / "out.log"
        request = {"command": "seq 1 100000", "cwd": "/workspace/work", "env": {"PATH": "/bin:/usr/bin"}}
        execution = sandbox.start_exec(request, path.open("wb"), output_limit=100)
        assert execution.done.wait(timeout=30)
        assert execution.truncated
        assert sandbox.exec_state(execution.exec_id) == (False, 0)
        data = path.read_bytes()
        assert data.startswith(b"1\n2\n") and b"[output beyond 100 bytes was discarded" in data
        assert len(data) < 200

    def test_kill_stops_a_running_command(self, sandbox, tmp_path):
        request = {"command": "sleep 60", "cwd": "/workspace/work", "env": {"PATH": "/bin:/usr/bin"}}
        execution = sandbox.start_exec(request, (tmp_path / "out.log").open("wb"), output_limit=1024)
        assert execution.started.wait(timeout=10)
        assert sandbox.exec_state(execution.exec_id)[0] is True
        assert sandbox.helper("kill", {"pgid": execution.pgid, "signal": "TERM"}) == {"ok": True, "killed": True}
        assert execution.done.wait(timeout=10)
        assert sandbox.exec_state(execution.exec_id) == (False, 128 + 15)

    def test_archive_and_restore(self, sandbox):
        command = "mkdir -p sub && printf 'é\\n' > sub/a.txt && echo r > /workspace/output/report.csv"
        request = {"command": command, "cwd": "/workspace/work", "env": {"PATH": "/bin:/usr/bin"}}
        execution = sandbox.start_exec(request, io.BytesIO(), output_limit=1024)
        assert execution.done.wait(timeout=30)
        archive = io.BytesIO()
        sandbox.archive(["work", "output"], archive)

        launcher = Launcher()
        image = sandbox.api.inspect_container(sandbox.container_id)["Config"]["Image"]
        other = launcher.start(spec(image=image, platform=launcher.platform()), f"yoriki-test-{uuid.uuid4().hex[:12]}")
        try:
            archive.seek(0)
            other.restore(archive)
            assert other.helper("read", {"path": "/workspace/work/sub/a.txt"})["text"].endswith("é")
            assert other.helper("stat", {"path": "/workspace/output/report.csv"})["exists"] is True
        finally:
            other.remove()

    def test_remove_kills_the_sandbox(self, sandbox):
        sandbox.remove()
        time.sleep(0.2)
        with pytest.raises(SandboxUnavailable):
            sandbox.helper("version", {})


@pytest.mark.sandbox
def test_failed_startup_leaves_no_container(sandbox_image, monkeypatch):
    launcher = Launcher()
    name = f"yoriki-test-{uuid.uuid4().hex[:12]}"

    def broken(self, command, request, **kwargs):
        raise SandboxUnavailable("init-env failed")

    monkeypatch.setattr("runtime.launcher.Sandbox.helper", broken)
    with pytest.raises(SandboxUnavailable):
        launcher.start(spec(image=sandbox_image, platform=launcher.platform()), name)
    assert launcher.api.containers(all=True, filters={"name": name}) == []


def test_refuses_a_runtime_missing_on_the_worker():
    """登録されていない実行環境なら、Dockerの既定で動かさずに断る"""

    class Api:
        def info(self):
            return {"Runtimes": {"runc": {}}, "SecurityOptions": ["name=userns"]}

    launcher = Launcher.__new__(Launcher)
    launcher.api = cast(Any, Api())
    with pytest.raises(SandboxUnavailable) as exc:
        launcher.start(spec(runtime="runsc"), "yoriki-test")
    assert not exc.value.retryable


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"OOMKilled": True, "Running": False}, True),
        # gVisorで超えたコマンドだけが止まったとき・runc。Sandboxは使い続けられる
        ({"OOMKilled": True, "Running": True}, False),
        ({"OOMKilled": False, "Running": False}, False),
    ],
)
def test_out_of_memory(state, expected):
    class Api:
        def inspect_container(self, container_id):
            return {"State": state}

    class Client:
        api = Api()

    assert Sandbox(cast(Any, Client()), "c1", "yoriki-test").out_of_memory() is expected
