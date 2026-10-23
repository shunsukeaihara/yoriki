from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from artifact.models import Artifact, ArtifactKind
from artifact.serializers import ArtifactSerializer
from llm.models import ModelProfile
from llm.serializers import AppModelProfileSerializer
from run.models import ACTIVE_STATUSES, Run
from run.serializers import RunSerializer
from utils.fields import NormalizedTextField

from .dispatch import CannotStart, MessageMode, post_message
from .models import Conversation, ConversationMessage, InstructionKind, InstructionState, MessageRole


class ConversationSerializer(serializers.ModelSerializer):
    """自分の会話。モデル設定を省くと既定のモデル設定にする。実行中に変えると、次のステップから切り替わる"""

    id = serializers.CharField(source="uid", read_only=True)
    model_profile: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(
        slug_field="uid",
        queryset=ModelProfile.objects.filter(
            deleted=False, is_active=True, current_version__connection__is_active=True
        ).select_related("current_version__connection"),
        required=False,
    )
    model_profile_info = AppModelProfileSerializer(source="model_profile", read_only=True)
    effort = serializers.CharField(
        max_length=50, required=False, allow_blank=True, help_text=_("空ならモデルの既定。値を入れると固定する")
    )
    active_run = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id",
            "model_profile",
            "model_profile_info",
            "effort",
            "handles_sensitive_data",
            "title",
            "last_posted",
            "active_run",
            "created",
            "updated",
        ]
        read_only_fields = ["last_posted", "created", "updated"]

    def get_fields(self):
        fields = super().get_fields()
        if self.instance is not None:
            # 扱うデータの区分は、会話を始めるときのウィザードで決め、後から変えない
            fields["handles_sensitive_data"].read_only = True
        return fields

    @extend_schema_field(RunSerializer(allow_null=True))
    def get_active_run(self, obj: Conversation) -> dict[str, Any] | None:
        run = obj.runs.filter(status__in=ACTIVE_STATUSES).select_related("model_version__profile").first()
        return RunSerializer(run, context=self.context).data if run else None

    def validate(self, attrs):
        profile = attrs.get("model_profile") or getattr(self.instance, "model_profile", None)
        if profile is None:
            profile = ModelProfile.objects.filter(deleted=False, is_active=True, is_default=True).first()
            if profile is None:
                raise serializers.ValidationError({"model_profile": _("モデル設定を選んでください")})
            attrs["model_profile"] = profile
        sensitive = attrs.get("handles_sensitive_data", getattr(self.instance, "handles_sensitive_data", False))
        version = profile.current_version
        if version is None:
            raise serializers.ValidationError({"model_profile": _("このモデル設定は使えません")})
        if sensitive and not version.is_local:
            raise serializers.ValidationError(
                {"model_profile": _("個人情報や機密情報を扱う会話では、ローカルLLMのモデルを選んでください")}
            )
        effort = attrs.get("effort", getattr(self.instance, "effort", ""))
        if "model_profile" in attrs and "effort" not in attrs:
            effort = attrs["effort"] = ""  # モデルを変えたら、そのモデルの既定から始める
        if effort and effort not in version.effort_values:
            raise serializers.ValidationError({"effort": _("このモデルが受け付けるeffortの値から選んでください")})
        return attrs

    def update(self, instance: Conversation, validated_data) -> Conversation:
        actor = validated_data.pop("actor", None)
        with transaction.atomic():
            instance = super().update(instance, validated_data)
            if "model_profile" in validated_data or "effort" in validated_data:
                self._switch_active_run(instance, actor)
        return instance

    @staticmethod
    def _switch_active_run(conversation: Conversation, actor) -> None:
        """実行中のRunにも、利用者の明示した切り替えとして反映する（design.md §4.8）"""
        from .dispatch import resolve_effort  # noqa: PLC0415  ここでだけ使う

        run = Run.objects.select_for_update().filter(conversation=conversation, status__in=ACTIVE_STATUSES).first()
        if run is None:
            return
        version = conversation.model_profile.current_version
        if version is None:
            return
        effort = resolve_effort(conversation, version)
        if run.model_version_id != version.pk or run.effort != effort:
            run.switch_model(version, effort, actor=actor, trigger="user")


class InstructionInfoSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=InstructionKind.choices, read_only=True)
    state = serializers.ChoiceField(choices=InstructionState.choices, read_only=True)
    state_display = serializers.CharField(source="get_state_display", read_only=True)


class MessageSerializer(serializers.ModelSerializer):
    """会話のメッセージ。投稿すると、新しい依頼・今の作業への追加指示・確認への回答・次の依頼のどれかになる"""

    id = serializers.CharField(source="uid", read_only=True)
    run: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(
        slug_field="uid", read_only=True, allow_null=True
    )
    # 外部スレッドからの投稿とエージェントの応答では空
    sender: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(
        slug_field="uid", read_only=True, allow_null=True
    )
    role = serializers.ChoiceField(choices=MessageRole.choices, read_only=True)
    body = NormalizedTextField(max_length=20000, allow_blank=True, required=False, default="")
    attachments: "serializers.SlugRelatedField[Artifact]" = serializers.SlugRelatedField(
        slug_field="uid",
        many=True,
        required=False,
        queryset=Artifact.objects.none(),
        help_text=_("送る添付（この会話にアップロードして、まだ送っていないもの）"),
    )
    attachments_info = ArtifactSerializer(source="attachments", many=True, read_only=True)
    client_message_id = serializers.CharField(max_length=64, required=False, allow_blank=True, write_only=True)
    mode = serializers.ChoiceField(
        choices=[(MessageMode.AUTO, _("自動")), (MessageMode.NEXT, _("次の依頼"))],
        default=MessageMode.AUTO,
        write_only=True,
        help_text=_("auto：実行中なら今の作業への追加指示、確認待ちなら回答。next：今の作業の後に始める"),
    )
    instruction = InstructionInfoSerializer(read_only=True, allow_null=True)

    class Meta:
        model = ConversationMessage
        fields = [
            "id",
            "run",
            "sender",
            "role",
            "body",
            "attachments",
            "attachments_info",
            "client_message_id",
            "mode",
            "instruction",
            "created",
        ]
        read_only_fields = ["created"]

    def get_fields(self):
        fields = super().get_fields()
        # 添付に指定できるのは、ログインユーザーがこの会話にアップロードして、まだ送っていないファイルだけ
        request, view = self.context.get("request"), self.context.get("view")
        attachments = fields["attachments"]
        # スキーマの生成（swagger_fake_view）では、URLの会話がないので絞り込まない（選べるものはない）
        if (
            request is not None
            and view is not None
            and not getattr(view, "swagger_fake_view", False)
            and isinstance(attachments, serializers.ManyRelatedField)
        ):
            relation: Any = attachments.child_relation
            relation.queryset = Artifact.objects.owned_by(request.user).filter(
                conversation=view.conversation, kind=ArtifactKind.ATTACHMENT, path=""
            )
        return fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not hasattr(instance, "instruction"):
            data["instruction"] = None
        return data

    def validate_attachments(self, value: list[Artifact]) -> list[Artifact]:
        limit = settings.YORIKI_ATTACHMENTS_PER_MESSAGE
        if len(value) > limit:
            raise serializers.ValidationError(_("添付は%(limit)s個までです") % {"limit": limit})
        return list(dict.fromkeys(value))

    def validate(self, attrs):
        if not attrs.get("body", "").strip() and not attrs.get("attachments"):
            raise serializers.ValidationError({"body": _("本文を入力してください")})
        return attrs

    def create(self, validated_data) -> ConversationMessage:
        try:
            return post_message(
                validated_data["conversation"],
                body=validated_data["body"],
                client_message_id=validated_data.get("client_message_id", ""),
                mode=validated_data["mode"],
                actor=validated_data["sender"],
                attachments=validated_data.get("attachments", []),
            )
        except CannotStart as exc:
            raise serializers.ValidationError({"non_field_errors": [str(exc)]}) from exc
