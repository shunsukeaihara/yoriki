"""配信が今も出口へ出してよいかの確かめ直し（authorization.md §6・§7.2）。送る直前と、出口へ渡したファイルの取得の
たびに使う"""

from django.utils.translation import gettext as _

from run.models import RunLaunch
from task.checks import launcher_scope_problem, task_problem
from user.models import User

from .models import Delivery, Destination


def send_problem(delivery: Delivery) -> str:
    """この配信の中身を今出口へ出せない理由（出せれば空文字）。固定した版のURLへ送るが、停止・削除・範囲・機密は今の
    出口の状態で見る。タスクの停止・削除・部門の無効化・持ち主の無効化と、実行の削除も確かめる"""
    run = delivery.run
    if run.conversation.deleted:
        return _(
            "実行が削除されました"
        )  # 消した実行の結果は、まだ送っていない配信・取得URLでも出さない（design.md §8）
    destination = Destination.objects.select_related("current_revision", "department").get(
        pk=delivery.destination_revision.destination_id
    )
    sensitive = run.handles_sensitive_data
    problem = destination.problem_for(run.task, sensitive=sensitive)
    if (
        not problem
        and sensitive
        and destination.is_external
        and not delivery.destination_revision.allows_sensitive_results
    ):
        problem = _("出口が機密の結果を送ってよい先ではありません")
    connection = delivery.destination_revision.connection
    if not problem and connection is not None and (connection.deleted or connection.suspended):
        problem = _("出口の連携の接続が停止中です")
    problem = problem or task_problem(run.task)
    if not problem and run.launch == RunLaunch.CHAT:
        # チャットから起動したRunは、起動した人が今も有効で起動できる立場かも確かめる（authorization.md §6）
        problem = launcher_scope_problem(User.objects.filter(pk=run.owner_id).first(), run)
    return problem
