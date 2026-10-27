from typing import Any

import jsonschema
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from harness.tools import SKILL_TOOLS
from harness.tools.base import schema_problem
from llm.models import ModelProfile
from llm.serializers import AppModelProfileSerializer
from user.models import Department
from user.serializers import MinimumDepartmentSerializer, MinimumUserSerializer
from utils.exceptions import Conflict
from utils.fields import NormalizedTextField
from utils.serializers import ReadOnlyModelSerializer

from . import checks
from .errors import VersionConflict
from .models import REVISION_FIELDS, TaskAction, TaskDefinition, TaskDefinitionRevision, TaskInputType, TaskScope

TOOL_CHOICES = [(t.name, t.name) for t in SKILL_TOOLS]
MAX_PROMPT_TEMPLATE = 20_000
MAX_CHOICES = 100


class TaskSkillSerializer(serializers.Serializer):
    skill = serializers.CharField(max_length=30, help_text=_("SkillのID"))
    version = serializers.IntegerField(
        min_value=1, help_text=_("固定する版の番号。Skillが新しい版を公開しても変わらない")
    )


class TaskInputSerializer(serializers.Serializer):
    name = serializers.RegexField(
        checks.INPUT_NAME_RE, help_text=_("項目の名前（英小文字・数字・_）。テンプレートでは {{ inputs.名前 }}")
    )
    label = serializers.CharField(max_length=100, help_text=_("画面に出す名前"))  # type: ignore[assignment]
    type = serializers.ChoiceField(choices=TaskInputType.choices)
    required = serializers.BooleanField(default=False)  # type: ignore[assignment]
    description = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    choices = serializers.ListField(
        child=serializers.CharField(max_length=200),
        required=False,
        default=list,
        max_length=MAX_CHOICES,
        help_text=_("型が選択肢のときの選べる値"),
    )
    max_length = serializers.IntegerField(
        min_value=1, max_value=10_000, required=False, allow_null=True, default=None, help_text=_("文字の長さの上限")
    )

    def validate(self, attrs):
        if attrs["type"] == TaskInputType.CHOICE:
            if not attrs["choices"]:
                raise serializers.ValidationError({"choices": _("選択肢を入力してください")})
            attrs["choices"] = list(dict.fromkeys(attrs["choices"]))
        else:
            attrs["choices"] = []
        if attrs["type"] != TaskInputType.STRING:
            attrs["max_length"] = None
        return attrs


