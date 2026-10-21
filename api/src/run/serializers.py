from collections import defaultdict
from typing import Any

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

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
    RunStatus,
)


class RunSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)
    conversation: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(slug_field="uid", read_only=True)
    status = serializers.ChoiceField(choices=RunStatus.choices, read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    end_reason = serializers.ChoiceField(choices=RunEndReason.choices, read_only=True, allow_null=True)
    end_reason_display = serializers.CharField(source="get_end_reason_display", read_only=True, allow_null=True)
    model_profile_name = serializers.CharField(source="model_version.profile.name", read_only=True)
    todos = serializers.SerializerMethodField(help_text=_("作業の計画（todo_write の最新の内容）"))

    class Meta:
        model = Run
        fields = [
            "id",
            "conversation",
            "status",
            "status_display",
            "end_reason",
            "end_reason_display",
            "error",
            "model_profile_name",
            "effort",
            "step_count",
            "todos",
            "created",
            "updated",
            "finished",
        ]
        read_only_fields = fields

    def get_todos(self, obj: Run) -> list[dict[str, str]]:
        event = (
            RunEvent.objects.filter(
                run=obj, kind=RunEventKind.TOOL_RESULT, data__name="todo_write", data__is_error=False
            )
            .order_by("-sequence")
            .first()
        )
        return (event.data.get("value") or {}).get("todos", []) if event else []


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


class ClarificationRequestSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)
    run: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(slug_field="uid", read_only=True)
    kind = serializers.ChoiceField(choices=ClarificationKind.choices, read_only=True)
    status = serializers.ChoiceField(choices=ClarificationStatus.choices, read_only=True)
    questions = ClarificationQuestionSerializer(many=True, read_only=True)
    answers = ClarificationAnswerItemSerializer(many=True, read_only=True)
    answered_by: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(slug_field="uid", read_only=True)

    class Meta:
        model = ClarificationRequest
        fields = [
            "id",
            "run",
            "kind",
            "status",
            "questions",
            "answers",
            "comment",
            "answered_by",
            "answered",
            "expires",
            "created",
        ]
        read_only_fields = fields


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
