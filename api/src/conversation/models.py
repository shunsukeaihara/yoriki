"""会話と、そのメッセージ・依頼（design.md §5.1、§5.5）。

会話は社員が見る履歴の単位で、その中で実行（Run）が進む。1つのメッセージを必ず1つのRunにはしない。
実行中に届いたメッセージは、今の作業への追加指示か、次の依頼（UserInstruction）として受け付ける。
"""

from typing import Self

from auditlog.registry import auditlog
from django.conf import settings
from django.db import models
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


class ConversationQuerySet(models.QuerySet):
    def owned_by(self, user) -> Self:
        return self.filter(owner=user, deleted=False, purpose=ConversationPurpose.CHAT)


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
    last_posted = models.DateTimeField(_("最終投稿日時"), null=True, blank=True)

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True)
    deleted = models.BooleanField(default=False)

    objects = ConversationQuerySet.as_manager()

    class Meta:
        verbose_name = _("会話")
        verbose_name_plural = verbose_name
        indexes = [models.Index(fields=["owner", "-last_posted"])]

    def __str__(self):
        return self.title or self.uid


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
    """実行中に届いた依頼。今の作業への追加指示はRunnerが安全な区切りで取り込み、次の依頼は先行Runの後に始める"""

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

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("実行中の依頼")
        verbose_name_plural = verbose_name
        indexes = [models.Index(fields=["conversation", "kind", "state"])]

    def __str__(self):
        return self.uid


auditlog.register(Conversation, exclude_fields=["updated", "last_posted"])
