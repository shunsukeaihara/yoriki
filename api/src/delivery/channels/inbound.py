"""対話の出口からの受信（design.md §4.6・§4.7）。

受け口（`POST /api/hooks/channels/<種類>/<接続のID>/`）は、接続の資格情報で要求の認証を確かめ、受信した出来事を
ChannelMessageReceipt に記録してすぐに応答する。照合と回答の適用はqclusterで行い、HTTPの受付の中でRunの再開を待たない。
常時接続で受けるもの（DiscordのGatewayの返信）は、channel-listener（listener.py）が同じ store_event() で記録する。

照合は、接続、Workspace、チャネル、スレッドの親メッセージの組で行う。bot自身・ほかのbotの投稿は取り込まない。回答は、
Runが確認待ちで、その確認が回答待ちのときだけ、確認ごとに1回だけ確定する（ClarificationRequest.accept_answer）。
回答者はチャットツールのユーザーとして記録し、Yorikiのユーザーには結び付けない（権限は増えない）。
"""

import logging

from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django_q.tasks import async_task

from plugin.errors import PluginError, PluginUnavailable
from run.errors import AlreadyAnswered
from run.models import ClarificationKind, ClarificationRequest, ClarificationStatus, RunStatus

from ..models import (
    ChannelConnection,
    ChannelMessageReceipt,
    ExternalThreadBinding,
    ReceiptKind,
    ReceiptResult,
    ThreadStatus,
)
from .base import ChannelAdapter, InboundEvent
from .outbound import channel_adapter, question_message

logger = logging.getLogger(__name__)

# 受け付ける本文の上限（Slackのイベント・ボタンは小さい）
MAX_BODY = 1024 * 1024


@csrf_exempt
@require_POST
def receive(request: HttpRequest, provider: str, connection: str) -> HttpResponse:
    """チャットツールからのイベント・ボタンの受け口。認証は接続の資格情報（Slackは Signing secret）で確かめる"""
    found = ChannelConnection.objects.alive().filter(uid=connection).first()
    if found is None or found.extension.rsplit(".", 1)[-1] != provider:
        return HttpResponse(status=404)
    if len(request.body) > MAX_BODY:
        return HttpResponse(status=413)
    try:
        adapter = channel_adapter(found.extension)
    except PluginUnavailable:
        return HttpResponse(status=503)
    return _receive(found, adapter, {k.lower(): v for k, v in request.headers.items()}, request.body)


def _receive(connection: ChannelConnection, adapter: ChannelAdapter, headers: dict[str, str], body: bytes):
    if not adapter.verify(connection.secret_values(), headers, body):
        return HttpResponse(status=401)
    try:
        parsed = adapter.parse(headers, body)
    except PluginError:
        return HttpResponse(status=400)
    if not connection.suspended:  # 停止中の接続は取り込まない（送り直させないよう200を返す）
        for event in parsed.events:
            store_event(connection, event)
    if parsed.response is not None:
        return JsonResponse(parsed.response)
    return HttpResponse(status=200)


def store_event(connection: ChannelConnection, event: InboundEvent) -> bool:
    """受信した出来事を記録し、照合と適用をqclusterへ渡す。同じ出来事を記録済みなら False"""
    action: dict = {}
    if event.clarification:
        action = {"clarification": event.clarification, "question": event.question, "option": event.option}
    if event.reply_token:
        action["reply_token"] = event.reply_token
    try:
        with transaction.atomic():
            receipt = ChannelMessageReceipt.objects.create(
                connection=connection,
                kind=ReceiptKind(event.kind.value),
                dedup_key=event.dedup_key[:200],
                workspace_id=event.workspace_id,
                channel_id=event.channel_id,
                thread_ref=event.thread_ref,
                message_ref=event.message_ref,
                user_id=event.user_id,
                user_name=event.user_name[:200],
                is_bot=event.is_bot,
                text=event.text,
                action=action,
            )
            transaction.on_commit(lambda: process_later(receipt.pk))
    except IntegrityError:
        return False  # 再送（同じ出来事は1回だけ記録する）
    return True


def process_later(receipt_id: int) -> None:
    async_task("delivery.channels.inbound.process_receipt", receipt_id, task_name=f"channel-receipt-{receipt_id}")


