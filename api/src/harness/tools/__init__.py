"""Toolの一覧（harness.md §7.4）"""

from .ask_user import ASK_USER
from .base import Effect, Tool
from .files import EDIT, GLOB, GREP, READ, WRITE
from .shell import JOB_KILL, JOB_OUTPUT, SHELL
from .todo_write import TODO_WRITE

# Sandboxを使うTool。Runtime Profileを固定したRunにだけ出す（harness.md §7.5の6）
SANDBOX_TOOLS: tuple[Tool, ...] = (READ, WRITE, EDIT, GLOB, GREP, SHELL, JOB_OUTPUT, JOB_KILL)
ALL_TOOLS: tuple[Tool, ...] = (*SANDBOX_TOOLS, ASK_USER, TODO_WRITE)
# Sandboxのファイルを変えるTool。障害で戻したWorkspaceに反映されていない呼出しを知らせるのに使う（harness.md §11.1）
WORKSPACE_WRITING_TOOLS = frozenset(t.name for t in SANDBOX_TOOLS if t.effect == Effect.WRITE_SANDBOX)
