"""配信の送信と再試行（Delivery Dispatcher。design.md §4.5）。

1つの配信を同時に2つのWorkerが送らないよう、送る前に配信の行をロックして「配信中」にし、持ち時間（lease）を置く。
送信はtransactionの外で行い、結果を記録する。一時的な障害（つながらない・429・再試行できる5xx等）は間隔を延ばしながら
上限まで送り直す。送った後に応答が失われた・Workerが止まって持ち時間が切れた等、届いたか分からないときは、自動では
送り直さず「確認待ち」にする（authorization.md §6）。受け手の event_id による重複の判定は補助で、exactly-onceではない。
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from . import in_app
from .adapters import Outcome, adapter_for
from .checks import send_problem
from .models import SENDABLE_STATUSES, AttemptOutcome, Delivery, DeliveryAttempt, DeliveryStatus, DestinationKind
from .outbox import send_later
from .payload import FileLinksUnavailable, with_file_links

logger = logging.getLogger(__name__)

# 送り直すまでの間隔（秒）。この数＋1回で諦める（合わせて約4時間半）
BACKOFF_SECONDS = (60, 300, 900, 3600, 10800)
MAX_ATTEMPTS = len(BACKOFF_SECONDS) + 1
# 送り先が指定した間隔（Retry-After）の上限
MAX_RETRY_AFTER = 3 * 3600
# 配信中の持ち時間（送信の時間切れより十分長くする）
LEASE = timedelta(minutes=2)
# 作ってからこれより長く配信待ちのままなら、Q2へ送れなかったとみなして送り直す
PENDING_GRACE = timedelta(seconds=30)


def send_delivery(delivery_id: int) -> None:
    claimed = _claim(delivery_id)
    if claimed is None:
        return
    delivery, attempt, url, secret = claimed
    revision = delivery.destination_revision
    try:
        if revision.destination.kind == DestinationKind.IN_APP:
            # 画面の通知は外へ送らず、通知欄に載せる（ファイルのURLは発行しない）
            outcome = in_app.notify(delivery)
        else:
            # ファイルのURLは送るたびに発行する（期限が短いので、送り直しでも新しくする。design.md §4.5）
            payload = with_file_links(delivery.payload, attempt)
            outcome = adapter_for(revision.destination.kind).deliver(
                url=url, secret=secret, payload=payload, attempt=attempt.number
            )
    except FileLinksUnavailable as exc:
        outcome = Outcome(AttemptOutcome.FAILED, error=str(exc))
    except Exception:
        logger.exception("delivery %s: unexpected error", delivery.uid)
        outcome = Outcome(AttemptOutcome.UNKNOWN, error=_("送信の途中で内部のエラーが起きました"))
    _record(delivery.pk, attempt.pk, outcome)


@transaction.atomic
def _claim(delivery_id: int) -> tuple[Delivery, DeliveryAttempt, str, str] | None:
    """送る番の配信を「配信中」にする。送らないなら None（送る前に出口が使えなくなっていたら取消にする）"""
    delivery = (
        Delivery.objects.select_for_update(skip_locked=True, of=("self",))
        .select_related("destination_revision__destination", "run__task__department", "run__task__owner")
        .filter(pk=delivery_id, status__in=SENDABLE_STATUSES)
        .first()
    )
    now = timezone.now()
    if delivery is None or (delivery.next_attempt is not None and delivery.next_attempt > now):
        return None
    # 送る直前に、出口とタスクが今も使えるかを確かめ直す（authorization.md §6・§7.2）
    problem = send_problem(delivery)
    if problem:
        delivery.status = DeliveryStatus.CANCELLED
        delivery.last_error = problem[:500]
        delivery.next_attempt = None
        delivery.save(update_fields=["status", "last_error", "next_attempt", "updated"])
        logger.info("delivery %s cancelled: %s", delivery.uid, problem)
        return None
    delivery.status = DeliveryStatus.SENDING
    delivery.attempt_count += 1
    delivery.lease_expires = now + LEASE
    delivery.save(update_fields=["status", "attempt_count", "lease_expires", "updated"])
    attempt = DeliveryAttempt.objects.create(delivery=delivery, number=delivery.attempt_count)
    revision = delivery.destination_revision
    return delivery, attempt, revision.url.reveal(), revision.destination.signing_secret.reveal()


@transaction.atomic
def _record(delivery_id: int, attempt_id: int, outcome: Outcome) -> None:
    delivery = Delivery.objects.select_for_update().get(pk=delivery_id)
    attempt = DeliveryAttempt.objects.select_for_update().get(pk=attempt_id)
    now = timezone.now()
    attempt.outcome = outcome.outcome
    attempt.http_status = outcome.http_status
    attempt.error = outcome.error[:500]
    attempt.finished = now
    attempt.save(update_fields=["outcome", "http_status", "error", "finished"])
    if delivery.attempt_count != attempt.number:
        return  # 後の試行がもう始まっている（記録だけ残す）
    if delivery.status != DeliveryStatus.SENDING:
        # 持ち時間が切れて確認待ちにされた（その後に人が送り直しを依頼し、まだ送っていないものを含む）後に戻った。
        # 受付が確かめられたなら受付確認済みにし、重ねて送らない。それ以外は記録だけ残す
        if outcome.outcome == AttemptOutcome.ACCEPTED and delivery.status in (
            DeliveryStatus.UNKNOWN,
            DeliveryStatus.PENDING,
        ):
            delivery.status = DeliveryStatus.DELIVERED
            delivery.delivered = now
            delivery.last_error = ""
            delivery.next_attempt = None
            delivery.save(update_fields=["status", "delivered", "last_error", "next_attempt", "updated"])
        return
    delivery.lease_expires = None
    delivery.last_error = outcome.error[:500]
    delivery.next_attempt = None
    if outcome.outcome == AttemptOutcome.ACCEPTED:
        delivery.status = DeliveryStatus.DELIVERED
        delivery.delivered = now
    elif (
        outcome.outcome == AttemptOutcome.RETRYABLE
        and (sent := delivery.attempt_count - delivery.retry_from) < MAX_ATTEMPTS
    ):
        delay = BACKOFF_SECONDS[sent - 1]
        if outcome.retry_after is not None:
            delay = max(delay, min(outcome.retry_after, MAX_RETRY_AFTER))
        delivery.status = DeliveryStatus.RETRY_WAIT
        delivery.next_attempt = now + timedelta(seconds=delay)
    elif outcome.outcome == AttemptOutcome.UNKNOWN:
        delivery.status = DeliveryStatus.UNKNOWN
    else:
        delivery.status = DeliveryStatus.FAILED
    delivery.save(update_fields=["status", "lease_expires", "last_error", "next_attempt", "delivered", "updated"])
    if delivery.status != DeliveryStatus.DELIVERED:
        logger.warning(
            "delivery %s attempt %s: %s %s", delivery.uid, attempt.number, delivery.get_status_display(), outcome.error
        )


def reconcile_deliveries() -> None:
    """送れずに残った配信を送り、持ち時間の切れた配信を確認待ちにする。Q2のScheduleで1分ごとに呼ぶ"""
    now = timezone.now()
    for pk in list(
        Delivery.objects.filter(status=DeliveryStatus.SENDING, lease_expires__lt=now).values_list("pk", flat=True)[:100]
    ):
        _expire(pk)
    due = Delivery.objects.filter(
        status=DeliveryStatus.PENDING, created__lt=now - PENDING_GRACE
    ) | Delivery.objects.filter(status=DeliveryStatus.RETRY_WAIT, next_attempt__lte=now)
    for pk in list(due.order_by("pk").values_list("pk", flat=True)[:100]):
        send_later(pk)


@transaction.atomic
def _expire(delivery_id: int) -> None:
    delivery = Delivery.objects.select_for_update().get(pk=delivery_id)
    if delivery.status != DeliveryStatus.SENDING or delivery.lease_expires is None:
        return
    if delivery.lease_expires >= timezone.now():
        return  # その間に送り終えた
    delivery.status = DeliveryStatus.UNKNOWN
    delivery.lease_expires = None
    delivery.last_error = _("送信の途中でWorkerが止まりました。届いたか分からないので、自動では送り直しません")
    delivery.save(update_fields=["status", "lease_expires", "last_error", "updated"])
    # 終了日時は埋めない（元のWorkerがまだ送っているかもしれない。戻ったときに _record() が埋め、それまでは人の
    # 送り直しも受け付けない。Delivery.retry()）
    DeliveryAttempt.objects.filter(delivery=delivery, number=delivery.attempt_count, finished__isnull=True).update(
        outcome=AttemptOutcome.UNKNOWN, error=delivery.last_error
    )
    logger.warning("delivery %s: lease expired while sending", delivery.uid)
