"""配信が今も出口へ出してよいかの確かめ直し（authorization.md §6・§7.2）。送る直前と、出口へ渡したファイルの取得の
たびに使う"""

from django.utils.translation import gettext as _

from task.checks import task_problem

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
    return problem or task_problem(run.task)
