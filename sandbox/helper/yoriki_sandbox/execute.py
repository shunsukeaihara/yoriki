"""コマンドを独立したプロセスグループで実行する（harness.md §8.1〜§8.3）。

標準入力のJSONを読んでから標準入力を /dev/null にし、`bash -c` を新しいsessionで始める。コマンドの標準出力と
標準エラーは、どちらもこのプロセスの標準出力へ流す。このコマンド自身の知らせ（始まり・終わり）は、標準エラーへ
1行ずつJSONで書く。終了コードの正本は、LauncherがDockerのexecの状態から読む。
"""

import json
import os
import signal
import sys
from typing import Any

from .common import ToolError, read_request, resolve

SIGNALS = {"INT": signal.SIGINT, "TERM": signal.SIGTERM, "KILL": signal.SIGKILL}


def _notify(event: dict[str, Any]) -> None:
    sys.stderr.write(json.dumps(event) + "\n")
    sys.stderr.flush()


def run_exec() -> int:
    request = read_request()
    command = request.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ToolError("invalid_argument", "command must be a non-empty string.")
    cwd = resolve(request.get("cwd") or "/workspace/work")
    if not cwd.is_dir():
        raise ToolError("not_a_directory", f"{cwd} is not a directory.")
    env = request.get("env") or {}
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise ToolError("invalid_argument", "env must be an object of strings.")

    pid = os.fork()
    if pid == 0:  # 子：新しいsessionでbashを始める
        try:
            os.setsid()
            devnull = os.open(os.devnull, os.O_RDONLY)
            os.dup2(devnull, 0)
            os.dup2(1, 2)
            os.chdir(cwd)
            os.execve("/bin/bash", ["bash", "-c", command], env)
        finally:
            os._exit(127)
    _notify({"event": "started", "pgid": pid})
    # 親はシグナルで止められても、子のグループは残る（止めるのは kill でグループへ送る）
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, signal.SIG_IGN)
    _, status = os.waitpid(pid, 0)
    code = os.waitstatus_to_exitcode(status)
    exit_code = code if code >= 0 else 128 - code
    _notify({"event": "exited", "exit_code": exit_code, "signal": -code if code < 0 else None})
    return exit_code


def run_kill() -> dict[str, Any]:
    request = read_request()
    pgid = request.get("pgid")
    name = request.get("signal", "TERM")
    if not isinstance(pgid, int) or pgid <= 1:
        raise ToolError("invalid_argument", "pgid must be an integer greater than 1.")
    if name not in SIGNALS:
        raise ToolError("invalid_argument", f"signal must be one of {', '.join(SIGNALS)}.")
    try:
        os.killpg(pgid, SIGNALS[name])
    except ProcessLookupError:
        return {"ok": True, "killed": False}
    return {"ok": True, "killed": True}
