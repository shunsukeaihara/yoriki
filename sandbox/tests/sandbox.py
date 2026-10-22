"""契約テストで使う、制限付きのSandbox（sandbox/CONTRACT.md §2）。Launcher（api の runtime）と同じ制限で起動する"""

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# CONTRACT.md §2 の制限。Launcherはこれより緩めない（ネットワーク、rootfs、uid、capability、資源）
RUN_OPTIONS = [
    "--network=none",
    "--user=10001:10001",
    "--read-only",
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges:true",
    "--pids-limit=256",
    "--memory=2g",
    "--memory-swap=2g",
    "--cpus=2",
    "--tmpfs=/tmp:size=256m,mode=1777",
    "--tmpfs=/workspace/work:size=512m,uid=10001,gid=10001,mode=0700,exec",
    "--tmpfs=/workspace/output:size=256m,uid=10001,gid=10001,mode=0700",
    "--tmpfs=/workspace/env:size=1g,uid=10001,gid=10001,mode=0700,exec",
]

# 呼出しごとに渡す環境変数（harness.md §8.2）
COMMAND_ENV = {
    "HOME": "/workspace/work/.home",
    "PATH": "/workspace/env/bin:/opt/yoriki/bin:/usr/local/bin:/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "TZ": "Asia/Tokyo",
    "PAGER": "cat",
    "GIT_PAGER": "cat",
    "TERM": "dumb",
    "NO_COLOR": "1",
    "PYTHONUNBUFFERED": "1",
    "MPLBACKEND": "Agg",
    "MPLCONFIGDIR": "/workspace/work/.cache/matplotlib",
    "XDG_CACHE_HOME": "/workspace/work/.cache",
    "PIP_CACHE_DIR": "/workspace/work/.cache/pip",
}


@dataclass
class ExecResult:
    exit_code: int
    output: str
    events: list[dict[str, Any]] = field(default_factory=list)


class Sandbox:
    def __init__(self, image: str, name: str, input_dir: Path):
        self.image = image
        self.name = name
        self.input_dir = input_dir

    def start(self) -> None:
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                self.name,
                *RUN_OPTIONS,
                f"--mount=type=bind,src={self.input_dir},dst=/workspace/input,readonly",
                self.image,
            ],
            check=True,
            capture_output=True,
        )
        assert self.helper("init-env", {})["ok"]

    def stop(self) -> None:
        subprocess.run(["docker", "rm", "-f", self.name], check=False, capture_output=True)

    def helper(self, command: str, request: dict[str, Any], timeout: int = 120) -> dict[str, Any]:
        proc = subprocess.run(
            ["docker", "exec", "-i", self.name, "/opt/yoriki/bin/yoriki-sandbox", command],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    def exec(self, command: str, cwd: str = "/workspace/work", timeout: int = 120, **env: str) -> ExecResult:
        """Launcherと同じく、`yoriki-sandbox exec` でコマンドを実行する"""
        request = {"command": command, "cwd": cwd, "env": COMMAND_ENV | env}
        proc = subprocess.run(
            ["docker", "exec", "-i", self.name, "/opt/yoriki/bin/yoriki-sandbox", "exec"],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        events = [json.loads(line) for line in proc.stderr.splitlines() if line.startswith("{")]
        return ExecResult(proc.returncode, proc.stdout, events)

    def sh(self, command: str, **env: str) -> str:
        """終了コードが0であることを確かめて、出力を返す"""
        result = self.exec(command, **env)
        assert result.exit_code == 0, f"{command!r} exited {result.exit_code}:\n{result.output}"
        return result.output