class TaskDefinitionSerializer(serializers.ModelSerializer):
    """タスクの作成と表示（定義を管理できる人だけが見る。authorization.md §5.3）。中身は今の版の値を平らに出す。

    中身を変えて保存すると、サーバーが新しい版を作る。更新では、編集を始めた時点の `version` を送り、その間に他の人が
    版を作っていたら409になる。範囲と部門は作成時に決め、更新では変えない
    """

    id = serializers.CharField(source="uid", read_only=True)
    scope = serializers.ChoiceField(choices=TaskScope.choices, help_text=_("範囲。作成した後は変えられない"))
    scope_display = serializers.CharField(source="get_scope_display", read_only=True)
    department: "serializers.SlugRelatedField[Department]" = serializers.SlugRelatedField(
        slug_field="uid",
        queryset=Department.objects.active(),
        required=False,
        allow_null=True,
        help_text=_("部門の範囲では必須。作成した後は変えられない"),
    )
    department_info = MinimumDepartmentSerializer(source="department", read_only=True, allow_null=True)
    owner = MinimumUserSerializer(read_only=True, allow_null=True)
    name = serializers.CharField(max_length=100)
    version = serializers.IntegerField(
        source="version_number",
        min_value=0,
        required=False,
        help_text=_("今の版。更新では、編集を始めた時点の値を送る（作成では送らない）"),
    )
    description = NormalizedTextField(
        source="current_revision.description", max_length=2000, required=False, allow_blank=True, default=""
    )
    prompt_template = NormalizedTextField(
        source="current_revision.prompt_template",
        max_length=MAX_PROMPT_TEMPLATE,
        help_text=_("Runの最初の依頼文。{{ inputs.名前 }} に入力の値を文字として埋め込む"),
    )
    skills = TaskSkillSerializer(source="current_revision.skills", many=True, required=False, default=list)
    connectors = serializers.ListField(
        source="current_revision.connectors",
        child=serializers.CharField(max_length=30),
        required=False,
        default=list,
        max_length=checks.MAX_TASK_CONNECTORS,
        help_text=_("使うConnectorのID。タスクのRunでは、エージェントはここにあるものだけを使う"),
    )
    tools = serializers.ListField(
        source="current_revision.tools",
        child=serializers.ChoiceField(choices=TOOL_CHOICES),
        required=False,
        default=list,
        max_length=50,
        help_text=_("使ってよいTool。Skillの「使ってよいTool」と両方に入っているものだけを使う"),
    )
    model_profile: "serializers.SlugRelatedField[ModelProfile]" = serializers.SlugRelatedField(
        source="current_revision.model_profile",
        slug_field="uid",
        queryset=ModelProfile.objects.filter(deleted=False).select_related("current_version"),
    )
    model_profile_info = AppModelProfileSerializer(source="current_revision.model_profile", read_only=True)
    effort = serializers.CharField(
        source="current_revision.effort",
        max_length=50,
        required=False,
        allow_blank=True,
        default="",
        help_text=_("空ならモデルの既定"),
    )
    handles_sensitive_data = serializers.BooleanField(
        source="current_revision.handles_sensitive_data",
        required=False,
        default=False,
        help_text=_("個人情報・機密情報を扱うタスク。機密のSkill・Connectorを使え、ローカルLLMのモデルだけを使う"),
    )
    inputs = TaskInputSerializer(source="current_revision.inputs", many=True, required=False, default=list)
    output_schema = serializers.JSONField(
        source="current_revision.output_schema",
        required=False,
        allow_null=True,
        default=None,
        help_text=_("業務結果のSchema。なければSkillのもの"),
    )
    change_reason = serializers.CharField(max_length=500, required=False, allow_blank=True, write_only=True)
    can_run = serializers.SerializerMethodField(help_text=_("今、手動で実行できるか（停止中でなく、版がある）"))
    created_by = MinimumUserSerializer(read_only=True)

    class Meta:
        model = TaskDefinition
        fields = [
            "id",
            "scope",
            "scope_display",
            "department",
            "department_info",
            "owner",
            "name",
            "suspended",
            "version",
            *REVISION_FIELDS,
            "model_profile_info",
            "change_reason",
            "can_run",
            "created_by",
            "created",
            "updated",
        ]
        read_only_fields = ["suspended", "created", "updated"]

    def get_can_run(self, obj: TaskDefinition) -> bool:
        return obj.in_service()

    def validate_skills(self, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(value) > checks.MAX_TASK_SKILLS:
            raise serializers.ValidationError(
                _("登録できるSkillは%(limit)s個までです") % {"limit": checks.MAX_TASK_SKILLS}
            )
        if len({e["skill"] for e in value}) != len(value):
            raise serializers.ValidationError(_("同じSkillを2回登録しています"))
        return [{"skill": e["skill"], "version": e["version"]} for e in value]

    def validate_connectors(self, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise serializers.ValidationError(_("同じConnectorを2回登録しています"))
        return value

    def validate_tools(self, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))

    def validate_inputs(self, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(value) > checks.MAX_INPUTS:
            raise serializers.ValidationError(_("入力の項目は%(limit)s個までです") % {"limit": checks.MAX_INPUTS})
        if len({i["name"] for i in value}) != len(value):
            raise serializers.ValidationError(_("同じ名前の項目があります"))
        return value

    def validate_output_schema(self, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise serializers.ValidationError(_("JSON Schemaのオブジェクトを入力してください"))
        try:
            jsonschema.Draft202012Validator.check_schema(value)
        except jsonschema.SchemaError as exc:
            raise serializers.ValidationError(
                _("JSON Schemaとして正しくありません：%(error)s") % {"error": exc.message}
            ) from exc
        # 業務結果のSchemaは submit_result の引数になるので、全プロバイダで通る範囲に限る（harness.md §7.3）
        if problem := schema_problem(value):
            raise serializers.ValidationError(
                _("業務結果のSchemaに使えない書き方があります：%(problem)s") % {"problem": problem}
            )
        return value

    def validate(self, attrs):
        user = self.context["request"].user
        if self.instance is None:
            scope = TaskScope(attrs["scope"])
            department = attrs.get("department") if scope == TaskScope.DEPARTMENT else None
            if scope == TaskScope.DEPARTMENT and department is None:
                raise serializers.ValidationError({"department": _("部門を選んでください")})
            # 作る人がその範囲で持つ権限。未保存のタスクで判定する（authorization.md §5.3）
            task = TaskDefinition(
                scope=scope, owner=user if scope == TaskScope.PERSONAL else None, department=department
            )
            if not task.can(user, TaskAction.MANAGE):
                raise PermissionDenied(_("この範囲にタスクを作る権限がありません"))
            attrs["department"] = department
        else:
            if "version_number" not in attrs:
                raise serializers.ValidationError({"version": _("編集を始めた時点の版を送ってください")})
            task = self.instance
        if "name" in attrs:
            attrs["name"] = attrs["name"].strip()
            self._check_unique_name(task, attrs["name"])
        content = self._content(attrs, task.current_revision if self.instance else None)
        if errors := checks.content_errors(task, content):
            raise serializers.ValidationError(errors)
        attrs["content"] = content
        return attrs

    def _check_unique_name(self, task: TaskDefinition, name: str) -> None:
        others = TaskDefinition.objects.filter(scope=task.scope, name=name, deleted=False)
        if task.scope == TaskScope.PERSONAL:
            others = others.filter(owner=task.owner)
        elif task.scope == TaskScope.DEPARTMENT:
            others = others.filter(department=task.department)
        if task.pk:
            others = others.exclude(pk=task.pk)
        if others.exists():
            raise serializers.ValidationError({"name": _("同じ範囲に同じ名前のタスクがあります")})

    @staticmethod
    def _content(attrs, current: TaskDefinitionRevision | None) -> dict[str, Any]:
        """送られた項目を優先し、残りは今の版の値で補う"""
        sent = attrs.pop("current_revision", {})
        return {name: sent[name] if name in sent else getattr(current, name) for name in REVISION_FIELDS}

    @transaction.atomic
    def create(self, validated_data) -> TaskDefinition:
        validated_data.pop("version_number", None)
        return TaskDefinition.create_with_revision(
            scope=validated_data["scope"],
            name=validated_data["name"],
            department=validated_data["department"],
            actor=validated_data["actor"],
            change_reason=validated_data.get("change_reason", ""),
            content=validated_data["content"],
        )

    @transaction.atomic
    def update(self, instance: TaskDefinition, validated_data) -> TaskDefinition:
        try:
            instance.revise(
                actor=validated_data["actor"],
                expected_version=validated_data["version_number"],
                change_reason=validated_data.get("change_reason", ""),
                **validated_data["content"],
            )
        except VersionConflict as exc:
            raise Conflict() from exc
        fields = ["current_revision", "updated"]
        if "name" in validated_data:
            instance.name = validated_data["name"]
            fields.append("name")
        # 変えた項目だけを保存する（行は revise() でロックしている）
        instance.save(update_fields=fields)
        return instance


class TaskDefinitionUpdateSerializer(TaskDefinitionSerializer):
    """タスクの更新。範囲と部門は変えられないので、読み取り専用にする"""

    scope = serializers.ChoiceField(choices=TaskScope.choices, read_only=True)
    department = serializers.SlugRelatedField(slug_field="uid", read_only=True)
    name = serializers.CharField(max_length=100, required=False)
    prompt_template = NormalizedTextField(
        source="current_revision.prompt_template", max_length=MAX_PROMPT_TEMPLATE, required=False
    )
    model_profile = serializers.SlugRelatedField(
        source="current_revision.model_profile",
        slug_field="uid",
        queryset=ModelProfile.objects.filter(deleted=False).select_related("current_version"),
        required=False,
    )

    class Meta(TaskDefinitionSerializer.Meta):
        pass

    def get_fields(self):
        # 部分更新でも、省いた項目に既定値を入れて今の版を上書きしないようにする
        fields = super().get_fields()
        for field in fields.values():
            if not field.read_only:
                field.required = False
                field.default = serializers.empty
        return fields


class TaskDefinitionRevisionSerializer(ReadOnlyModelSerializer):
    """版の表示（履歴）"""

    id = serializers.CharField(source="uid", read_only=True)
    skills = TaskSkillSerializer(many=True, read_only=True)
    inputs = TaskInputSerializer(many=True, read_only=True)
    model_profile: "serializers.SlugRelatedField[ModelProfile]" = serializers.SlugRelatedField(
        slug_field="uid", read_only=True
    )
    created_by = MinimumUserSerializer(read_only=True)

    class Meta:
        model = TaskDefinitionRevision
        fields = ["id", "number", *REVISION_FIELDS, "change_reason", "created_by", "created"]


class TaskSuspendSerializer(serializers.Serializer):
    def update(self, instance: TaskDefinition, validated_data) -> TaskDefinition:
        instance.set_suspended(validated_data["suspended"])
        return instance

    def create(self, validated_data):
        raise NotImplementedError

    def to_representation(self, instance):
        return TaskDefinitionSerializer(instance, context=self.context).data


class TaskRunCreateSerializer(serializers.Serializer):
    inputs = serializers.DictField(
        child=serializers.JSONField(allow_null=True),
        required=False,
        default=dict,
        help_text=_("入力の項目の名前と値。タスクの定義で決めた項目だけを受け付ける"),
    )
