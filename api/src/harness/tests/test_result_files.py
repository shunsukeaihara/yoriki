"""業務結果のファイルの項目（harness/result_files.py）と、submit_result での確認"""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from harness.result_files import (
    file_fields,
    file_format_problem,
    missing_files,
    model_schema,
    output_path,
    result_files,
)
from harness.tools.base import ToolContext
from harness.tools.submit_result import _file_problem, make_submit_result_tool
from llm.providers.base import TextPart

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "report": {"type": "string", "format": "file", "description": "照合の結果"},
        "attachments": {"type": "array", "items": {"type": "string", "format": "file"}},
    },
}


def test_file_fields_and_the_schema_for_the_model():
    assert file_fields(SCHEMA) == {"report": False, "attachments": True}
    shown = model_schema(SCHEMA)
    assert "format" not in shown["properties"]["report"]
    assert shown["properties"]["report"]["description"].startswith("照合の結果 Path of a file")
    assert "format" not in shown["properties"]["attachments"]["items"]
    assert "/workspace/output" in shown["properties"]["attachments"]["description"]
    # 元のSchemaは変えない
    assert SCHEMA["properties"]["report"]["format"] == "file"
    tool = make_submit_result_tool(SCHEMA)
    assert tool.needs_sandbox
    assert tool.parameters == shown
    assert not make_submit_result_tool({"type": "object", "properties": {}}).needs_sandbox


@pytest.mark.parametrize(
    "schema",
    [
        {
            "type": "object",
            "properties": {"a": {"type": "object", "properties": {"f": {"type": "string", "format": "file"}}}},
        },
        {"type": "object", "properties": {"a": {"type": "integer", "format": "file"}}},
        {
            "type": "object",
            "properties": {
                "a": {"type": "array", "items": {"type": "array", "items": {"type": "string", "format": "file"}}}
            },
        },
        {"type": "object", "additionalProperties": {"type": "string", "format": "file"}},
    ],
)
def test_file_format_only_on_top_level_strings(schema):
    assert file_format_problem(schema)


def test_file_format_problem_accepts_top_level_files():
    assert file_format_problem(SCHEMA) == ""


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/workspace/output/report.xlsx", "report.xlsx"),
        ("a/b.csv", "a/b.csv"),
        ("/workspace/output/a/../b.csv", "b.csv"),
        ("/workspace/input/x.csv", None),
        ("../etc/passwd", None),
        ("/workspace/output", None),
        ("/workspace/output/.hidden", None),
        ("", None),
        (3, None),
    ],
)
def test_output_path(value, expected):
    assert output_path(value) == expected


def test_result_files_skips_wrong_values():
    result = {"ok": True, "report": "/workspace/input/x", "attachments": ["a.csv", 3, "b.csv"]}
    assert result_files(SCHEMA, result) == [("attachments", "a.csv"), ("attachments", "b.csv")]
    assert result_files(SCHEMA, None) == []


def _context(files: dict[str, dict]) -> ToolContext:
    def helper(command, request):
        assert command == "stat"
        return {"ok": True, "exists": False, **files.get(request["path"], {})}

    session = SimpleNamespace(helper=helper)
    return cast(ToolContext, SimpleNamespace(sandbox_session=lambda: session))


def _error(schema: dict, arguments: dict, context: ToolContext) -> str:
    result = _file_problem(schema, arguments, context)
    assert result is not None
    assert result.is_error
    return "".join(p.text for p in result.content if isinstance(p, TextPart))


def test_submit_result_checks_the_files_exist():
    present = {
        "/workspace/output/report.xlsx": {"exists": True, "type": "file", "path": "/workspace/output/report.xlsx"}
    }
    assert _file_problem(SCHEMA, {"ok": True, "report": "report.xlsx"}, _context(present)) is None
    assert "does not exist" in _error(SCHEMA, {"ok": True, "report": "other.xlsx"}, _context(present))
    outside = _error(SCHEMA, {"ok": True, "report": "/workspace/input/a.csv"}, _context(present))
    assert "is not a file in /workspace/output" in outside
    # symlinkは回収しないので断る（statは辿った先を返す）
    linked = {"/workspace/output/l.csv": {"exists": True, "type": "file", "path": "/workspace/input/a.csv"}}
    assert "symlinks" in _error(SCHEMA, {"ok": True, "attachments": ["l.csv"]}, _context(linked))
    # ファイルの項目がなければSandboxを使わない
    assert _file_problem({"type": "object", "properties": {}}, {}, _context({})) is None


def test_missing_files_compares_the_workspace_at_completion():
    result = {"ok": True, "report": "report.xlsx", "attachments": ["a.csv"]}
    present = {"report.xlsx": "r1", "a.csv": "a1"}
    collected = {"report.xlsx": "r1", "a.csv": "a1"}
    assert missing_files(SCHEMA, result, present, collected.get) == []
    # submit_result の後に消した（同じ会話の前のRunの成果物は渡さない）
    assert missing_files(SCHEMA, result, {"a.csv": "a1"}, collected.get) == ["/workspace/output/report.xlsx"]
    # 回収できなかった（上限を超えた等）、または回収した中身が今の中身と違う
    assert missing_files(SCHEMA, result, present, {"report.xlsx": "r1"}.get) == ["/workspace/output/a.csv"]
    assert missing_files(SCHEMA, result, present, {**collected, "a.csv": "old"}.get) == ["/workspace/output/a.csv"]
