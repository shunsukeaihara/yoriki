"""対話の出口への送信（design.md §4.7）。

質問・回答の受付・完了等の報告は、結果の通知と同じ配信（Delivery）に載せ、再試行・確認待ち・手での送り直しも同じに
扱う（delivery/tasks.py）。配信には種類（`payload["kind"]`）だけを保存し、メッセージは送るときの状態から作る（質問を
送る前にWebで答えられていれば、答えた形で送る）。

スレッドはRunごとに1つ（ExternalThreadBinding）。最初の投稿のときに行をロックして親メッセージを投稿し、その結果を
保存してからスレッドへ投稿する。親メッセージを送ったか分からなければ、自動では作り直さず確認待ちにする。
"""

import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from plugin import registry
from run.models import ClarificationRequest, ClarificationStatus, Run, RunEvent, RunEventKind, RunStatus

from ..adapters import Outcome
from ..models import (
    SENDABLE_STATUSES,
    AttemptOutcome,
    ChannelConnection,
    ChannelEvent,
    Delivery,
    DeliveryStatus,
    ExternalThreadBinding,
    NotifyContent,
    ThreadStatus,
)
from ..outbox import enqueue_delivery
from ..payload import EVENT_TYPES, build_payload, notification_candidate
from .base import ChannelAdapter, MessageKind, QuestionAnswer, QuestionView, ThreadMessage

logger = logging.getLogger(__name__)

QUESTION = "question"
ANSWERED = "answered"
REPORT = "report"

# 報告に載せる最後の応答の長さ（全体はRunの画面で見る）
MAX_REPORT_TEXT = 2500


def channel_adapter(extension_id: str) -> ChannelAdapter:
    """対話の出口のAdapter。停止中・配備されていないPluginは PluginUnavailable"""
    extension, _spec = registry.get(extension_id)
    adapter = extension.adapter()
    assert isinstance(adapter, ChannelAdapter)
    return adapter


# --- 配信の作成（Runの状態の記録と同じtransactionの中で呼ぶ） ---


def queue_question(run: Run, event: RunEvent, clarification: ClarificationRequest) -> Delivery | None:
    return _queue(run, event, ChannelEvent.QUESTION, {"kind": QUESTION}, clarification=clarification)


def queue_answered(run: Run, event: RunEvent, clarification: ClarificationRequest) -> Delivery | None:
    """質問のメッセージを、確定した回答の形に書き換える"""
    if not Delivery.objects.filter(clarification=clarification, event_type=ChannelEvent.QUESTION).exists():
        return None
    return _queue(run, event, ChannelEvent.ANSWERED, {"kind": ANSWERED}, clarification=clarification)


def queue_report(run: Run, event: RunEvent) -> Delivery | None:
    """完了・失敗・停止の報告（design.md §4.6）"""
    event_type = EVENT_TYPES.get(run.status)
    if event_type is None:
        return None
    current = Run.objects.select_related("task", "task_revision").get(pk=run.pk)
    payload = {"kind": REPORT, **build_payload(current, event, event_type, NotifyContent.STATUS)}
    return _queue(run, event, event_type, payload)


def _queue(run: Run, event: RunEvent, event_type: str, payload: dict, *, clarification=None) -> Delivery | None:
    if run.interaction_revision_id is None:
        return None
    delivery = Delivery.objects.create(
        run=run,
        run_event=event,
        event_type=event_type,
        destination_revision_id=run.interaction_revision_id,
        payload=payload,
        clarification=clarification,
    )
    enqueue_delivery(delivery)
    return delivery


# --- 送信（delivery/tasks.py の send_delivery() から呼ぶ） ---


def deliver_channel(delivery: Delivery, adapter: ChannelAdapter) -> Outcome:
    revision = delivery.destination_revision
    connection = revision.connection
    if connection is None:
        return Outcome(AttemptOutcome.FAILED, error=_("対話の出口に連携の接続がありません"))
    secrets = connection.secret_values()
    kind = delivery.payload.get("kind")
    if kind == ANSWERED:
        return _update_question(delivery, adapter, secrets)
    thread = _thread(delivery, connection, adapter, secrets)
    if isinstance(thread, Outcome):
        return thread
    if kind == QUESTION:
        if delivery.clarification is None:
            return Outcome(AttemptOutcome.FAILED, error=_("対象の確認がありません"))
        message = question_message(delivery.run, delivery.clarification, thread)
        return adapter.post(secrets, thread.channel_id, message, thread_ref=thread.thread_ref)
    outcome = adapter.post(secrets, thread.channel_id, report_message(delivery.run), thread_ref=thread.thread_ref)
    if outcome.outcome == AttemptOutcome.ACCEPTED:
        _close_questions(delivery.run, adapter, secrets, thread)
    return outcome


