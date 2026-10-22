"""ファイルを探す（harness.md §7.8 `glob`・`grep`）"""

import json
import subprocess
from pathlib import Path
from typing import Any

from .common import ToolError, resolve

MAX_GLOB = 100
MAX_GREP = 250
GREP_TIMEOUT = 60


def run_glob(request: dict[str, Any]) -> dict[str, Any]:
    pattern = request.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        raise ToolError("invalid_argument", 'pattern must be a non-empty string such as "**/*.csv".')
    if pattern.startswith("/") or ".." in Path(pattern).parts:
        raise ToolError("invalid_argument", "pattern must be relative to path and must not contain '..'.")
    base = resolve(request.get("path") or "/workspace")
    if not base.is_dir():
        raise ToolError("not_a_directory", f"{base} is not a directory.")
    found = []
    for item in base.glob(pattern):
        try:
            stat = item.lstat()
        except OSError:
            continue
        kind = "symlink" if item.is_symlink() else "directory" if item.is_dir() else "file"
        found.append((stat.st_mtime, str(item), kind, stat.st_size))
    found.sort(key=lambda x: (-x[0], x[1]))
    entries = [{"path": p, "type": k, "size": s} for _, p, k, s in found[:MAX_GLOB]]
    return {"ok": True, "total": len(found), "truncated": len(found) > MAX_GLOB, "entries": entries}


def run_grep(request: dict[str, Any]) -> dict[str, Any]:
    pattern = request.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        raise ToolError("invalid_argument", "pattern must be a non-empty regular expression.")
    output = request.get("output", "files")
    if output not in ("files", "lines", "count"):
        raise ToolError("invalid_argument", "output must be one of files, lines, count.")
    base = resolve(request.get("path") or "/workspace")
    args = ["rg", "--json", "--no-config", "--hidden", "--glob", "!.git", "-e", pattern]
    include = request.get("include")
    if include:
        if not isinstance(include, str):
            raise ToolError("invalid_argument", 'include must be a glob string such as "*.py".')
        args += ["--glob", include]
    args.append(str(base))
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=GREP_TIMEOUT, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ToolError(
            "timeout", f"The search took longer than {GREP_TIMEOUT} seconds. Narrow path or include."
        ) from exc
    if proc.returncode == 2 and not proc.stdout:
        raise ToolError("invalid_argument", proc.stderr.strip() or "The search failed.")
    counts: dict[str, int] = {}
    lines: list[dict[str, Any]] = []
    for raw in proc.stdout.splitlines():
        event = json.loads(raw)
        if event["type"] != "match":
            continue
        data = event["data"]
        path = data["path"].get("text", "")
        counts[path] = counts.get(path, 0) + 1
        if output == "lines" and len(lines) < MAX_GREP:
            text = data["lines"].get("text", "").rstrip("\n")
            lines.append({"path": path, "line": data["line_number"], "text": text[:2000]})
    total = sum(counts.values())
    result: dict[str, Any] = {"ok": True, "total_matches": total, "total_files": len(counts)}
    if output == "files":
        ranked = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
        result["files"] = [{"path": p, "matches": c} for p, c in ranked[:MAX_GREP]]
        result["truncated"] = len(counts) > MAX_GREP
    elif output == "lines":
        result["lines"] = lines
        result["truncated"] = total > MAX_GREP
    return result
