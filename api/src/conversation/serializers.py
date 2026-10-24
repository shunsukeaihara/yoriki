from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from artifact.models import Artifact, ArtifactKind
from artifact.serializers import ArtifactSerializer
from harness.skills import MAX_EXPLICIT_SKILLS, unsupported_skills
from llm.models import ModelProfile
from llm.serializers import AppModelProfileSerializer
from run.models import ACTIVE_STATUSES, Run
from run.serializers import RunSerializer
from skill.models import Skill, SkillAccess
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
        if self.instance is not None and "model_profile" in attrs:
            # 実行中のRunも切り替わるので、そのRunが使うSkillの必要な能力を持つモデルに限る（design.md §4.8）
            run = self.instance.runs.filter(status__in=ACTIVE_STATUSES).first()
            if run is not None and (names := unsupported_skills(run, version)):
                raise serializers.ValidationError(
                    {
                        "model_profile": _("このモデルは、使っているSkill（%(names)s）に必要な能力を持ちません")
                        % {"names": "、".join(names)}
                    }
                )
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


class MessageSkillSerializer(serializers.Serializer):
    """メッセージで指定したSkill（送ったときの名前）"""

    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)


class MessageSkillEditSerializer(serializers.Serializer):
    """メッセージのSkillの編集の依頼（送ったときの名前）。新しいSkillを作る依頼ではidが空"""

    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)


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
    skills: "serializers.SlugRelatedField[Skill]" = serializers.SlugRelatedField(
        slug_field="uid",
        many=True,
        required=False,
        queryset=Skill.objects.none(),
        write_only=True,
        help_text=_("入力欄の `/` で指定したSkill。実行中なら次の依頼になる（Skillの版は依頼を始めるときに決める）"),
    )
    skills_info = MessageSkillSerializer(source="skills", many=True, read_only=True)
    edit_skill: "serializers.SlugRelatedField[Skill]" = serializers.SlugRelatedField(
        slug_field="uid",
        required=False,
        allow_null=True,
        queryset=Skill.objects.none(),
        write_only=True,
        help_text=_("入力欄の `/` で「編集」を選んだSkill。このメッセージはSkillの編集の依頼になる（別のRunで行う）"),
    )
    create_skill = serializers.BooleanField(
        required=False,
        default=False,
        write_only=True,
        help_text=_("入力欄の `/` で「新しいSkillを作る」を選んだ。このメッセージは新しいSkillを作る依頼になる"),
    )
    skill_edit = serializers.SerializerMethodField()
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
            "skills",
            "skills_info",
            "edit_skill",
            "create_skill",
            "skill_edit",
            "client_message_id",
            "mode",
            "instruction",
            "created",
        ]
        read_only_fields = ["created"]

    def get_fields(self):
        fields = super().get_fields()
        # 添付に指定できるのは、ログインユーザーがこの会話にアップロードしたファイルだけ。送信済みのものも受け付け、
        # 同じ client_message_id の再送なら post_message() が前のメッセージを返し、別のメッセージなら断る
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
                conversation=view.conversation, kind=ArtifactKind.ATTACHMENT
            )
        skills = fields["skills"]
        if request is not None and isinstance(skills, serializers.ManyRelatedField):
            # 指定できるのは、利用者が使えるSkillだけ（使えるかはRun開始時にもう一度確かめる）
            skill_relation: Any = skills.child_relation
            skill_relation.queryset = Skill.objects.usable_by(request.user)
        edit_skill: Any = fields["edit_skill"]
        if request is not None:
            # 編集の権限は validate_edit_skill() で確かめる（見えないSkillは、ないものとして断る）
            edit_skill.queryset = Skill.objects.visible_to(request.user)
        return fields

    @extend_schema_field(MessageSkillEditSerializer(allow_null=True))
    def get_skill_edit(self, obj: ConversationMessage) -> dict[str, str] | None:
        return obj.skill_edit or None

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

    def validate_skills(self, value: list[Skill]) -> list[Skill]:
        value = list(dict.fromkeys(value))
        if len(value) > MAX_EXPLICIT_SKILLS:
            raise serializers.ValidationError(_("Skillは%(limit)s個まで指定できます") % {"limit": MAX_EXPLICIT_SKILLS})
        return value

    def validate_edit_skill(self, value: Skill | None) -> Skill | None:
        if value is not None and value.scope_access_of(self.context["request"].user) < SkillAccess.EDIT:
            raise serializers.ValidationError(_("このSkillを編集する権限がありません"))
        return value

    def validate(self, attrs):
        editing = attrs.get("edit_skill") is not None or attrs.get("create_skill")
        if editing and attrs.get("skills"):
            raise serializers.ValidationError(
                {"skills": _("Skillの編集の依頼では、使うSkillを指定できません。別のメッセージで送ってください")}
            )
        if attrs.get("edit_skill") is not None and attrs.get("create_skill"):
            raise serializers.ValidationError(
                {"create_skill": _("編集と新しいSkillの作成は、どちらか1つにしてください")}
            )
        if editing and not attrs.get("body", "").strip():
            raise serializers.ValidationError({"body": _("Skillをどう変えるかを入力してください")})
        if not attrs.get("body", "").strip() and not attrs.get("attachments") and not attrs.get("skills"):
            raise serializers.ValidationError({"body": _("本文を入力してください")})
        return attrs

    @staticmethod
    def _skill_edit(validated_data) -> dict[str, str] | None:
        if (skill := validated_data.get("edit_skill")) is not None:
            return {"id": skill.uid, "name": skill.name}
        if validated_data.get("create_skill"):
            return {"id": "", "name": ""}
        return None

    def create(self, validated_data) -> ConversationMessage:
        try:
            return post_message(
                validated_data["conversation"],
                body=validated_data["body"],
                client_message_id=validated_data.get("client_message_id", ""),
                mode=validated_data["mode"],
                actor=validated_data["sender"],
                attachments=validated_data.get("attachments", []),
                skills=validated_data.get("skills", []),
                skill_edit=self._skill_edit(validated_data),
            )
        except CannotStart as exc:
            raise serializers.ValidationError({"non_field_errors": [str(exc)]}) from exc
