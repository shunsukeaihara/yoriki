from collections import defaultdict
from typing import Any

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from skill.serializers import AssetDiffCountSerializer, asset_diff_items
from utils.exceptions import Conflict

from .errors import AlreadyAnswered, InvalidTransition
from .models import (
    ClarificationKind,
    ClarificationRequest,
    ClarificationStatus,
    Run,
    RunEndReason,
    RunEvent,
    RunEventKind,
    RunKind,
    RunStatus,
)


class TodoSerializer(serializers.Serializer):
    """作業の計画の1項目（harness/tools/todo_write.py）"""

    content = serializers.CharField(read_only=True)
    status = serializers.ChoiceField(
        choices=[("pending", _("未着手")), ("in_progress", _("作業中")), ("completed", _("完了"))], read_only=True
    )


class RunSkillSerializer(serializers.Serializer):
    """Runが使うSkillと版（Manifestに固定・追記したもの）"""

    skill = serializers.CharField(read_only=True, help_text=_("SkillのID"))
    name = serializers.CharField(read_only=True)
    number = serializers.IntegerField(read_only=True, help_text=_("版"))
    chosen = serializers.BooleanField(read_only=True, help_text=_("利用者が `/` で指定した"))


class RunSkillChangeSummarySerializer(serializers.Serializer):
    fields = serializers.ListField(child=serializers.CharField(), read_only=True, required=False)  # type: ignore[assignment]
    assets_added = serializers.ListField(child=serializers.CharField(), read_only=True, required=False)
    assets_removed = serializers.ListField(child=serializers.CharField(), read_only=True, required=False)
    assets_replaced = serializers.ListField(child=serializers.CharField(), read_only=True, required=False)
    assets_changed = serializers.ListField(child=serializers.CharField(), read_only=True, required=False)


class RunSkillChangeSerializer(serializers.Serializer):
    """Skillの編集のRunで変えたSkill（Skill管理のToolの結果。harness.md §10.9）。Skillごとに最後の状態"""

    skill = serializers.CharField(read_only=True, help_text=_("SkillのID"))
    name = serializers.CharField(read_only=True)
    scope = serializers.CharField(read_only=True, help_text=_("範囲（「部門 経理部」等）"))
    state = serializers.ChoiceField(
        choices=[
            ("created", _("作成")),
            ("draft_saved", _("変更案を保存")),
            ("published", _("公開")),
            ("rolled_back", _("切戻し")),
        ],
        read_only=True,
    )
    version = serializers.IntegerField(read_only=True, allow_null=True, help_text=_("公開・切戻しでできた版"))
    revision = serializers.IntegerField(read_only=True, allow_null=True, help_text=_("保存したDraftのrevision"))
    summary = RunSkillChangeSummarySerializer(read_only=True, allow_null=True, help_text=_("変わった項目と資料のkey"))
    asset_diffs = serializers.SerializerMethodField(help_text=_("置き換えた資料の差分の数"))

    @extend_schema_field(AssetDiffCountSerializer(many=True))
    def get_asset_diffs(self, obj: dict[str, Any]) -> list[dict[str, Any]]:
        return asset_diff_items(obj.get("asset_diffs"))


# 結果の値がSkillの変更（RunSkillChangeSerializer）になるTool
SKILL_CHANGE_TOOLS = (
    "create_skill",
    "update_skill_draft",
    "stage_skill_asset",
    "remove_skill_asset",
    "publish_skill_draft",
    "rollback_skill",
)


def skill_changes(run: Run) -> list[dict[str, Any]]:
    """Skillの編集のRunで変えたSkillの、Skillごとの最後の状態。公開していない変更は、前の公開の後の変更点をまとめる"""
    events = RunEvent.objects.filter(
        run=run, kind=RunEventKind.TOOL_RESULT, data__name__in=SKILL_CHANGE_TOOLS, data__is_error=False
    ).order_by("sequence")
    changes: dict[str, dict[str, Any]] = {}
    for event in events:
        value = event.data.get("value") or {}
        if "skill" not in value:
            continue
        before = changes.get(value["skill"])
        if value["state"] == "draft_saved" and before is not None and before["state"] in ("draft_saved", "created"):
            merged: dict[str, list[str]] = {k: list(v) for k, v in (before.get("summary") or {}).items()}
            for key, names in (value.get("summary") or {}).items():
                merged[key] = list(dict.fromkeys([*merged.get(key, []), *names]))
            diffs = {**(before.get("asset_diffs") or {}), **(value.get("asset_diffs") or {})}
            value = {**value, "summary": merged, "asset_diffs": diffs}
        changes[value["skill"]] = value
    return list(changes.values())


class RunSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)
    conversation: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(slug_field="uid", read_only=True)
    kind = serializers.ChoiceField(choices=RunKind.choices, read_only=True)
    status = serializers.ChoiceField(choices=RunStatus.choices, read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    end_reason = serializers.ChoiceField(choices=RunEndReason.choices, read_only=True, allow_null=True)
    end_reason_display = serializers.CharField(source="get_end_reason_display", read_only=True, allow_null=True)
    model_profile_name = serializers.CharField(source="model_version.profile.name", read_only=True)
    todos = serializers.SerializerMethodField(help_text=_("作業の計画（todo_write の最新の内容）"))
    skills = serializers.SerializerMethodField(help_text=_("使っているSkillと版"))
    result = serializers.JSONField(read_only=True, allow_null=True, help_text=_("業務結果（submit_result の内容）"))
    skill_changes = serializers.SerializerMethodField(help_text=_("Skillの編集のRunで変えたSkill"))

    class Meta:
        model = Run
        fields = [
            "id",
            "conversation",
            "kind",
            "status",
            "status_display",
            "end_reason",
            "end_reason_display",
            "error",
            "model_profile_name",
            "effort",
            "step_count",
            "todos",
            "skills",
            "result",
            "skill_changes",
            "created",
            "updated",
            "finished",
        ]
        read_only_fields = fields

    @extend_schema_field(TodoSerializer(many=True))
    def get_todos(self, obj: Run) -> list[dict[str, str]]:
        event = (
            RunEvent.objects.filter(
                run=obj, kind=RunEventKind.TOOL_RESULT, data__name="todo_write", data__is_error=False
            )
            .order_by("-sequence")
            .first()
        )
        return (event.data.get("value") or {}).get("todos", []) if event else []

    @extend_schema_field(RunSkillSerializer(many=True))
    def get_skills(self, obj: Run) -> list[dict[str, Any]]:
        manifest = obj.manifest or {}
        chosen = [{**e, "chosen": True} for e in (manifest.get("fixed") or {}).get("skills") or []]
        loaded = [{**e, "chosen": False} for e in manifest.get("appended", []) if e.get("kind") == "skill"]
        return list(RunSkillSerializer([*chosen, *loaded], many=True).data)

    @extend_schema_field(RunSkillChangeSerializer(many=True))
    def get_skill_changes(self, obj: Run) -> list[dict[str, Any]]:
        if obj.kind != RunKind.SKILL_EDIT:
            return []
        return list(RunSkillChangeSerializer(skill_changes(obj), many=True).data)


class RunCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000, required=False, allow_blank=True)

    def validate(self, attrs):
        if self.instance is None or not self.instance.is_cancellable():
            raise serializers.ValidationError({"non_field_errors": _("この実行は停止できません")})
        return attrs

    def update(self, instance: Run, validated_data) -> Run:
        actor = validated_data.pop("actor")
        try:
            instance.request_cancel(validated_data.get("reason", ""), actor=actor)
        except InvalidTransition as exc:
            # validate()の後に、Workerが先に状態を変えた場合
            raise Conflict() from exc
        instance.refresh_from_db()
        return instance

    def create(self, validated_data):
        raise NotImplementedError("instanceを渡して使う")

    def to_representation(self, instance: Run):
        return RunSerializer(instance, context=self.context).data


class ClarificationOptionSerializer(serializers.Serializer):
    # Field.label と同じ名前だが、宣言したフィールドはメタクラスがクラス属性から外すので衝突しない
    label = serializers.CharField(read_only=True)  # type: ignore[assignment]
    description = serializers.CharField(read_only=True)


