"""テキストのファイルの読み書き（harness.md §7.8）。encoding・改行・BOMは元のファイルに合わせて保つ"""

import codecs
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from charset_normalizer import from_bytes

from .common import ToolError, check_size, replace_file, resolve, sha256_of

MAX_LINES = 2000
MAX_LINE_CHARS = 2000
# 変えた箇所の前後に返す行の数
SNIPPET_CONTEXT = 3


@dataclass
class Decoded:
    text: str  # 改行は LF にそろえた本文（表示と行番号に使う）
    encoding: str
    bom: bool
    newline: str  # "\n"、"\r\n"、どちらもあれば "mixed"
    raw: str = ""  # 改行をそろえる前の本文（edit はこれを直接変え、触らない行をそのまま残す）


def is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def decode(data: bytes) -> Decoded:
    bom = data.startswith(codecs.BOM_UTF8)
    raw = data[len(codecs.BOM_UTF8) :] if bom else data
    try:
        text, encoding = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        best = from_bytes(raw).best()
        if best is None:
            raise ToolError(
                "not_text", "The file is not text in a known encoding. Use shell (python) to read it."
            ) from None
        # Shift_JIS は、Windowsの拡張文字も読めるcp932として扱う
        encoding = "cp932" if best.encoding in ("shift_jis", "cp932") else best.encoding
        text = raw.decode(encoding)
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    newline = "mixed" if crlf and lf else "\r\n" if crlf else "\n"
    return Decoded(text.replace("\r\n", "\n"), encoding, bom, newline, text)


def encode(decoded: Decoded, text: str, *, keep_newlines: bool = False) -> bytes:
    """元のencoding・BOMで書く。keep_newlines なら改行をそのまま、でなければ元のファイルの改行に合わせる"""
    if keep_newlines:
        body = text
    else:
        body = text.replace("\r\n", "\n")
        if decoded.newline == "\r\n":
            body = body.replace("\n", "\r\n")
    try:
        data = body.encode(decoded.encoding)
    except UnicodeEncodeError as exc:
        raise ToolError(
            "unencodable",
            f"The new text has characters that {decoded.encoding} cannot store ({exc.object[exc.start : exc.end]!r}).",
        ) from exc
    return (codecs.BOM_UTF8 + data) if decoded.bom else data


def load(path: Path) -> tuple[bytes, Decoded]:
    if not path.is_file():
        raise ToolError("not_a_file", f"{path} is not a regular file.")
    check_size(path)
    data = path.read_bytes()
    if is_binary(data):
        raise ToolError("not_text", f"{path} is a binary file. write and edit work only on text files.")
    return data, decode(data)


def numbered(lines: list[str], start: int) -> str:
    """1から数えた行番号つきの本文。長い行は切る"""
    out = []
    for number, line in enumerate(lines, start=start):
        shown = (
            line
            if len(line) <= MAX_LINE_CHARS
            else line[:MAX_LINE_CHARS] + f"… [line truncated: {len(line)} characters]"
        )
        out.append(f"{number:>6}│{shown}")
    return "\n".join(out)


def page_of(text: str, offset: int, limit: int) -> tuple[str, dict[str, Any]]:
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    total = len(lines)
    start = max(offset, 1)
    chosen = lines[start - 1 : start - 1 + limit]
    end = start + len(chosen) - 1
    meta = {"total_lines": total, "first_line": start, "last_line": end}
    if end < total:
        meta["next_offset"] = end + 1
    return numbered(chosen, start), meta


def check_version(path: Path, expected: Any) -> None:
    """読んでから書く（harness.md §7.8）。Runnerが記録した版と今の版が一致するときだけ書く"""
    exists = path.exists()
    if expected is None:
        if exists:
            raise ToolError("not_read", f"{path} already exists and has not been read. Read it with read first.")
        return
    if not exists:
        raise ToolError("not_found", f"{path} no longer exists.")
    if sha256_of(path) != expected:
        raise ToolError("changed", f"{path} has changed since it was read. Read it again before changing it.")


def snippet(text: str, start_line: int, end_line: int) -> str:
    lines = text.split("\n")
    first = max(start_line - SNIPPET_CONTEXT, 1)
    last = min(end_line + SNIPPET_CONTEXT, len(lines))
    return numbered(lines[first - 1 : last], first)


