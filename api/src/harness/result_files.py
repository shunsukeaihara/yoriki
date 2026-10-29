"""業務結果のファイルの項目（harness.md §10.5、design.md §4.4）。

業務結果のSchemaのルートの項目に `{"type": "string", "format": "file"}`（ファイルの並びなら
`{"type": "array", "items": {"type": "string", "format": "file"}}`）と書くと、その値は `/workspace/output` の中の
ファイルのパスになる。出口へ渡す成果物は、この項目で指したものだけにする（Runの成果物すべては渡さない）。

- モデルへは `format` を外し、説明の文に置き換えて渡す（プロバイダごとに `format` の扱いが違うため。harness.md §7.3）。
- `submit_result` のときに、指したファイルがSandboxにあるかを確かめる。
- 配信を作るとき（Runの完了）に、回収した成果物（Artifact）に結び付ける。
"""

import posixpath
from typing import Any

FILE_FORMAT = "file"
OUTPUT_DIR = "/workspace/output"
FILE_DESCRIPTION = "Path of a file you created in /workspace/output (for example /workspace/output/report.xlsx)."


def _is_file(schema: Any) -> bool:
    return isinstance(schema, dict) and schema.get("format") == FILE_FORMAT


def file_fields(schema: dict[str, Any] | None) -> dict[str, bool]:
    """ファイルの項目の名前と、並びか（True）・1つか（False）"""
    fields: dict[str, bool] = {}
    for name, child in ((schema or {}).get("properties") or {}).items():
        if _is_file(child):
            fields[name] = False
        elif isinstance(child, dict) and child.get("type") == "array" and _is_file(child.get("items")):
            fields[name] = True
    return fields


def file_format_problem(schema: dict[str, Any]) -> str:
    """`format: file` を置ける場所はルートの項目（文字列か、文字列の並び）だけ。誤りなら理由、正しければ空文字"""

    def visit(node: Any, *, allowed: bool, top: bool = False) -> str:
        """`allowed`：ファイルにしてよい場所（ルートの項目か、ルートの項目の並びの要素）。`top`：ルートの項目"""
        if not isinstance(node, dict):
            return ""
        if _is_file(node) and not (allowed and node.get("type") == "string"):
            return 'format "file" can be used only on a top-level string property (or the items of a top-level array)'
        children = [(c, False) for c in (node.get("properties") or {}).values()]
        if isinstance(node.get("additionalProperties"), dict):
            children.append((node["additionalProperties"], False))
        if isinstance(node.get("items"), dict):
            children.append((node["items"], top and node.get("type") == "array"))
        return next((p for child, ok in children if (p := visit(child, allowed=ok))), "")

    children = [(c, True, True) for c in (schema.get("properties") or {}).values()]
    children += [(schema.get(key), False, False) for key in ("additionalProperties", "items")]
    return next((p for child, ok, top in children if (p := visit(child, allowed=ok, top=top))), "")


def model_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """モデルへ渡すSchema。ファイルの項目の `format` を外し、説明に置き場所を書く"""
    fields = file_fields(schema)
    if not fields:
        return schema
    properties = dict(schema["properties"])
    for name, is_list in fields.items():
        child = dict(properties[name])
        if is_list:
            child["items"] = _plain(child["items"])
            child["description"] = _join(child.get("description"), "Each item is the p" + FILE_DESCRIPTION[1:])
        else:
            child = _plain(child)
        properties[name] = child
    return {**schema, "properties": properties}


def _plain(schema: dict[str, Any]) -> dict[str, Any]:
    plain = {k: v for k, v in schema.items() if k != "format"}
    plain["description"] = _join(schema.get("description"), FILE_DESCRIPTION)
    return plain


def _join(described: Any, text: str) -> str:
    return f"{described} {text}" if isinstance(described, str) and described.strip() else text


def output_path(value: Any) -> str | None:
    """`/workspace/output` の中のファイルの相対パス（成果物のArtifactの `path` と同じ形）。中でなければNone"""
    if not isinstance(value, str) or not value.strip():
        return None
    path = value.strip()
    if not path.startswith("/"):
        path = posixpath.join(OUTPUT_DIR, path)
    path = posixpath.normpath(path)
    if not path.startswith(OUTPUT_DIR + "/"):
        return None
    relative = path.removeprefix(OUTPUT_DIR + "/")
    # 隠しファイルは成果物として回収しない（harness/sandbox.py の collect_outputs()）
    if any(part.startswith(".") for part in relative.split("/")):
        return None
    return relative


def result_files(schema: dict[str, Any] | None, result: Any) -> list[tuple[str, str]]:
    """業務結果が指すファイルの（項目の名前、`/workspace/output` からの相対パス）。正しくない値は飛ばす"""
    if not isinstance(result, dict):
        return []
    files = []
    for name, is_list in file_fields(schema).items():
        values = result.get(name)
        for value in (values if isinstance(values, list) else []) if is_list else [values]:
            if (path := output_path(value)) is not None:
                files.append((name, path))
    return files