class ClarificationQuestionSerializer(serializers.Serializer):
    header = serializers.CharField(read_only=True)
    question = serializers.CharField(read_only=True)
    options = ClarificationOptionSerializer(many=True, read_only=True)
    multi_select = serializers.BooleanField(read_only=True)


class ClarificationAnswerItemSerializer(serializers.Serializer):
    selected = serializers.ListField(child=serializers.CharField(max_length=200), max_length=10, default=list)
    text = serializers.CharField(max_length=5000, required=False, allow_blank=True, default="")


class ClarificationContextSerializer(serializers.Serializer):
    """確認の対象。Skillの編集の確認で、対象のSkill（新しいSkillならskillが空）と変更の要約"""

    skill = serializers.CharField(read_only=True, required=False)
    name = serializers.CharField(read_only=True, required=False)
    scope = serializers.CharField(read_only=True, required=False)
    summary = serializers.CharField(read_only=True, required=False)


class ClarificationRequestSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)
    run: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(slug_field="uid", read_only=True)
    kind = serializers.ChoiceField(choices=ClarificationKind.choices, read_only=True)
    status = serializers.ChoiceField(choices=ClarificationStatus.choices, read_only=True)
    questions = ClarificationQuestionSerializer(many=True, read_only=True)
    answers = ClarificationAnswerItemSerializer(many=True, read_only=True)
    answered_by: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(
        slug_field="uid", read_only=True, allow_null=True
    )
    target = ClarificationContextSerializer(source="context", read_only=True, allow_null=True)

    class Meta:
        model = ClarificationRequest
        fields = [
            "id",
            "run",
            "kind",
            "status",
            "target",
            "questions",
            "answers",
            "comment",
            "answered_by",
            "answered",
            "expires",
            "created",
        ]
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["target"] = data["target"] or None  # 対象のない確認（ask_user・モデルの切り替え）
        return data


class ClarificationAnswerSerializer(serializers.Serializer):
    """確認への回答。質問ごとの選択（と自由記述）を質問と同じ順に送る。補足だけでもよい"""

    answers = ClarificationAnswerItemSerializer(many=True, required=False, default=list)
    comment = serializers.CharField(max_length=5000, required=False, allow_blank=True, default="")

    def validate(self, attrs):
        clarification = self.instance
        if clarification is None or clarification.status != ClarificationStatus.OPEN:
            raise serializers.ValidationError({"non_field_errors": _("この確認には回答できません")})
        questions = clarification.questions
        answers = attrs["answers"] or [{"selected": [], "text": ""} for _q in questions]
        errors: dict[str, list] = defaultdict(list)
        if len(answers) != len(questions):
            errors["answers"].append(_("質問と同じ数の回答を送ってください"))
        for question, answer in zip(questions, answers, strict=False):
            labels = {o["label"] for o in question["options"]}
            if any(s not in labels for s in answer["selected"]):
                errors["answers"].append(_("選択肢から選んでください"))
            if not question.get("multi_select") and len(answer["selected"]) > 1:
                errors["answers"].append(_("この質問では1つだけ選んでください"))
        answered = any(a["selected"] or a["text"].strip() for a in answers) or attrs["comment"].strip()
        if not answered:
            errors["non_field_errors"].append(_("回答を選ぶか入力してください"))
        if clarification.kind == ClarificationKind.MODEL_SWITCH and not any(a["selected"] for a in answers):
            errors["answers"].append(_("続け方を選んでください"))
        if errors:
            raise serializers.ValidationError(errors)
        attrs["answers"] = answers
        return attrs

    def update(self, instance: ClarificationRequest, validated_data) -> ClarificationRequest:
        actor = validated_data.pop("actor")
        try:
            instance.accept_answer(validated_data["answers"], validated_data["comment"], actor=actor)
        except AlreadyAnswered as exc:
            # Web画面と外部スレッドから同時に回答された場合
            raise Conflict() from exc
        instance.refresh_from_db()
        return instance

    def create(self, validated_data):
        raise NotImplementedError("instanceを渡して使う")

    def to_representation(self, instance: ClarificationRequest):
        return ClarificationRequestSerializer(instance, context=self.context).data
