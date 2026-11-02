"""タスクのRun（harness.md §7.5・§10、authorization.md §5.4・§6）。

タスクのRunは、受付時に固定したタスクの版（Manifestの `fixed.task`）の範囲で動く。エージェントが使えるのは、固定した
Skillの版と、固定したConnectorだけで、`skill` で探して読み込むことも、利用者の使えるConnectorから選ぶこともしない。
Toolは、Skillの「使ってよいTool」とタスクのToolの一覧の両方に入っているものだけ。使えるかは、起動した人ではなく
タスクの範囲で、区切りと操作のたびに確かめ直す（task/checks.py）。チャットから起動したRunは、それに加えて起動した人
（`Run.owner`）の今の権限でも確かめる（タスクの版の一覧 ∩ 起動した人の今の権限。authorization.md §6）。
修正チャットのRunは、タスクに固定したものの範囲で動き、動かした人（`Run.owner`）が今もタスクを管理できるかを確かめる
（停止中かは見ない。design.md §11.11）。
"""

from typing import Any

from django.utils.translation import gettext as _

from run.models import Run, RunLaunch, RunTrigger
from task import checks
from task.models import TaskDefinition
from user.models import User


def is_task_run(run: Run) -> bool:
    return run.trigger == RunTrigger.TASK


def task_entry(run: Run) -> dict[str, Any]:
    return ((run.manifest or {}).get("fixed") or {}).get("task") or {}


def load_task(run: Run) -> TaskDefinition | None:
    """今のタスク（停止・削除、範囲と部門、個人のタスクの持ち主を確かめるため、毎回DBから読む）"""
    if run.task_id is None:
        return None
    return TaskDefinition.objects.select_related("owner", "department").filter(pk=run.task_id).first()


def launcher(run: Run) -> User | None:
    """チャットから起動したRunの、起動した人（権限の判定のため、毎回DBから読む）。ほかのRunでは None"""
    if not is_task_run(run) or run.launch != RunLaunch.CHAT:
        return None
    return User.objects.filter(pk=run.owner_id).first()


def revoked_task(run: Run) -> str:
    """タスクのRunを続けられなくなった理由（タスクの停止・削除、個人のタスクの持ち主・チャットから起動した人の
    無効化、起動した人が起動できる部門から外れた、修正チャットの持ち主がタスクを管理できなくなった）。なければ空文字"""
    if not is_task_run(run):
        return ""
    task = load_task(run)
    reason = checks.task_problem(task, allow_suspended=run.is_task_fix)
    if not reason and run.launch == RunLaunch.CHAT:
        reason = checks.launcher_scope_problem(launcher(run), run)
    if not reason and run.is_task_fix:
        reason = checks.fixer_problem(User.objects.filter(pk=run.owner_id).first(), task)
    if reason:
        return _("タスクを続けられなくなったので、作業を止めました（%(reason)s）。") % {"reason": reason}
    return ""


def skill_problem(run: Run, skill, version) -> str:
    """タスクのRunがこのSkillを今も使えない理由（使えれば空文字）"""
    task = load_task(run)
    if task is None:
        return _("タスクが削除されました")
    if reason := checks.skill_problem(
        task,
        skill,
        version,
        connectors=task_entry(run).get("connectors", []),
        sensitive=run.handles_sensitive_data,
    ):
        return reason
    if run.launch == RunLaunch.CHAT:
        user = launcher(run)
        return checks.launcher_inactive(user) or checks.launcher_skill_problem(user, skill)
    return ""


def connector_permitted(run: Run, connector) -> bool:
    task = load_task(run)
    connectors = task_entry(run).get("connectors", [])
    if task is None or not checks.connector_permitted(
        task, connector, connectors=connectors, allow_suspended=run.is_task_fix
    ):
        return False
    if run.launch == RunLaunch.CHAT:
        user = launcher(run)
        return user is not None and checks.launcher_connector_permitted(user, connector)
    return True


def allowed_tools(run: Run, skill_tools: set[str]) -> set[str]:
    """タスクのRunが使うTool：固定したSkillの「使ってよいTool」を合わせたもの（authorization.md §5.4）。

    タスクの版がToolの一覧を持っていた頃に受け付けたRunは、固定したその一覧との両方に入っているものに限る"""
    fixed = task_entry(run).get("tools")
    return skill_tools if fixed is None else skill_tools & set(fixed)


def output_schema(run: Run) -> dict[str, Any] | None:
    """業務結果のSchema。タスクのものか、なければSkillのもの（受付時に決めて固定したもの）"""
    return task_entry(run).get("output_schema")