# --- 照合と適用（qcluster） ---


class _Done(Exception):
    def __init__(self, result: ReceiptResult, reason: str = "", notice: str = ""):
        super().__init__(reason)
        self.result, self.reason, self.notice = result, reason, notice


def process_receipt(receipt_id: int) -> None:
    receipt = ChannelMessageReceipt.objects.select_related("connection").filter(pk=receipt_id).first()
    if receipt is None or receipt.result != ReceiptResult.RECEIVED:
        return
    connection = receipt.connection
    adapter: ChannelAdapter | None = None
    secrets: dict[str, str] = {}
    thread: ExternalThreadBinding | None = None
    try:
        if receipt.workspace_id != connection.workspace_id:
            raise _Done(ReceiptResult.UNMATCHED, "workspace")
        # Runのスレッドの受信は、取り込まないもの（botの投稿等）もRunに結び付けて残す。Runに結び付かない受信は清掃で
        # 消す（design.md §8）が、Runのスレッドの記録は読み直し（listener.after_ref）と重複の判定に使うので消さない
        thread = (
            ExternalThreadBinding.objects.select_related("run")
            .filter(
                connection=connection,
                channel_id=receipt.channel_id,
                thread_ref=receipt.thread_ref,
                status=ThreadStatus.CREATED,
            )
            .first()
        )
        if thread is not None:
            receipt.run = thread.run
        if not connection.in_service():
            raise _Done(ReceiptResult.IGNORED, "connection suspended")
        if receipt.is_bot or not receipt.user_id or receipt.user_id == connection.bot_user_id:
            raise _Done(ReceiptResult.IGNORED, "bot")
        if thread is None:
            raise _Done(ReceiptResult.UNMATCHED, "thread")
        adapter = channel_adapter(connection.extension)
        secrets = connection.secret_values()
        if not receipt.user_name:
            receipt.user_name = _user_name(adapter, secrets, receipt.user_id)
        _apply(receipt, thread, adapter, secrets)
    except _Done as done:
        receipt.result = done.result
        receipt.reason = done.reason[:300]
        if done.notice and adapter is not None and thread is not None:
            _notify(adapter, secrets, thread, receipt, done.notice)
    except PluginUnavailable:
        receipt.result, receipt.reason = ReceiptResult.IGNORED, "plugin unavailable"
    receipt.processed = timezone.now()
    # 本人への知らせに使うtoken（Discordのinteraction）は、処理を終えたら残さない
    receipt.action = {k: v for k, v in (receipt.action or {}).items() if k != "reply_token"}
    if receipt.run_id is None:
        # Runに結び付かない受信（Yorikiと関係のないスレッドの返信等）は、中身を残さない。行は清掃で消す
        receipt.text, receipt.user_name, receipt.action = "", "", {}
    receipt.save(update_fields=["run", "clarification", "user_name", "text", "action", "result", "reason", "processed"])


def _apply(receipt: ChannelMessageReceipt, thread: ExternalThreadBinding, adapter: ChannelAdapter, secrets) -> None:
    run = thread.run
    open_question = (
        ClarificationRequest.objects.filter(run=run, kind=ClarificationKind.QUESTION, status=ClarificationStatus.OPEN)
        .order_by("-created")
        .first()
    )
    waiting = open_question is not None and run.status == RunStatus.WAITING_USER
    if receipt.kind == ReceiptKind.REPLY:
        if not waiting:
            raise _Done(
                ReceiptResult.REJECTED,
                "no open question",
                _(
                    "回答を待っている質問がないので、この返信は受け付けませんでした（このスレッドでは追加の指示を受け付けません）。"
                ),
            )
        assert open_question is not None
        receipt.clarification = open_question
        if not receipt.text.strip():
            # ファイルだけの共有等。回答の添付ファイルにはまだ対応していない（design.md §4.6）
            raise _Done(
                ReceiptResult.REJECTED,
                "empty reply",
                _(
                    "本文のない返信は回答として受け付けません（ファイルの添付にはまだ対応していません）。文で答えてください。"
                ),
            )
        selections = thread.selections.get(open_question.uid) or []
        answers = [
            {"selected": selections[i] if i < len(selections) else [], "text": ""}
            for i in range(len(open_question.questions))
        ]
        _answer(receipt, thread, open_question, answers, receipt.text.strip())
        return
    target_uid = (receipt.action or {}).get("clarification", "")
    if not waiting or open_question is None or open_question.uid != target_uid:
        raise _Done(ReceiptResult.REJECTED, "closed question", _("この質問には、もう回答できません。"))
    receipt.clarification = open_question
    if receipt.kind == ReceiptKind.SUBMIT:
        selections = thread.selections.get(open_question.uid) or []
        if not any(selections):
            raise _Done(
                ReceiptResult.REJECTED,
                "nothing selected",
                _("選択肢を選んでから「この内容で回答」を押すか、このスレッドに返信して答えてください。"),
            )
        answers = [
            {"selected": selections[i] if i < len(selections) else [], "text": ""}
            for i in range(len(open_question.questions))
        ]
        _answer(receipt, thread, open_question, answers, "")
        return
    _select(receipt, thread, open_question, adapter, secrets)