def run_write(request: dict[str, Any]) -> dict[str, Any]:
    path = resolve(request.get("path"), writable=True, must_exist=False)
    content = request.get("content")
    if not isinstance(content, str):
        raise ToolError("invalid_argument", "content must be a string.")
    expected = request.get("expected_sha256")
    check_version(path, expected)
    if path.exists():
        _, decoded = load(path)
        created = False
        # 改行がまじったファイルは、送られた内容の改行のまま書く
        if decoded.newline == "mixed":
            decoded.newline = "\n"
    else:
        decoded = Decoded("", "utf-8", False, "\n")
        created = True
    sha256 = replace_file(path, encode(decoded, content), expected)
    lines = content.replace("\r\n", "\n").count("\n") + (0 if content.endswith("\n") or not content else 1)
    return {
        "ok": True,
        "path": str(path),
        "created": created,
        "sha256": sha256,
        "lines": lines,
        "encoding": decoded.encoding,
    }


# old_string の改行（LF）は、ファイルのCRLFにもLFにも一致させる
NEWLINE = r"\r?\n"


def _exact_pattern(old: str) -> re.Pattern[str]:
    return re.compile(NEWLINE.join(re.escape(line) for line in old.split("\n")))


def _flexible_pattern(old: str) -> re.Pattern[str]:
    """行末の空白の違いを無視して探す（2段目の一致。あいまいな一致はしない）"""
    return re.compile(NEWLINE.join(re.escape(line.rstrip(" \t")) + r"[ \t]*" for line in old.split("\n")))


def _newline_for(raw: str, span: str) -> str:
    """置き換える文字列の改行。一致した箇所の改行に合わせ、改行を含まなければファイルで多い方にする"""
    if "\n" in span:
        return "\r\n" if "\r\n" in span else "\n"
    crlf = raw.count("\r\n")
    return "\r\n" if crlf > raw.count("\n") - crlf else "\n"


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def run_edit(request: dict[str, Any]) -> dict[str, Any]:
    path = resolve(request.get("path"), writable=True)
    old = request.get("old_string")
    new = request.get("new_string")
    replace_all = bool(request.get("replace_all", False))
    if not isinstance(old, str) or not old:
        raise ToolError("invalid_argument", "old_string must be a non-empty string.")
    if not isinstance(new, str):
        raise ToolError("invalid_argument", "new_string must be a string.")
    old, new = old.replace("\r\n", "\n"), new.replace("\r\n", "\n")
    if old == new:
        raise ToolError("invalid_argument", "old_string and new_string are the same.")
    expected = request.get("expected_sha256")
    check_version(path, expected)
    _, decoded = load(path)
    # 改行をそろえずに、元の本文のまま探して置き換える。改行がまじったファイルでも、触らない行はそのまま残る
    text = decoded.raw

    spans = [(m.start(), m.end()) for m in _exact_pattern(old).finditer(text)]
    if not spans:
        spans = [(m.start(), m.end()) for m in _flexible_pattern(old).finditer(text)]
    if not spans:
        raise ToolError(
            "no_match", "old_string was not found. Read the file again and copy the exact text, including indentation."
        )
    if len(spans) > 1 and not replace_all:
        lines = [_line_of(text, s) for s, _ in spans]
        raise ToolError(
            "multiple_matches",
            f"old_string matches {len(spans)} places (lines {', '.join(map(str, lines[:20]))}). "
            "Add surrounding lines to make it unique, or set replace_all.",
            lines=lines,
        )
    result, last, first_line, last_line = [], 0, None, 0
    shift = 0
    for start, end in spans:
        replacement = new.replace("\n", _newline_for(text, text[start:end]))
        result.append(text[last:start])
        result.append(replacement)
        last = end
        line = _line_of(text, start) + shift
        first_line = line if first_line is None else first_line
        last_line = line + new.count("\n")
        shift += new.count("\n") - text.count("\n", start, end)
    result.append(text[last:])
    updated = "".join(result)
    sha256 = replace_file(path, encode(decoded, updated, keep_newlines=True), expected)
    return {
        "ok": True,
        "path": str(path),
        "replacements": len(spans),
        "sha256": sha256,
        "snippet": snippet(updated.replace("\r\n", "\n"), first_line or 1, last_line),
    }