def _thread(
    delivery: Delivery, connection: ChannelConnection, adapter: ChannelAdapter, secrets: dict[str, str]
) -> ExternalThreadBinding | Outcome:
    """Runのスレッド。なければ親メッセージを投稿して作る。作れなければ、その結果"""
    run = delivery.run
    revision = delivery.destination_revision
    try:
        with transaction.atomic():
            ExternalThreadBinding.objects.get_or_create(
                run=run,
                defaults={
                    "destination_revision": revision,
                    "connection": connection,
                    "channel_id": revision.channel_id,
                },
            )
    except IntegrityError:
        pass  # 同時に作られた
    with transaction.atomic():
        # 行をロックしたまま親メッセージを投稿する（同じRunにスレッドを2つ作らない）
        binding = ExternalThreadBinding.objects.select_for_update().get(run=run)
        if binding.status == ThreadStatus.CREATED:
            return binding
        if binding.status == ThreadStatus.UNKNOWN:
            return Outcome(
                AttemptOutcome.UNKNOWN,
                error=_("スレッドの親メッセージを送ったか分かりません。チャネルを確かめてから送り直してください"),
            )
        outcome = adapter.post(secrets, binding.channel_id, parent_message(run))
        if outcome.outcome != AttemptOutcome.ACCEPTED or not outcome.message_ref:
            if outcome.outcome == AttemptOutcome.UNKNOWN:
                binding.status = ThreadStatus.UNKNOWN
                binding.save(update_fields=["status", "updated"])
            outcome.error = _("スレッドを作れません：%(error)s") % {"error": outcome.error}
            outcome.message_ref = ""
            return outcome
        binding.thread_ref = outcome.message_ref
        binding.status = ThreadStatus.CREATED
        binding.save(update_fields=["thread_ref", "status", "updated"])
    # Runの画面のリンク（行のロックの外で得る。得られなくてもスレッドは使える）
    _save_thread_url(binding, connection, adapter, secrets)
    return binding


def _save_thread_url(
    binding: ExternalThreadBinding, connection: ChannelConnection, adapter: ChannelAdapter, secrets: dict[str, str]
) -> None:
    try:
        url = adapter.thread_url(secrets, connection.workspace_id, binding.channel_id, binding.thread_ref)
    except Exception:
        logger.warning("thread %s: cannot get the url", binding.pk, exc_info=True)
        return
    if not url.startswith("https://") or len(url) > 500:
        return
    binding.thread_url = url
    ExternalThreadBinding.objects.filter(pk=binding.pk).update(thread_url=url)


def _update_question(delivery: Delivery, adapter: ChannelAdapter, secrets: dict[str, str]) -> Outcome:
    clarification = delivery.clarification
    question = _question_delivery(clarification) if clarification is not None else None
    if question is None:
        return Outcome(AttemptOutcome.FAILED, error=_("書き換える質問の配信がありません"))
    if not question.external_ref:
        if question.status in (*SENDABLE_STATUSES, DeliveryStatus.SENDING):
            # 質問の投稿を待つ（送るときに、確定した回答の形で投稿される）
            return Outcome(AttemptOutcome.RETRYABLE, error=_("質問の投稿を待っています"))
        return Outcome(AttemptOutcome.FAILED, error=_("質問を投稿できていないので、書き換えません"))
    thread = ExternalThreadBinding.objects.filter(run=delivery.run).first()
    if thread is None:
        return Outcome(AttemptOutcome.FAILED, error=_("スレッドがありません"))
    assert clarification is not None
    message = question_message(delivery.run, clarification, thread)
    return adapter.update(secrets, thread.channel_id, question.external_ref, message)


def _question_delivery(clarification: ClarificationRequest) -> Delivery | None:
    return Delivery.objects.filter(clarification=clarification, event_type=ChannelEvent.QUESTION).first()


