"""Pythonのpackageの追加（harness.md §7.4、design.md §7.6）"""

import logging
from typing import Any

from runtime.launcher import SandboxUnavailable
from runtime.packages import MAX_REQUESTED, PackageError, catalog_problems, parse_requests

from .. import packages
from .base import Effect, Tool, ToolContext, ToolResult

logger = logging.getLogger(__name__)

INSTALL_DESCRIPTION = f"""Add Python packages to this sandbox's Python (`python` in `shell`).

- Use this only when the libraries that are already installed are not enough. pandas, openpyxl, pypdf, pdfplumber, \
python-docx, python-pptx, matplotlib and markitdown are already installed.
- Only packages in the package catalog managed by the platform administrator can be added. Their dependencies are \
added automatically. If a package is not in the catalog, ask the user to have the administrator add it, or work \
without it; do not try other ways to install it.
- Give `version` only when you need a specific version; otherwise the newest allowed version is used.
- Only binary wheels are used; packages that need compiling or OS libraries cannot be added.
- Added packages stay available for the rest of this conversation.
- Up to {MAX_REQUESTED} packages at a time. Fetching can take a minute or more."""

INSTALL_PARAMETERS = {
    "type": "object",
    "properties": {
        "packages": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_REQUESTED,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 100, "description": "The package name."},
                    "version": {
                        "type": "string",
                        "maxLength": 50,
                        "description": "An exact version (e.g. 3.10.1). Omit to use the newest allowed version.",
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["packages"],
    "additionalProperties": False,
}


def execute_install(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    session = ctx.sandbox_session()
    try:
        requests = parse_requests(args.get("packages") or [])
    except PackageError as exc:
        return ToolResult.error(str(exc))
    if problems := catalog_problems(requests):
        return ToolResult.error("Not installed: " + "; ".join(problems) + f".\n{packages.NOT_IN_CATALOG}")
    try:
        result = packages.add(session, requests)
    except PackageError as exc:
        return ToolResult.error(str(exc))
    except SandboxUnavailable:
        raise
    added = sorted((w.name, w.version) for w in result.added)
    lines = []
    if added:
        lines.append("Installed: " + ", ".join(f"{n} {v}" for n, v in added))
    if result.already:
        lines.append("Already installed: " + ", ".join(result.already))
    value = {
        "installed": [{"name": n, "version": v} for n, v in added],
        "already_installed": result.already,
    }
    return ToolResult.text("\n".join(lines), value=value)


INSTALL_PYTHON_PACKAGES = Tool(
    "install_python_packages",
    INSTALL_DESCRIPTION,
    INSTALL_PARAMETERS,
    execute_install,
    Effect.WRITE_SANDBOX,
    needs_sandbox=True,
)
