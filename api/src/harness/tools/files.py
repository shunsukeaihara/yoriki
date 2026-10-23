"""ファイルを読む・書く・探すTool（harness.md §7.4・§7.8）。処理はSandboxの補助コマンドで行う（harness.md §8.4）。

ファイルの解析をWorkerのプロセスで行わない（壊れたPDF等の影響をSandboxに閉じ込める）。補助コマンドの結果は
ファイルの中身の表示として扱い、権限や実行状態の正本にしない。
"""

import base64
import binascii
from typing import Any

from llm.providers.base import ImagePart, Part, TextPart

from ..sandbox import WORKSPACE, workspace_path
from .base import Effect, Tool, ToolContext, ToolResult
from .output import limited

WRITABLE = ("/workspace/work", "/workspace/output")
PATH_DESCRIPTION = "Absolute path inside /workspace, e.g. /workspace/input/invoice.pdf."


def _always(_args: dict[str, Any]) -> bool:
    return True


def call_helper(context: ToolContext, command: str, request: dict[str, Any]) -> dict[str, Any]:
    return context.sandbox_session().helper(command, request)


def helper_error(result: dict[str, Any]) -> ToolResult:
    error = result.get("error") or {}
    return ToolResult.error(error.get("message") or f"The operation failed ({error.get('code', 'unknown')}).")


def resolve(path: str) -> str | None:
    return workspace_path(path)


def outside(path: str) -> ToolResult:
    return ToolResult.error(f"{path} is outside /workspace. Use an absolute path inside /workspace.")


def file_value(result: dict[str, Any]) -> dict[str, Any]:
    """読んだ・書いたファイルの版。RunnerがRunEventから「今の版の一覧」を求めるのに使う（harness.md §7.8）"""
    return {"file": {"path": result["path"], "sha256": result["sha256"]}}


# --- read ---

READ_DESCRIPTION = """Read a file in the sandbox, formatted for its type.

- Text, CSV, JSON, Markdown: numbered lines (`  12│text`). Up to 2000 lines per call; continue with `offset`.
- PDF: text per page (default: the first 10 pages; choose with `pages`, e.g. "1-5" or "3,7"). Pages without \
text are scanned; their images are shown (up to 5 per call) when you can see images.
- Excel (.xlsx): without `sheet`, the list of sheets and their sizes. With `sheet` (and optionally `range` such as \
"A1:F50"), cell values with addresses; formulas are shown next to their values.
- Word (.docx), PowerPoint (.pptx): headings, text, tables (and slide notes and layouts for .pptx).
- Images (PNG, JPEG, GIF, WebP, BMP): the image itself, resized to at most 1568px, when you can see images; \
otherwise format and size only.
- Other binary files: format and size only. Process them with Python in `shell`.

Use this tool, not `cat`, `head` or `sed` in the shell: the output is limited and formatted consistently. For a \
large file, read its structure first, then the part you need. Read independent files together in one response. \
You must read an existing file before you `write` or `edit` it."""

READ_PARAMETERS = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": PATH_DESCRIPTION},
        "offset": {"type": "integer", "minimum": 1, "description": "First line to read (1-based). Default 1."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "description": "Number of lines. Default 2000."},
        "pages": {"type": "string", "description": 'PDF pages, e.g. "1-5" or "3,7".'},
        "sheet": {"type": "string", "description": "Excel sheet name."},
        "range": {"type": "string", "description": 'Excel cell range, e.g. "A1:F50".'},
    },
    "required": ["path"],
    "additionalProperties": False,
}


