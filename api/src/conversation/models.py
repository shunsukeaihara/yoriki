"""会話と、そのメッセージ・依頼（design.md §5.1、§5.5）。

会話は社員が見る履歴の単位で、その中で実行（Run）が進む。1つのメッセージを必ず1つのRunにはしない。
実行中に届いたメッセージは、今の作業への追加指示か、次の依頼（UserInstruction）として受け付ける。
"""

from typing import Self

from auditlog.context import set_actor
from auditlog.registry import auditlog
from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from utils.fields import NanoidField


class MessageRole(models.IntegerChoices):
    USER = 1, _("ユーザー")
    AGENT = 2, _("エージェント")
    SYSTEM = 3, _("システム")


class ConversationPurpose(models.IntegerChoices):
    CHAT = 1, _("会話")
    # Draftの評価のRunごとに作る会話（design.md §3.7.1）。会話の一覧には出さない
    EVALUATION = 2, _("Skillの評価")
    # タスクのRunごとに作る会話（design.md §5.1）。会話の一覧には出さない
    TASK = 3, _("タスク")
    # タスクの修正チャット（design.md §11.11）。1つのタスクに結び付き、会話の一覧にも出る
    TASK_FIX = 4, _("タスクの修正")


# 利用者が会話の画面で開く会話の用途
OWNED_PURPOSES = (ConversationPurpose.CHAT, ConversationPurpose.TASK_FIX)


class ConversationQuerySet(models.QuerySet):
    def owned_by(self, user) -> Self:
        """持ち主が開ける会話。アーカイブした会話も含む（発言するとアーカイブを解除する。design.md §8）"""
        return self.filter(owner=user, deleted=False, purpose__in=OWNED_PURPOSES)


class Conversation(models.Model):
    uid = NanoidField(unique=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="conversations", verbose_name=_("所有者")
    )
    model_profile = models.ForeignKey(
        "llm.ModelProfile", on_delete=models.PROTECT, related_name="+", verbose_name=_("モデル設定")
    )
    # 空ならモデル設定の既定のeffort。値を入れると、利用者がその値に固定したことになる（design.md §4.8）
    effort = models.CharField(_("effort"), max_length=50, blank=True)
    # 個人情報や機密情報を扱う会話は、ローカルLLMのモデルだけを使う（design.md §4冒頭）
    handles_sensitive_data = models.BooleanField(_("個人情報・機密情報を扱う"), default=False)
    title = models.CharField(_("タイトル"), max_length=200, blank=True)
    purpose = models.PositiveSmallIntegerField(
        _("用途"), choices=ConversationPurpose.choices, default=ConversationPurpose.CHAT
    )
    # 修正チャットで直すタスク（用途が「タスクの修正」の会話だけ。design.md §11.11）
    task = models.ForeignKey(
        "task.TaskDefinition",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="fix_chats",
        verbose_name=_("修正するタスク"),
    )
    last_posted = models.DateTimeField(_("最終投稿日時"), null=True, blank=True)
    # アーカイブした日時。会話の一覧から外し、アーカイブの一覧に出す。発言すると空に戻す（design.md §8）
    archived = models.DateTimeField(_("アーカイブ日時"), null=True, blank=True)
    # アーカイブ・削除でワークスペースを消すと決めた日時。これより前に始めたRunのsnapshotは戻さず、全部止まったら消す
    # （Checkpoint.discard_closed()）。次の依頼は空のワークスペースで始め、前のファイルがないことをモデルに伝える
    workspace_discarded = models.DateTimeField(_("ワークスペースを消した日時"), null=True, blank=True)

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True)
    deleted = models.BooleanField(default=False)

    objects = ConversationQuerySet.as_manager()

    class Meta:
        verbose_name = _("会話")
        verbose_name_plural = verbose_name
        indexes = [models.Index(fields=["owner", "-last_posted"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(purpose=ConversationPurpose.TASK_FIX, task__isnull=False)
                | (~models.Q(purpose=ConversationPurpose.TASK_FIX) & models.Q(task__isnull=True)),
                name="conversation_fix_task",
            ),
        ]

    def __str__(self):
        return self.title or self.uid

    @property
    def is_task_fix(self) -> bool:
        return self.purpose == ConversationPurpose.TASK_FIX

    @transaction.atomic
    def close(self, *, delete: bool, actor) -> None:
        """アーカイブ（`delete` がFalse）か論理削除をする（design.md §8）。会話の一覧から外して動いているRunに停止を
        依頼し、全部止まったらワークスペースを消す。履歴・入力・成果物は残す。操作は監査ログ（auditlog）に残る"""
        from run.models import CANCELLABLE_STATUSES, Checkpoint, Run  # noqa: PLC0415  runがconversationを参照するため

        # ロックの順序は 会話 → Run（Run.locked()）
        locked = Conversation.objects.select_for_update().get(pk=self.pk)
        reason = _("会話を削除しました") if delete else _("会話をアーカイブしました")
        # 待っている次の依頼と、まだ取り込んでいない発言は取り消す（止めたRunの終わりで新しいRunを始めない）
        UserInstruction.objects.filter(conversation=locked, state=InstructionState.RECEIVED).update(
            state=InstructionState.DISCARDED, updated=timezone.now()
        )
        for run in Run.objects.filter(conversation=locked, status__in=CANCELLABLE_STATUSES):
            run.request_cancel(str(reason), actor=actor)
        now = timezone.now()
        self.workspace_discarded = now
        fields = ["workspace_discarded", "updated"]
        if delete:
            self.deleted = True
            fields.append("deleted")
        else:
            self.archived = now
            fields.append("archived")
        with set_actor(actor):
            self.save(update_fields=fields)
        pk = self.pk
        transaction.on_commit(lambda: Checkpoint.discard_closed(pk))


class ConversationMessage(models.Model):
    """会話のメッセージ。利用者の発言と、エージェントの最後の応答。作成後に変更しない"""

    uid = NanoidField(unique=True)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    run = models.ForeignKey(
        "run.Run", on_delete=models.SET_NULL, null=True, blank=True, related_name="messages", verbose_name=_("実行")
    )
    # 外部スレッドからの投稿では空になりうる
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, verbose_name=_("送信者")
    )
    role = models.PositiveSmallIntegerField(_("送信元の種別"), choices=MessageRole.choices)
    body = models.TextField(_("本文"), blank=True)
    # 利用者が送った添付。送ったときに /workspace/input の中の名前を決める（Artifact.attach()）
    attachments = models.ManyToManyField(
        "artifact.Artifact", blank=True, related_name="messages", verbose_name=_("添付")
    )
    # 利用者が入力欄の `/` で指定したSkill（[{"id", "name"}]。送ったときの名前を残す）。このメッセージで始めるRunの
    # 開始時に、その時点の公開中の版へ解決する（design.md §3.2）
    skills = models.JSONField(_("指定したSkill"), default=list, blank=True)
    # 利用者が入力欄の `@` で指定したConnector（[{"id", "name"}]。送ったときの名前を残す）。このメッセージで始めるRunの
    # 開始時に、使えるかを確かめて固定する（harness.md §10.2）
    connectors = models.JSONField(_("指定したConnector"), default=list, blank=True)
    # Skillの編集の依頼（{"id", "name"}。新しいSkillを作る依頼ではidが空）。空でなければ、このメッセージで始めるRunは
    # Skillの編集のRunになる（入力欄の `/` での明示か、request_skill_edit の承認。design.md §11.6）
    skill_edit = models.JSONField(_("Skillの編集の依頼"), default=dict, blank=True)
    # チャットから起動したタスクのRun（authorization.md §6）。起動した会話に、そのRunを指すカードとして出す。タスクの
    # Runは会話の文脈を引き継がず、このメッセージも会話のRunの文脈には入らない（文脈はRunEventから組み立てる）
    task_run = models.ForeignKey(
        "run.Run",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="launch_messages",
        verbose_name=_("起動したタスクのRun"),
    )
    # 画面が送るID。再送されたメッセージを二重に受け付けない（design.md §5.5）
    client_message_id = models.CharField(_("クライアントのメッセージID"), max_length=64, blank=True)

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("会話メッセージ")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "client_message_id"],
                condition=~models.Q(client_message_id=""),
                name="uniq_conversation_client_message",
            ),
        ]

    def __str__(self):
        return f"{self.conversation_id}:{self.pk}"