def _select(receipt, thread: ExternalThreadBinding, question: ClarificationRequest, adapter, secrets) -> None:
    index, option = receipt.action.get("question", -1), receipt.action.get("option", -1)
    questions = question.questions
    if not (0 <= index < len(questions)) or not (0 <= option < len(questions[index]["options"])):
        raise _Done(ReceiptResult.REJECTED, "invalid option")
    label = questions[index]["options"][option]["label"]
    if len(questions) == 1 and not questions[0].get("multi_select"):
        # 1問で単一選択なら、押した選択で確定する
        _answer(receipt, thread, question, [{"selected": [label], "text": ""}], "")
        return
    with transaction.atomic():
        current = ExternalThreadBinding.objects.select_for_update().get(pk=thread.pk)
        selections = current.selections.get(question.uid) or [[] for _q in questions]
        selections = [list(s) for s in selections] + [[] for _q in questions[len(selections) :]]
        chosen = selections[index]
        if questions[index].get("multi_select"):
            selections[index] = [v for v in chosen if v != label] if label in chosen else [*chosen, label]
        else:
            selections[index] = [] if chosen == [label] else [label]
        current.selections = {**current.selections, question.uid: selections}
        current.save(update_fields=["selections", "updated"])
    thread.selections = current.selections
    receipt.result = ReceiptResult.SELECTED
    # 選んだ状態をメッセージに出す（できなくても選択は保持している）
    try:
        adapter.update(secrets, thread.channel_id, receipt.message_ref, question_message(thread.run, question, thread))
    except Exception:
        logger.warning("receipt %s: cannot update the question message", receipt.pk, exc_info=True)


def _answer(receipt, thread: ExternalThreadBinding, question: ClarificationRequest, answers, comment: str) -> None:
    external = {
        "provider": receipt.connection.extension.rsplit(".", 1)[-1],
        "user_id": receipt.user_id,
        "name": receipt.user_name or receipt.user_id,
    }
    try:
        question.accept_answer(answers, comment, None, external=external)
    except AlreadyAnswered as exc:
        raise _Done(ReceiptResult.REJECTED, "already answered", _("この質問には、すでに回答がありました。")) from exc
    with transaction.atomic():
        current = ExternalThreadBinding.objects.select_for_update().get(pk=thread.pk)
        if question.uid in current.selections:
            current.selections = {k: v for k, v in current.selections.items() if k != question.uid}
            current.save(update_fields=["selections", "updated"])
    receipt.result = ReceiptResult.APPLIED


def _user_name(adapter: ChannelAdapter, secrets: dict[str, str], user_id: str) -> str:
    try:
        return adapter.user_name(secrets, user_id)[:200]
    except Exception:
        logger.warning("cannot read the name of %s", user_id, exc_info=True)
        return ""


def _notify(adapter: ChannelAdapter, secrets, thread: ExternalThreadBinding, receipt, text: str) -> None:
    try:
        token = (receipt.action or {}).get("reply_token", "")
        adapter.notify_user(secrets, thread.channel_id, thread.thread_ref, receipt.user_id, text, reply_token=token)
    except Exception:
        logger.warning("receipt %s: cannot send the notice", receipt.pk, exc_info=True)
