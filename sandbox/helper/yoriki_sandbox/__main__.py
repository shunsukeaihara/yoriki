"""`yoriki-sandbox <サブコマンド>`。引数は標準入力のJSON、結果は標準出力のJSON（sandbox/CONTRACT.md）"""

import sys
from collections.abc import Callable
from typing import Any

from .common import ToolError, read_request, respond
from .execute import run_exec, run_kill
from .read import run_read
from .search import run_glob, run_grep
from .text import run_edit, run_write
from .workspace import run_init_env, run_stat, run_version

COMMANDS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "read": run_read,
    "write": run_write,
    "edit": run_edit,
    "glob": run_glob,
    "grep": run_grep,
    "stat": run_stat,
    "init-env": run_init_env,
    "version": run_version,
}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in (*COMMANDS, "exec", "kill"):
        sys.stderr.write(f"usage: yoriki-sandbox {{{','.join((*COMMANDS, 'exec', 'kill'))}}} < request.json\n")
        return 2
    command = argv[0]
    try:
        # exec はコマンドの終了コードで終わり、結果のJSONは返さない（出力はコマンドのもの）
        if command == "exec":
            return run_exec()
        if command == "kill":
            respond(run_kill())
            return 0
        respond(COMMANDS[command](read_request()))
    except ToolError as exc:
        error: dict[str, Any] = {"code": exc.code, "message": exc.message}
        if exc.detail:
            error["detail"] = exc.detail
        respond({"ok": False, "error": error})
        # exec が始まる前に断った場合は、コマンドと区別できるよう 125 で終わる（docker run の慣習）
        return 125 if command == "exec" else 0
    return 0


sys.exit(main(sys.argv[1:]))
