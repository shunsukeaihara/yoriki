"""画面の通知（in_app。design.md §4.3・§4.4、authorization.md §5.5・§7.1）。

受け手は送るときに決める（送り直しでも、そのときに通知してよい人に送る）：

- 個人のタスク：持ち主
- 部門・全社・システムのタスクの手動実行：実行した人
- 入口（Webhook）から動いたRun：タスクを管理できる人全員

どれも、Runを実行したときのタスクの範囲と部門で、今もタスクを管理できる人に限る（Runの結果を見られる人と同じ。
authorization.md §7.1）。同じ配信を同じ人へ二度載せない（送り直しても重ならない）。
"""

from django.contrib.auth import get_user_model
from django.utils.translation import gettext as _

from run.models import RunLaunch
from stream.notifier import notify_notification
from task.models import SCOPE_PERMISSIONS, TaskScope, can_manage

from .adapters import Outcome
from .models import AttemptOutcome, Delivery, Notification

PERMISSION_BACKEND = "utils.backends.PasswordBackend"


def recipients(delivery: Delivery) -> list:
    run = delivery.run
    task = run.task
    if task is None or run.task_scope is None:
        return []
    users = get_user_model().objects.filter(is_active=True)
    if run.task_scope == TaskScope.PERSONAL:
        candidates = users.filter(pk=task.owner_id)
    elif run.launch == RunLaunch.MANUAL:
        candidates = users.filter(pk=run.owner_id)
    else:
        # permissionはDjango標準の形（Group・本人・superuser）で持つ。所属の判定は下の can_manage() で行う
        perm = SCOPE_PERMISSIONS[TaskScope(run.task_scope)].perm
        candidates = users.filter(pk__in=get_user_model().objects.with_perm(perm, backend=PERMISSION_BACKEND))
    return [
        user
        for user in candidates.order_by("pk")
        if can_manage(user, run.task_scope, owner_id=task.owner_id, department_id=run.task_department_id)
    ]


def notify(delivery: Delivery) -> Outcome:
    users = recipients(delivery)
    if not users:
        return Outcome(AttemptOutcome.FAILED, error=_("通知を受け取れる人がいません"))
    run = delivery.run
    task = run.task
    assert task is not None  # recipients() はタスクのないRunでは空を返す
    Notification.objects.bulk_create(
        [
            Notification(
                user=user,
                delivery=delivery,
                run=run,
                task=task,
                event_type=delivery.event_type,
                task_name=task.name,
            )
            for user in users
        ],
        ignore_conflicts=True,
    )
    # 通知欄の未読の数をすぐ更新できるように、受け手の画面へ知らせる（保存はしないので、届かなくても次に読めば分かる）
    for user in users:
        notify_notification(user.uid)
    return Outcome(AttemptOutcome.ACCEPTED)
