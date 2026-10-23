"""Sandboxの制限が効いていることを確かめる（design.md §7.2、CONTRACT.md §2）"""

import json
import subprocess
import uuid

from .conftest import IMAGE
from .sandbox import GVISOR, HOST_PIDS_LIMIT, Sandbox


def test_runs_as_the_sandbox_user(sandbox):
    assert sandbox.sh("id -u; id -g").split() == ["10001", "10001"]


def test_has_no_network(sandbox):
    # 経路がない（ネットワークの名前空間に lo しかつながっていない）
    assert len(sandbox.sh("cat /proc/net/route").splitlines()) == 1
    result = sandbox.exec(
        "python -c \"import socket; socket.create_connection(('1.1.1.1', 53), timeout=3)\"",
    )
    assert result.exit_code != 0
    assert "Network is unreachable" in result.output or "OSError" in result.output


def test_rootfs_and_input_are_read_only(sandbox):
    for path in ("/usr/local/bin/x", "/etc/x", "/opt/yoriki/bin/x", "/workspace/input/x", "/workspace/x"):
        result = sandbox.exec(f"touch {path}")
        assert result.exit_code != 0, path
        assert "Read-only file system" in result.output or "Permission denied" in result.output


def test_writable_places(sandbox):
    sandbox.sh("touch /tmp/a /workspace/work/a /workspace/output/a /workspace/env/a")


def test_has_no_capabilities_and_cannot_gain_privileges(sandbox):
    status = sandbox.sh("cat /proc/self/status")
    fields = dict(line.split(":\t", 1) for line in status.splitlines() if ":\t" in line)
    assert fields["CapEff"].strip() == "0000000000000000"
    assert fields["CapBnd"].strip() == "0000000000000000"
    assert fields["NoNewPrivs"].strip() == "1"
    assert sandbox.exec("command -v sudo").exit_code != 0
    # パッケージは入れられない（rootでなく、ネットワークもない）
    assert sandbox.exec("apt-get install -y cowsay").exit_code != 0


def test_resource_limits(sandbox):
    host = json.loads(sandbox.inspect("{{json .HostConfig}}"))
    assert (host["PidsLimit"], host["Memory"], host["NanoCpus"]) == (HOST_PIDS_LIMIT, 2 * 1024**3, 2 * 10**9)
    if GVISOR:
        # gVisorでは、中からcgroupは見えず、CPUの数とメモリの量が上限のとおりに見える
        assert sandbox.sh("nproc").strip() == "2"
        assert sandbox.sh("head -1 /proc/meminfo").split()[1] == str(2 * 1024**2)
    else:
        assert sandbox.sh("cat /sys/fs/cgroup/pids.max").strip() == str(HOST_PIDS_LIMIT)
        assert sandbox.sh("cat /sys/fs/cgroup/memory.max").strip() == str(2 * 1024**3)
        assert sandbox.sh("cat /sys/fs/cgroup/cpu.max").split()[0] == "200000"


def test_memory_limit(tmp_path):
    """メモリの上限を超えたコマンドは止まり、DockerはOOMを記録する（Launcherはこれでモデルに知らせる）。
    runcではそのコマンドだけが止まる。gVisorでは、ホストのOOM killerがgVisor自体を選ぶと、Sandboxごと止まる"""
    box = Sandbox(IMAGE, f"yoriki-sandbox-test-{uuid.uuid4().hex[:8]}", tmp_path)
    box.start()
    try:
        result = box.exec("python -c 'b = [bytearray(256 * 1024**2) for _ in range(12)]'")
        assert result.exit_code != 0
        assert box.inspect("{{.State.OOMKilled}}") == "true"
        if not GVISOR:
            assert box.sh("echo alive").strip() == "alive"
    finally:
        box.stop()


def test_docker_socket_is_not_visible(sandbox):
    assert sandbox.exec("test -e /var/run/docker.sock").exit_code != 0


def test_image_declares_the_contract():
    labels = json.loads(
        subprocess.run(
            ["docker", "image", "inspect", "--format", "{{json .Config.Labels}}", IMAGE],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert labels["org.yoriki.sandbox-contract"] == "1"
    config = json.loads(
        subprocess.run(
            ["docker", "image", "inspect", "--format", "{{json .Config}}", IMAGE],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert config["User"] == "10001:10001"