class InstructionKind(models.IntegerChoices):
    CURRENT = 1, _("今の作業への追加指示")
    NEXT = 2, _("次の依頼")


class InstructionState(models.IntegerChoices):
    RECEIVED = 1, _("受付済み")
    APPLIED = 2, _("反映済み")
    STARTED = 3, _("開始済み")  # 次の依頼から新しいRunを始めた
    DISCARDED = 9, _("取消")


class UserInstruction(models.Model):
    """実行中に届いた依頼。親へ渡す発言はRunnerが安全な区切りで取り込み、編集のRunの依頼は先行Runの後に始める"""

    uid = NanoidField(unique=True)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="instructions")
    message = models.OneToOneField(ConversationMessage, on_delete=models.CASCADE, related_name="instruction")
    # 追加指示では対象のRun。次の依頼では、始めたRun
    run = models.ForeignKey(
        "run.Run", on_delete=models.SET_NULL, null=True, blank=True, related_name="instructions", verbose_name=_("実行")
    )
    kind = models.PositiveSmallIntegerField(_("種類"), choices=InstructionKind.choices)
    state = models.PositiveSmallIntegerField(
        _("反映状態"), choices=InstructionState.choices, default=InstructionState.RECEIVED
    )
    applied = models.DateTimeField(_("反映日時"), null=True, blank=True)
    # 本文と添付は確認への回答として渡した。指定したSkill・Connectorだけを取り込む（design.md §5.5）
    is_answer = models.BooleanField(_("確認への回答"), default=False)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("実行中の依頼")
        verbose_name_plural = verbose_name
        indexes = [models.Index(fields=["conversation", "kind", "state"])]

    def __str__(self):
        return self.uid


auditlog.register(Conversation, exclude_fields=["updated", "last_posted"])