def _close_questions(run: Run, adapter: ChannelAdapter, secrets: dict[str, str], thread: ExternalThreadBinding) -> None:
    """Runが終わったら、答えられないまま残った質問のボタンを消す（できなくても報告は済んでいるので記録だけ）"""
    questions = Delivery.objects.filter(run=run, event_type=ChannelEvent.QUESTION).exclude(external_ref="")
    for delivery in questions.select_related("clarification"):
        clarification = delivery.clarification
        if clarification is None or clarification.status == ClarificationStatus.ANSWERED:
            continue
        try:
            adapter.update(
                secrets, thread.channel_id, delivery.external_ref, question_message(run, clarification, thread)
            )
        except Exception:
            logger.warning("run %s: cannot close the question %s", run.uid, clarification.uid, exc_info=True)


# --- メッセージ ---


def run_link(run: Run) -> str:
    """Runの画面（社内の認証が要る）。公開のURLがなければ空"""
    base = settings.YORIKI_PUBLIC_URL
    if not base or run.task is None:
        return ""
    return f"{base}/tasks/{run.task.uid}/runs/{run.uid}"


def _task_name(run: Run) -> str:
    fixed = ((run.manifest or {}).get("fixed") or {}).get("task") or {}
    return fixed.get("name") or (run.task.name if run.task else "")


def parent_message(run: Run) -> ThreadMessage:
    return ThreadMessage(
        kind=MessageKind.PARENT,
        title=_task_name(run),
        text=_("タスクの実行についての確認と報告は、このスレッドに書きます。"),
        link=run_link(run),
    )


def question_view(clarification: ClarificationRequest, thread: ExternalThreadBinding | None) -> QuestionView:
    selections = (thread.selections if thread else {}).get(clarification.uid) or []
    answer = None
    closed = ""
    if clarification.status == ClarificationStatus.ANSWERED:
        external = clarification.answered_external or {}
        user = clarification.answered_by
        answer = QuestionAnswer(
            name=str(external.get("name") or getattr(user, "display_name", "") or getattr(user, "email", "") or ""),
            via=external.get("provider", "") or "web",
            selected=[a.get("selected", []) for a in clarification.answers],
            text="\n".join(
                v for v in [*(a.get("text", "") for a in clarification.answers), clarification.comment] if v.strip()
            ),
        )
    elif clarification.status == ClarificationStatus.EXPIRED:
        closed = _("回答の期限が過ぎました。")
    elif clarification.status == ClarificationStatus.CANCELLED or clarification.run.status not in (
        RunStatus.WAITING_USER,
        RunStatus.RUNNING,
        RunStatus.QUEUED,
    ):
        closed = _("この質問には、もう回答できません。")
    return QuestionView(
        clarification=clarification.uid,
        questions=clarification.questions,
        selections=selections,
        answer=answer,
        closed=closed,
    )


def question_message(
    run: Run, clarification: ClarificationRequest, thread: ExternalThreadBinding | None
) -> ThreadMessage:
    return ThreadMessage(
        kind=MessageKind.QUESTION,
        text="",
        link=run_link(run),
        question=question_view(clarification, thread),
    )


REPORT_TITLES = {
    RunStatus.COMPLETED: gettext_lazy("✅ 完了しました"),
    RunStatus.FAILED: gettext_lazy("⚠️ 失敗しました"),
    RunStatus.CANCELLED: gettext_lazy("⏹ 停止しました"),
}


def report_message(run: Run) -> ThreadMessage:
    """完了・失敗・停止の報告。状態、最後の応答（通知の書き方の文章があればそれ）、失敗の理由、Runの画面へのリンク"""
    run = Run.objects.select_related("task").get(pk=run.pk)
    title = str(REPORT_TITLES.get(RunStatus(run.status), run.get_status_display()))
    parts = []
    candidate = notification_candidate(run) if run.status == RunStatus.COMPLETED else None
    text = candidate["message"] if candidate else last_response(run)
    if text:
        parts.append(text if len(text) <= MAX_REPORT_TEXT else text[: MAX_REPORT_TEXT - 1] + "…")
    if run.status != RunStatus.COMPLETED and run.error:
        parts.append(_("理由：%(error)s") % {"error": run.error})
    return ThreadMessage(kind=MessageKind.REPORT, title=title, text="\n\n".join(parts), link=run_link(run))


def last_response(run: Run) -> str:
    """Runの最後の応答（サブエージェントのものは除く）"""
    for data in (
        RunEvent.objects.filter(run=run, kind=RunEventKind.ASSISTANT_MESSAGE, subagent__isnull=True)
        .order_by("-sequence")
        .values_list("data", flat=True)[:20]
    ):
        text = (data or {}).get("text", "").strip()
        if text:
            return text
    return ""
