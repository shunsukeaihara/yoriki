"""入出力と、パスの確認。結果は表示のためのもので、権限や実行状態の正本にしない（design.md §7.1）"""

import contextlib
import errno
import hashlib
import json
import os
import secrets
import stat
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


def _open_dir(path: Path, *, create: bool) -> int:
    """ディレクトリを、symlinkを辿らずに / から1つずつ開く（確かめた後にsymlinkへ差し替えられても外へ出ない）"""
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in path.parts[1:]:
            try:
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(name, 0o755, dir_fd=fd)
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise ToolError("not_a_directory", f"{path} has a symlink or a file in its path.") from exc
                raise
            os.close(fd)
            fd = child
    except BaseException:
        os.close(fd)
        raise
    return fd


def _hash_at(dir_fd: int, name: str) -> str | None:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    digest = hashlib.sha256()
    with os.fdopen(fd, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def replace_file(path: Path, data: bytes, expected_sha256: str | None) -> str:
    """一時ファイルに書いてから置き換える（途中で失敗しても、元のファイルを半端に残さない）。

    置き換える直前に、今の内容が expected_sha256（None なら、まだないこと）と一致するかを確かめ直す。
    親のディレクトリは symlink を辿らずに開き、置き換える先が通常のファイル以外なら断る。返すのは書いた内容のSHA-256
    """
    dir_fd = _open_dir(path.parent, create=True)
    tmp = f".{path.name}.yoriki-{secrets.token_hex(4)}"
    try:
        try:
            current = os.stat(path.name, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            current = None
        if current is not None and not stat.S_ISREG(current.st_mode):
            raise ToolError("not_a_file", f"{path} is not a regular file.")
        mode = stat.S_IMODE(current.st_mode) if current is not None else 0o644
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=dir_fd)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if _hash_at(dir_fd, path.name) != expected_sha256:
            raise ToolError("changed", f"{path} changed while it was being written. Read it again and retry.")
        os.replace(tmp, path.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp, dir_fd=dir_fd)
        raise
    finally:
        os.close(dir_fd)
    return hashlib.sha256(data).hexdigest()
