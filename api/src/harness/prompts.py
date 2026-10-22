"""system promptの組立（harness.md §6.1）。使えるToolの節だけを入れる"""

from pathlib import Path

from .tools.base import Tool

PROMPT_DIR = Path(__file__).parent / "prompts"

TOOL_GUIDANCE = {
    "read": (
        "- Read, search and change files with `read`, `grep`, `glob` and `edit`, not with `cat`, `grep`, `sed` or "
        "`ls` in `shell`. Read independent files together in one response."
    ),
    "shell": (
        "- `shell` runs each command in a new shell in the sandbox (no network). Start long commands with "
        "`background: true`; you are notified when they finish."
    ),
    "write": (
        "- Inputs are in /workspace/input and /workspace/master (read-only). Work in /workspace/work and put "
        "deliverables in /workspace/output."
    ),
    "ask_user": "- Use `ask_user` for business decisions only. Call it alone; the answers arrive as its result.",
    "todo_write": (
        "- For work with 3 or more steps, keep a task list with `todo_write` and update it as you progress."
    ),
}


def system_prompt(tools: tuple[Tool, ...]) -> str:
    lines = [TOOL_GUIDANCE[t.name] for t in tools if t.name in TOOL_GUIDANCE]
    guidance = ("# Tools\n- Call independent tools together in one response.\n" + "\n".join(lines)) if lines else ""
    template = (PROMPT_DIR / "system.md").read_text()
    return template.replace("{tool_guidance}", guidance).strip()
