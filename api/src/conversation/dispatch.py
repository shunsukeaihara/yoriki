"""会話のメッセージの受付と、Runの開始（design.md §5.1、§5.5）"""

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from run.dispatch import enqueue_run
from run.models import (
    ACTIVE_STATUSES,
    ClarificationKind,
    ClarificationRequest,
    ClarificationStatus,
    Run,
    RunEvent,
    RunEventKind,
    RunStatus,
)

from .models import Conversation, ConversationMessage, InstructionKind, InstructionState, MessageRole, UserInstruction


class MessageMode:
    AUTO = "auto"  # 実行中なら今の作業への追加指示、確認待ちなら回答、それ以外は新しい依頼
    NEXT = "next"  # 実行中なら、終わった後に始める次の依頼


class CannotStart(Exception):
    pass


def resolve_effort(conversation: Conversation, version) -> str:
    """利用者が固定したeffortがモデルの受け付ける値ならそれを、なければモデルの既定を使う"""
    if conversation.effort and conversation.effort in version.effort_values:
        return conversation.effort
    return version.default_effort


def active_run(conversation: Conversation) -> Run | None:
    return Run.objects.select_for_update().filter(conversation=conversation, status__in=ACTIVE_STATUSES).first()


@transaction.atomic
def post_message(conversation: Conversation, *, body: str, client_message_id: str, mode: str, actor):
    """利用者のメッセージを保存し、新しいRun・追加指示・確認への回答・次の依頼のどれかとして受け付ける"""
    conversation = Conversation.objects.select_for_update().get(pk=conversation.pk)
    if client_message_id:
        sent = ConversationMessage.objects.filter(
            conversation=conversation, client_message_id=client_message_id
        ).first()
        if sent is not None:
            return sent  # 再送。二重に受け付けない
    message = ConversationMessage.objects.create(
        conversation=conversation, sender=actor, role=MessageRole.USER, body=body, client_message_id=client_message_id
    )
    conversation.last_posted = timezone.now()
    fields = ["last_posted", "updated"]
    if not conversation.title:
        conversation.title = body.strip().splitlines()[0][:50] if body.strip() else ""
        fields.append("title")
    conversation.save(update_fields=fields)

    run = active_run(conversation)
    if run is None:
        start_run(conversation, message, actor)
        return message
    message.run = run
    message.save(update_fields=["run"])
    question = ClarificationRequest.objects.filter(
        run=run, status=ClarificationStatus.OPEN, kind=ClarificationKind.QUESTION
    ).first()
    if mode == MessageMode.AUTO and question is not None and run.status == RunStatus.WAITING_USER:
        # 確認待ちのときのメッセージは、その確認への自由記述の回答にする
        question.accept_answer([{"selected": [], "text": ""} for _q in question.questions], body, actor)
        return message
    kind = (
        InstructionKind.NEXT
        if mode == MessageMode.NEXT or run.status == RunStatus.CANCELLING
        else InstructionKind.CURRENT
    )
    UserInstruction.objects.create(
        conversation=conversation, message=message, run=run if kind == InstructionKind.CURRENT else None, kind=kind
    )
    return message


def start_run(conversation: Conversation, message: ConversationMessage, actor, *, parent: Run | None = None) -> Run:
    """呼び出し側のtransactionの中で呼ぶ。モデル設定の版とeffortを固定し、最初の発言を記録して実行を依頼する"""
    profile = conversation.model_profile
    version = profile.current_version
    if version is None or not profile.is_active or profile.deleted:
        raise CannotStart(_("この会話のモデル設定は使えません。別のモデルを選んでください。"))
    if conversation.handles_sensitive_data and not version.is_local:
        raise CannotStart(_("個人情報や機密情報を扱う会話では、ローカルLLMのモデルを選んでください。"))
    effort = resolve_effort(conversation, version)
    run = Run.objects.create(
        conversation=conversation,
        parent=parent,
        owner=conversation.owner,
        model_version=version,
        effort=effort,
        handles_sensitive_data=conversation.handles_sensitive_data,
        manifest={
            "fixed": {
                "model_version": version.pk,
                "effort": effort,
                "handles_sensitive_data": conversation.handles_sensitive_data,
            },
            "appended": [],
        },
    )
    message.run = run
    message.save(update_fields=["run"])
    RunEvent.record(run, RunEventKind.STATUS, {"status": RunStatus.QUEUED}, actor=actor)
    RunEvent.record(run, RunEventKind.USER_MESSAGE, {"text": message.body, "message": message.uid}, actor=actor)
    enqueue_run(run)
    return run


def start_next_request(conversation: Conversation) -> Run | None:
    """先行Runが終わった後に、待っている次の依頼を1つ始める（Runの状態を変えるtransactionの中で呼ぶ）"""
    if Run.objects.filter(conversation=conversation, status__in=ACTIVE_STATUSES).exists():
        return None
    instruction = (
        UserInstruction.objects.select_for_update(of=("self",))
        .filter(conversation=conversation, kind=InstructionKind.NEXT, state=InstructionState.RECEIVED)
        .select_related("message", "message__sender")
        .order_by("pk")
        .first()
    )
    if instruction is None:
        return None
    try:
        run = start_run(conversation, instruction.message, instruction.message.sender)
    except CannotStart:
        instruction.state = InstructionState.DISCARDED
        instruction.save(update_fields=["state", "updated"])
        return None
    instruction.state = InstructionState.STARTED
    instruction.run = run
    instruction.save(update_fields=["state", "run", "updated"])
    return run