def _read_header(result: dict[str, Any], images: bool) -> list[str]:
    kind = result.get("kind")
    lines = [f"File: {result['path']} ({kind}, {result.get('size', 0):,} bytes)"]
    if kind in ("text", "csv", "docx", "pptx") and result.get("total_lines") is not None:
        span = f"lines {result.get('first_line')}-{result.get('last_line')} of {result['total_lines']}"
        if kind in ("text", "csv"):
            span += f", encoding {result.get('encoding')}, newline {result.get('newline')}"
        lines.append(f"Showing {span}.")
    if kind == "csv" and result.get("csv"):
        info = result["csv"]
        lines.append(
            f"CSV: delimiter {info.get('delimiter')!r}, {info.get('rows')} rows, columns {info.get('columns')}"
        )
    if kind == "pdf":
        lines.append(f"PDF: {result.get('total_pages')} pages; showing pages {result.get('pages')}.")
        if scanned := result.get("pages_without_text"):
            lines.append(f"Pages without text (scanned images): {scanned}.")
            lines.append(_scanned_notice(scanned, [i.get("page") for i in result.get("images", [])], images))
    if kind == "image":
        if images:
            shown = result.get("images") or [{}]
            lines.append(f"The image follows (shown at {shown[0].get('width')}x{shown[0].get('height')}).")
        else:
            lines.append("The current model does not accept images, so only the format and size are shown.")
    return lines


def _scanned_notice(scanned: list[int], shown: list[int], images: bool) -> str:
    if not images:
        return (
            "Their content cannot be read as text, and the current model does not accept images. Tell the user "
            "that these pages need a model that accepts images."
        )
    rest = [page for page in scanned if page not in shown]
    # 画像だけを別にまとめて送るAPI（Chat Completions、Gemini）もあるので、並びの順をここで示す
    notice = f"The images of pages {shown} follow, in this order."
    if rest:
        notice += f" Pages {rest} are not shown; read them with pages (up to 5 images per call)."
    return notice


def _images(result: dict[str, Any]) -> list[Part]:
    """補助コマンドが返した画像（base64）。PDFのページには、どのページかの文を前に置く"""
    parts: list[Part] = []
    for image in result.get("images", []):
        try:
            data = base64.b64decode(image["data"], validate=True)
        except (KeyError, binascii.Error):
            continue
        if image.get("page"):
            parts.append(TextPart(f"[page {image['page']}]"))
        parts.append(ImagePart(image.get("mime", "image/png"), data))
    return parts


def _read_footer(result: dict[str, Any]) -> list[str]:
    if result.get("next_offset"):
        return [f"[more lines follow: call read with offset={result['next_offset']}]"]
    if result.get("next_pages"):
        return [f'[more pages follow: call read with pages="{result["next_pages"]}"]']
    return []


def execute_read(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    path = resolve(arguments["path"])
    if path is None:
        return outside(arguments["path"])
    # 画像は、使うモデルが画像の入力に対応するときだけ求める（harness.md §7.8、§12）
    images = context.run.model_version.supports_images
    request: dict[str, Any] = {"path": path, "images": images}
    for name in ("offset", "limit", "pages", "sheet", "range"):
        if name in arguments:
            request[name] = arguments[name]
    result = call_helper(context, "read", request)
    if not result.get("ok"):
        return helper_error(result)
    text = "\n".join([*_read_header(result, images), "", result.get("text", ""), *_read_footer(result)])
    hint = "Read a smaller part with offset/limit (or pages, or sheet and range)."
    meta: dict[str, Any] = {"path": result["path"], "kind": result.get("kind")}
    if shown := result.get("images"):
        meta["images"] = [{k: i.get(k) for k in ("page", "mime", "width", "height") if i.get(k)} for i in shown]
    return ToolResult(
        (TextPart(limited(context, text, head_ratio=1, full_text=result.get("text", ""), hint=hint)), *_images(result)),
        value=file_value(result),
        meta=meta,
    )


READ = Tool(
    "read",
    READ_DESCRIPTION,
    READ_PARAMETERS,
    execute_read,
    Effect.READ,
    concurrent=_always,
    replay_safe=True,
    needs_sandbox=True,
)


# --- write ---

WRITE_DESCRIPTION = """Create a text file, or replace the whole content of an existing one.

- Only inside /workspace/work (intermediate files) and /workspace/output (deliverables). /workspace/input and \
/workspace/master are read-only.
- To replace an existing file, `read` it first. If it changed after you read it, read it again.
- The encoding, line endings and BOM of an existing file are kept. New files are UTF-8 with LF; parent \
directories are created.
- For small changes to an existing file, use `edit` instead. For Excel, Word and PowerPoint files, write a Python \
script and run it with `shell`."""

WRITE_PARAMETERS = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Absolute path, e.g. /workspace/work/process.py."},
        "content": {"type": "string", "description": "The full content of the file."},
    },
    "required": ["path", "content"],
    "additionalProperties": False,
}


