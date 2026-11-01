"""配信が今も出口へ出してよいかの確かめ直し（authorization.md §6・§7.2）。送る直前と、出口へ渡したファイルの取得の
たびに使う"""

from django.utils.translation import gettext as _

from run.models import RunLaunch
from task.checks import launcher_inactive, launcher_problem, task_problem
from user.models import User

from .models import URL_KINDS, Delivery, Destination


def send_problem(delivery: Delivery) -> str:
    """この配信の中身を今出口へ出せない理由（出せれば空文字）。固定した版のURLへ送るが、停止・削除・範囲・機密は今の
    出口の状態で見る。タスクの停止・削除・部門の無効化・持ち主の無効化も確かめる"""
    run = delivery.run
    destination = Destination.objects.select_related("current_revision", "department").get(
        pk=delivery.destination_revision.destination_id
    )
    sensitive = run.handles_sensitive_data
    problem = destination.problem_for(run.task, sensitive=sensitive)
    if (
        not problem
        and sensitive
        and destination.kind in URL_KINDS
        and not delivery.destination_revision.allows_sensitive_results
    ):
        problem = _("出口が機密の結果を送ってよい先ではありません")
    problem = problem or task_problem(run.task)
    if not problem and run.launch == RunLaunch.CHAT:
        # チャットから起動したRunは、起動した人が今も有効で、固定したSkill・Connectorを今も使えるかも確かめる
        # （authorization.md §6。DBから読み直す）
        launcher = User.objects.filter(pk=run.owner_id).first()
        problem = launcher_inactive(launcher) or (
            launcher_problem(launcher, run.task_revision) if run.task_revision is not None else ""
        )
    return problem
