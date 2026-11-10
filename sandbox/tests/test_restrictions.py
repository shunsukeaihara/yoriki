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


# 開いたときに /workspace/work/macro-ran を作る、文書に埋め込んだマクロ
MACRO_DOCUMENT = """<?xml version="1.0" encoding="UTF-8"?>
<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
  xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
  xmlns:script="urn:oasis:names:tc:opendocument:xmlns:script:1.0" xmlns:ooo="http://openoffice.org/2004/office"
  xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:dom="http://www.w3.org/2001/xml-events"
  office:version="1.3" office:mimetype="application/vnd.oasis.opendocument.text">
 <office:scripts>
  <office:script script:language="ooo:Basic"><ooo:libraries><ooo:library-embedded ooo:name="Standard">
   <ooo:module ooo:name="Module1"><ooo:source-code>Sub Main
  Open "/workspace/work/macro-ran" For Output As #1
  Close #1
End Sub
</ooo:source-code></ooo:module>
  </ooo:library-embedded></ooo:libraries></office:script>
  <office:event-listeners>
   <script:event-listener script:language="ooo:script" script:event-name="dom:load" xlink:type="simple"
     xlink:href="vnd.sun.star.script:Standard.Module1.Main?language=Basic&amp;location=document"/>
  </office:event-listeners>
 </office:scripts>
 <office:body><office:text><text:p>macro</text:p></office:text></office:body>
</office:document>
"""

# ユーザーのプロファイルでマクロのセキュリティを最低にする設定
LOW_MACRO_SECURITY = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting">
<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>0</value></prop></item>
</oor:items>
"""


def test_document_macros_do_not_run(sandbox):
    """信頼しない文書のマクロは、プロファイルでセキュリティを下げても動かない（design.md §7.6）。
    イメージの設定（yoriki.xcd）がなければ、この手順で開くとマクロが動くことを確かめてある"""
    profile = "/tmp/lo-macro-test"
    sandbox.sh(
        f"mkdir -p {profile}/user && cat > {profile}/user/registrymodifications.xcu <<'XML'\n{LOW_MACRO_SECURITY}XML\n"
        f"cat > macro.fodt <<'XML'\n{MACRO_DOCUMENT}XML"
    )
    # 開くだけ（変換しない）だとLibreOfficeは終わらないので、時間で切る
    sandbox.exec(
        f"timeout 20 soffice -env:UserInstallation=file://{profile} --headless --norestore macro.fodt;"
        " pkill -f soffice.bin; true",
        timeout=60,
    )
    assert sandbox.exec("test -e macro-ran").exit_code != 0


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
    assert labels["org.yoriki.sandbox-contract"] == "3"
    config = json.loads(
        subprocess.run(
            ["docker", "image", "inspect", "--format", "{{json .Config}}", IMAGE],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert config["User"] == "10001:10001"