def _writable(path: str | None) -> bool:
    return path is not None and any(path == root or path.startswith(root + "/") for root in WRITABLE)


def not_writable(path: str) -> ToolResult:
    return ToolResult.error(
        f"{path} is not writable. Write only inside /workspace/work or /workspace/output; copy a file from "
        "/workspace/input or /workspace/master into /workspace/work to change it."
    )


def execute_write(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    path = resolve(arguments["path"])
    if not _writable(path):
        return not_writable(arguments["path"])
    assert path is not None
    context.sandbox_session().ensure()
    request: dict[str, Any] = {"path": path, "content": arguments["content"]}
    if (version := context.file_version(path)) is not None:
        request["expected_sha256"] = version
    result = call_helper(context, "write", request)
    if not result.get("ok"):
        return helper_error(result)
    action = "Created" if result.get("created") else "Updated"
    text = f"{action} {result['path']} ({result.get('lines', 0)} lines, {result.get('encoding')})."
    return ToolResult.text(text, value=file_value(result), meta={"path": result["path"]})


WRITE = Tool("write", WRITE_DESCRIPTION, WRITE_PARAMETERS, execute_write, Effect.WRITE_SANDBOX, needs_sandbox=True)


# --- edit ---

EDIT_DESCRIPTION = """Replace text in a text file.

- `read` the file first. If it changed after you read it, read it again.
- `old_string` must match the file exactly once (trailing whitespace differences are tolerated). Do not include \
the line number prefix from `read`. If it matches several places, add surrounding lines to make it unique, or set \
`replace_all` to replace every occurrence.
- Line endings and encoding of the file are kept.
- Only inside /workspace/work and /workspace/output."""

EDIT_PARAMETERS = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Absolute path of the file."},
        "old_string": {"type": "string", "minLength": 1, "description": "The exact text to replace."},
        "new_string": {"type": "string", "description": "The replacement text."},
        "replace_all": {"type": "boolean", "description": "Replace every occurrence. Default false."},
    },
    "required": ["path", "old_string", "new_string"],
    "additionalProperties": False,
}


