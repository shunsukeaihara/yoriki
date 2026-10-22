"""入出力と、パスの確認。結果は表示のためのもので、権限や実行状態の正本にしない（design.md §7.1）"""

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

WORKSPACE = Path("/workspace")
# write・edit で書けるのは work と output だけ（input・master は読取り専用。design.md §7.1）
WRITABLE = (WORKSPACE / "work", WORKSPACE / "output")
# これより大きいファイルは read・edit で読まない（shellで head・sed 等を使うよう案内する）
MAX_FILE_BYTES = 20 * 1024 * 1024

# 環境変数で、確かめる範囲を変えられるようにする（契約テストのためではなく、イメージを手元で試すため）
ROOT = Path(os.environ.get("YORIKI_SANDBOX_ROOT", str(WORKSPACE)))


class ToolError(Exception):
    """モデルに返して直させるエラー。code は CONTRACT.md の一覧から選ぶ"""

    def __init__(self, code: str, message: str, **detail: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


def read_request() -> dict[str, Any]:
    raw = sys.stdin.read()
    try:
        request = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ToolError("invalid_argument", f"The request is not valid JSON: {exc}") from exc
    if not isinstance(request, dict):
        raise ToolError("invalid_argument", "The request must be a JSON object.")
    return request


def respond(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.write("\n")
    sys.stdout.flush()


def resolve(value: Any, *, writable: bool = False, must_exist: bool = True) -> Path:
    """絶対パスを確かめ、symlinkを辿った先が /workspace（書くときは work・output）の中にあるかを確かめる"""
    if not isinstance(value, str) or not value:
        raise ToolError("invalid_argument", "path must be a non-empty string.")
    path = Path(value)
    if not path.is_absolute():
        raise ToolError("invalid_argument", f"path must be absolute (for example /workspace/work/{value}).")
    real = Path(os.path.realpath(path))
    roots = WRITABLE if writable else (ROOT,)
    if not any(real == root or root in real.parents for root in roots):
        if writable:
            raise ToolError("read_only", f"{value} is not writable. Write under /workspace/work or /workspace/output.")
        raise ToolError("outside_workspace", f"{value} is outside /workspace.")
    if must_exist and not real.exists():
        raise ToolError("not_found", f"{value} does not exist. Check the path with glob.")
    return real


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_size(path: Path) -> int:
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ToolError(
            "too_large",
            f"{path} is {size} bytes, larger than {MAX_FILE_BYTES}. "
            "Use shell (head, sed -n, python) to read part of it.",
        )
    return size
