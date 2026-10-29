"""配信のOutbox（design.md §4.3・§4.5）。Runが終わったときに、Runの状態と同じtransactionで配信を作り、commitした後に
Q2へ送る。送れずに残ったものは `delivery.tasks.reconcile_deliveries()` が送り直す。Q2には配信のIDだけを渡す
（backend-conventions.md §8）。
"""

from django.db import transaction
from django_q.tasks import async_task

from run.models import Run, RunEvent

from .models import Delivery
from .payload import EVENT_TYPES, build_payload


def queue_deliveries(run: Run, event: RunEvent) -> Delivery | None:
    """Runの終わりの状態の記録（`Run.set_status()`）と同じtransactionの中で呼ぶ。タスクの版で選んだイベントなら、
    受付時に固定した出口の版への配信を作る"""
    if run.destination_revision_id is None or run.task_revision is None:
        return None
    event_type = EVENT_TYPES.get(run.status)
    if event_type is None or event_type not in run.task_revision.notify_events:
        return None
    # 業務結果は submit_result が先に保存しているので、DBから読み直す（呼び出し元のインスタンスは古いことがある）
    current = Run.objects.select_related("task", "task_revision").get(pk=run.pk)
    payload = build_payload(current, event, event_type, run.task_revision.notify_content)
    delivery = Delivery.objects.create(
        run=current,
        run_event=event,
        event_type=event_type,
        destination_revision_id=run.destination_revision_id,
        payload=payload,
    )
    enqueue_delivery(delivery)
    return delivery


def enqueue_delivery(delivery: Delivery) -> None:
    transaction.on_commit(lambda: send_later(delivery.pk))


def send_later(delivery_id: int) -> None:
    async_task("delivery.tasks.send_delivery", delivery_id, task_name=f"delivery-{delivery_id}")