def execute_edit(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    path = resolve(arguments["path"])
    if not _writable(path):
        return not_writable(arguments["path"])
    assert path is not None
    context.sandbox_session().ensure()
    version = context.file_version(path)
    if version is None:
        return ToolResult.error(f"You have not read {path} yet. Read it with read, then call edit again.")
    request = {
        "path": path,
        "old_string": arguments["old_string"],
        "new_string": arguments["new_string"],
        "replace_all": arguments.get("replace_all", False),
        "expected_sha256": version,
    }
    result = call_helper(context, "edit", request)
    if not result.get("ok"):
        return helper_error(result)
    count = result.get("replacements", 0)
    text = f"Replaced {count} occurrence{'s' if count != 1 else ''} in {result['path']}.\n{result.get('snippet', '')}"
    return ToolResult.text(text, value=file_value(result), meta={"path": result["path"]})


# 別のファイルへの edit は並べてもよいが（harness.md §7.4）、初期は順に実行する
EDIT = Tool("edit", EDIT_DESCRIPTION, EDIT_PARAMETERS, execute_edit, Effect.WRITE_SANDBOX, needs_sandbox=True)


# --- glob ---

GLOB_DESCRIPTION = """Find files by path pattern, newest first (up to 100). Also use it to list a directory.

- `pattern` is relative to `path`, e.g. "**/*.csv" or "*". `**` matches any number of directories.
- Use this tool, not `ls` or `find` in the shell."""

GLOB_PARAMETERS = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string", "minLength": 1, "description": 'Glob pattern, e.g. "**/*.xlsx".'},
        "path": {"type": "string", "description": "Directory to search. Default /workspace."},
    },
    "required": ["pattern"],
    "additionalProperties": False,
}


def execute_glob(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    base = resolve(arguments.get("path") or WORKSPACE)
    if base is None:
        return outside(arguments.get("path", ""))
    result = call_helper(context, "glob", {"pattern": arguments["pattern"], "path": base})
    if not result.get("ok"):
        return helper_error(result)
    entries = result.get("entries", [])
    if not entries:
        return ToolResult.text(f"No files match {arguments['pattern']!r} in {base}.", value={"total": 0})
    lines = [
        f"{e['path']}{'/' if e.get('type') == 'directory' else ''}"
        + (f"  ({e.get('size', 0):,} bytes)" if e.get("type") == "file" else "")
        for e in entries
    ]
    if result.get("truncated"):
        lines.append(f"[showing {len(entries)} of {result.get('total')} entries; narrow the pattern or path]")
    return ToolResult.text(limited(context, "\n".join(lines), head_ratio=1), value={"total": result.get("total")})


GLOB = Tool(
    "glob",
    GLOB_DESCRIPTION,
    GLOB_PARAMETERS,
    execute_glob,
    Effect.READ,
    concurrent=_always,
    replay_safe=True,
    needs_sandbox=True,
)


# --- grep ---

GREP_DESCRIPTION = """Search file contents with a regular expression (ripgrep syntax).

- `output`: "files" (default) lists files with their match counts; "lines" shows matching lines with line \
numbers; "count" shows only the totals. Up to 250 results.
- Narrow the search with `path` and `include` (e.g. "*.csv").
- It does not search inside PDF or Office files; use `read` for them.
- Use this tool, not `grep` or `rg` in the shell: the output is limited and formatted consistently."""

GREP_PARAMETERS = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string", "minLength": 1, "description": "Regular expression."},
        "path": {"type": "string", "description": "File or directory to search. Default /workspace."},
        "include": {"type": "string", "description": 'File name glob, e.g. "*.py".'},
        "output": {"type": "string", "enum": ["files", "lines", "count"]},
    },
    "required": ["pattern"],
    "additionalProperties": False,
}


def execute_grep(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    base = resolve(arguments.get("path") or WORKSPACE)
    if base is None:
        return outside(arguments.get("path", ""))
    output = arguments.get("output", "files")
    request = {"pattern": arguments["pattern"], "path": base, "output": output}
    if arguments.get("include"):
        request["include"] = arguments["include"]
    result = call_helper(context, "grep", request)
    if not result.get("ok"):
        return helper_error(result)
    total, files = result.get("total_matches", 0), result.get("total_files", 0)
    summary = f"{total} matches in {files} files."
    value = {"total_matches": total, "total_files": files}
    if total == 0:
        return ToolResult.text(f"No matches for {arguments['pattern']!r} in {base}.", value=value)
    if output == "files":
        lines = [f"{f['path']}: {f['matches']}" for f in result.get("files", [])]
    elif output == "lines":
        lines = [f"{line['path']}:{line['line']}: {line['text']}" for line in result.get("lines", [])]
    else:
        lines = []
    if result.get("truncated"):
        lines.append("[results truncated at 250; narrow the pattern, path or include]")
    text = "\n".join([summary, *lines])
    return ToolResult.text(limited(context, text, head_ratio=1), value=value)


GREP = Tool(
    "grep",
    GREP_DESCRIPTION,
    GREP_PARAMETERS,
    execute_grep,
    Effect.READ,
    concurrent=_always,
    replay_safe=True,
    needs_sandbox=True,
)
