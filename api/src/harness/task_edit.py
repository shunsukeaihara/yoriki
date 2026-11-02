"""タスクの編集のRun（harness.md §10.10、design.md §11.11）。

編集のRunは修正チャットの会話のRunで、その会話のタスクを1つだけ対象にし、Run開始時にManifestの `fixed.task_edit` へ
固定する。タスク管理のToolは引数にタスクを取らず、対象のタスクにだけ働く。権限は依頼した人（`Run.owner`）が今も
タスクを管理できるかを、呼ぶたびと区切りのたびにDBから読み直して確かめる。
"""

from django.utils.translation import gettext as _

from run.models import Run, RunKind
from task.checks import fixer_problem
from task.models import TaskDefinition, TaskScope
from user.models import User


def edit_entry(run: Run) -> dict[str, str] | None:
    """編集のRunの対象（{"task", "name"}）。編集のRunでなければNone"""
    if run.kind != RunKind.TASK_EDIT:
        return None
    return dict(((run.manifest or {}).get("fixed") or {}).get("task_edit") or {})


def edit_target(run: Run) -> TaskDefinition | None:
    """対象のタスク（削除したものも返す。権限の判定で断る）。毎回DBから読む"""
    entry = edit_entry(run)
    if not entry:
        return None
    return (
        TaskDefinition.objects.select_related("current_revision", "department", "owner")
        .filter(uid=entry["task"])
        .first()
    )


def scope_label(task: TaskDefinition) -> str:
    """範囲（「部門 経理部」等）"""
    if task.scope == TaskScope.DEPARTMENT and task.department is not None:
        return f"{task.get_scope_display()} {task.department.name}"
    return str(task.get_scope_display())


def requester(run: Run) -> User | None:
    """編集を依頼した人（権限の判定のため、毎回DBから読む）"""
    return User.objects.filter(pk=run.owner_id).first()


def target_problem(run: Run, task: TaskDefinition | None) -> str:
    """編集を続けられない理由（画面に出すので日本語）。依頼した人がタスクを管理できなくなった・タスクが削除された、
    機密でない修正チャットで、タスクが機密になった（読んだ定義を次のモデルの呼出しで送らない）"""
    if reason := fixer_problem(requester(run), task):
        return reason
    assert task is not None  # fixer_problem() が確かめた
    revision = task.current_revision
    if revision is not None and revision.handles_sensitive_data and not run.handles_sensitive_data:
        return _("タスクが個人情報・機密情報を扱うようになりました。タスクの画面から新しい修正チャットを始めてください")
    return ""


def revoked_task_edit(run: Run) -> str:
    """編集のRunを続けられなくなった理由（区切りのたびに確かめる）。なければ空文字"""
    if edit_entry(run) is None:
        return ""
    if reason := target_problem(run, edit_target(run)):
        return _("タスクを直す作業を続けられなくなったので、止めました（%(reason)s）。") % {"reason": reason}
    return ""


def runtime_text(run: Run) -> str:
    """`<runtime_context>` の中の `<task_edit>`。対象のタスクと今の版"""
    entry = edit_entry(run)
    if entry is None:
        return ""
    task = edit_target(run)
    name = task.name if task is not None else entry.get("name", "")
    version = task.version_number if task is not None else 0
    return f'  <task_edit task="{_escape(name)}" version="{version}" />'


def confirmation_title(name: str) -> str:
    """request_task_edit の確認の文（画面に出すので日本語）"""
    return _("タスク「%(name)s」の定義を直しますか？") % {"name": name}


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
