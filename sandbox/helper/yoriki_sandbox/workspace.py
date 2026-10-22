"""ファイルの情報と、Run専用のPythonの環境の用意（design.md §7.6）"""

import shutil
import sys
import time
from pathlib import Path
from typing import Any

from . import CONTRACT
from .common import ToolError, resolve, sha256_of
from .read import SUFFIX_KIND

# イメージの中の環境の雛形。/workspace/env と同じパスで作ってあるので、そのままコピーすれば動く
ENV_TEMPLATE = Path("/opt/yoriki/env")
ENV = Path("/workspace/env")


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
    """空の /workspace/env に、イメージの雛形をコピーする。すでにあれば何もしない"""
    if (ENV / "bin" / "python").exists():
        return {"ok": True, "created": False}
    if not ENV.is_dir():
        raise ToolError("not_found", f"{ENV} is not mounted.")
    started = time.monotonic()
    for item in ENV_TEMPLATE.iterdir():
        target = ENV / item.name
        if item.is_dir() and not item.is_symlink():
            shutil.copytree(item, target, symlinks=True)
        else:
            shutil.copy2(item, target, follow_symlinks=False)
    return {"ok": True, "created": True, "seconds": round(time.monotonic() - started, 3)}


def run_version(_request: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "contract": CONTRACT, "python": sys.version.split()[0]}
