"""ファイルの情報と、Run専用のPythonの環境の用意（design.md §7.6）"""

import sys
import time
import venv
from pathlib import Path
from typing import Any

from . import CONTRACT
from .common import ToolError, resolve, sha256_of
from .read import SUFFIX_KIND

ENV = Path("/workspace/env")

# 仮想環境に pip を入れない代わりに置く。Sandboxにはネットワークがないので、エージェントが呼ぶ pip は、
# 入っているpackageを確かめる操作（list・show・freeze 等）だけを通し、入れる操作は install_python_packages へ
# 案内する。追加のpackageは、Runnerが `python -m pip` で /packages から入れる（design.md §7.6）。
# 権限の境界ではない（境界はネットワークとrootfs。エージェントは自分でファイルを書いてimportできる）
PIP_SCRIPT = """#!/workspace/env/bin/python
import sys

BLOCKED = {"install", "download", "wheel", "uninstall"}
command = next((a for a in sys.argv[1:] if not a.startswith("-")), None)
if command in BLOCKED:
    sys.stderr.write(
        "pip cannot install packages here: the sandbox has no network.\\n"
        "Use the install_python_packages tool to add packages.\\n"
    )
    sys.exit(1)
from pip._internal.cli.main import main
sys.exit(main())
"""


def run_stat(request: dict[str, Any]) -> dict[str, Any]:
    path = resolve(request.get("path"), must_exist=False)
    if not path.exists():
        return {"ok": True, "path": str(path), "exists": False}
    stat = path.stat()
    result: dict[str, Any] = {
        "ok": True,
        "path": str(path),
        "exists": True,
        "type": "directory" if path.is_dir() else "file" if path.is_file() else "other",
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }
    if path.is_file():
        result["sha256"] = sha256_of(path)
        result["kind"] = SUFFIX_KIND.get(path.suffix.lower(), "text")
    return result


def run_init_env(_request: dict[str, Any]) -> dict[str, Any]:
    """空の /workspace/env に、イメージのPythonの上の薄い仮想環境を作る。すでにあれば何もしない（design.md §7.6）"""
    if (ENV / "bin" / "python").exists():
        return {"ok": True, "created": False}
    if not ENV.is_dir():
        raise ToolError("not_found", f"{ENV} is not mounted.")
    started = time.monotonic()
    # イメージの頻出ライブラリはそのまま見え、追加したpackageはこの環境に入って先に読まれる
    venv.EnvBuilder(system_site_packages=True, symlinks=True, with_pip=False).create(ENV)
    for name in ("pip", "pip3"):
        script = ENV / "bin" / name
        script.write_text(PIP_SCRIPT)
        script.chmod(0o755)
    return {"ok": True, "created": True, "seconds": round(time.monotonic() - started, 3)}


def run_version(_request: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "contract": CONTRACT, "python": sys.version.split()[0]}
