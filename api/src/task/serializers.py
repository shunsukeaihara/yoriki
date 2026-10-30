import json
from typing import Any

import jsonschema
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.fields import empty
from rest_framework.utils import html

from connector.models import Connector
from delivery.models import Destination, NotifyContent, NotifyEvent
from delivery.serializers import DestinationChoiceSerializer
from harness.result_files import file_format_problem
from harness.tools.base import schema_problem
from llm.models import ModelProfile
from llm.serializers import AppModelProfileSerializer
from run.models import Run, RunEndReason, RunStatus
from skill.models import Skill
from user.models import Department
from user.serializers import MinimumDepartmentSerializer, MinimumUserSerializer
from utils.exceptions import Conflict
from utils.fields import NormalizedTextField
from utils.serializers import ReadOnlyModelSerializer

from . import checks
from .dispatch import plan_run, received_run
from .errors import CannotRun, VersionConflict
from .inputs import InputFile, clean_inputs, render
from .models import (
    DEFAULT_MAX_PAYLOAD_BYTES,
    MAX_PAYLOAD_BYTES,
    REVISION_FIELDS,
    TRIGGER_REVISION_FIELDS,
    WEBHOOK_TASK_SCOPES,
    TaskAction,
    TaskDefinition,
    TaskDefinitionRevision,
    TaskInputType,
    TaskScope,
    TriggerBinding,
    TriggerBindingRevision,
    TriggerKind,
    TriggerReceipt,
)
from .webhook import PayloadError, dedup_key, input_values, parse_path, parse_payload

MAX_PROMPT_TEMPLATE = 20_000
MAX_CHOICES = 100
MAX_NOTIFICATION_INSTRUCTIONS = 4000


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
        # 手動実行のmultipartでは、ファイル以外の値を `inputs` のフィールドで送るので、ファイルの項目の名前に使わせない
        if attrs["type"] == TaskInputType.FILE and attrs["name"] == "inputs":
            raise serializers.ValidationError({"name": _("inputs は型がファイルの項目の名前に使えません")})
        if attrs["type"] == TaskInputType.CHOICE:
            if not attrs["choices"]:
                raise serializers.ValidationError({"choices": _("選択肢を入力してください")})
            attrs["choices"] = list(dict.fromkeys(attrs["choices"]))
        else:
            attrs["choices"] = []
        if attrs["type"] != TaskInputType.STRING:
            attrs["max_length"] = None
        return attrs


class TaskTriggerSerializer(ReadOnlyModelSerializer):
    """タスクの画面に出す入口（タスクを管理できる人が見る。作る・変えるのは連携の管理者）"""

    id = serializers.CharField(source="uid", read_only=True)
    kind = serializers.ChoiceField(choices=TriggerKind.choices, read_only=True)

    class Meta:
        model = TriggerBinding
        fields = ["id", "kind", "name", "suspended", "key_prefix", "created"]


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
    destination: "serializers.SlugRelatedField[Destination]" = serializers.SlugRelatedField(
        source="current_revision.destination",
        slug_field="uid",
        queryset=Destination.objects.alive().select_related("current_revision", "department"),
        required=False,
        allow_null=True,
        default=None,
        help_text=_("結果の通知先（出口）。タスクの範囲で使ってよい出口だけ（候補は app/v1/destinations/）"),
    )
    destination_info = DestinationChoiceSerializer(
        source="current_revision.destination", read_only=True, allow_null=True
    )
    notify_events = serializers.ListField(
        source="current_revision.notify_events",
        child=serializers.ChoiceField(choices=NotifyEvent.choices),
        required=False,
        default=list,
        max_length=len(NotifyEvent),
        help_text=_("通知するイベント。Webhookの入口があるタスクでは run.failed が必須"),
    )
    notify_content = serializers.ChoiceField(
        source="current_revision.notify_content",
        choices=NotifyContent.choices,
        required=False,
        default=NotifyContent.STATUS_AND_RESULT,
        help_text=_("通知の内容（状態だけ・業務結果・両方）"),
    )
    notification_instructions = NormalizedTextField(
        source="current_revision.notification_instructions",
        max_length=MAX_NOTIFICATION_INSTRUCTIONS,
        required=False,
        allow_blank=True,
        default="",
        help_text=_(
            "通知の書き方（任意）。業務のRunの最後に、これに従って完了の通知の文章と渡すファイルを決めさせる。"
            "通知先と完了（run.completed）の通知が要り、通知の内容に業務結果を含める。空なら決まった形で送る"
        ),
    )
    change_reason = serializers.CharField(max_length=500, required=False, allow_blank=True, write_only=True)
    can_run = serializers.SerializerMethodField(help_text=_("今、手動で実行できるか（停止中でなく、版がある）"))
    triggers = serializers.SerializerMethodField(help_text=_("このタスクの入口（作る・変えるのは連携の管理者）"))
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
            "destination_info",
            "change_reason",
            "can_run",
            "triggers",
            "created_by",
            "created",
            "updated",
        ]
        read_only_fields = ["suspended", "created", "updated"]

    def get_can_run(self, obj: TaskDefinition) -> bool:
        return obj.in_service()

    @extend_schema_field(TaskTriggerSerializer(many=True))
    def get_triggers(self, obj: TaskDefinition) -> list[dict[str, Any]]:
        if obj.pk is None:
            return []
        bindings = [b for b in obj.triggers.all() if not b.deleted]
        return list(TaskTriggerSerializer(bindings, many=True).data)

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

    def validate_notify_events(self, value: list[str]) -> list[str]:
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
        if problem := schema_problem(value) or file_format_problem(value):
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
            # 内容の検証より先に409にする（数で届いた版は、項目ごとの検証の前に to_internal_value() で照合している）
            if attrs["version_number"] != task.version_number:
                raise Conflict()
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
        # Webhookの入口があるタスクは、失敗を知らせる先を外せない（design.md §4.2）。入口の作成もタスクの行を
        # ロックしてから確かめるので、行をロックした後（revise() の後）に確かめる
        if (
            not checks.notifies_failure(validated_data["content"])
            and TriggerBinding.objects.alive().filter(task=instance, kind=TriggerKind.WEBHOOK).exists()
        ):
            raise serializers.ValidationError(
                {"notify_events": _("Webhookの入口があるタスクでは、失敗（run.failed）を知らせる通知先が必要です")}
            )
        fields = ["current_revision", "updated"]
        if "name" in validated_data:
            instance.name = validated_data["name"]
            fields.append("name")
        # 変えた項目だけを保存する（行は revise() でロックしている）
        instance.save(update_fields=fields)
        return instance


class TaskReferenceSerializer(serializers.Serializer):
    """版に登録したSkill・Connectorの名前（削除されたものも、登録した名前のまま出す）"""

    id = serializers.CharField()
    name = serializers.CharField()
    deleted = serializers.BooleanField()


class TaskDefinitionDetailSerializer(TaskDefinitionSerializer):
    """タスクの詳細（編集画面）。一覧の項目に加えて、登録したSkill・Connectorの名前と、今の版が保存のときの検査に
    通らない理由（Skillの停止・Connectorの削除・範囲の外等。多くは実行も断られる）を返す"""

    skills_info = serializers.SerializerMethodField(help_text=_("登録したSkillの名前"))
    connectors_info = serializers.SerializerMethodField(help_text=_("登録したConnectorの名前"))
    problems = serializers.SerializerMethodField(
        help_text=_(
            "今の版が、保存のときの検査に通らない理由（なければ空）。版を作ったときより後の変更（Skillの停止・"
            "Connectorの削除等）で生じたもので、多くは実行も断られる"
        )
    )

    class Meta(TaskDefinitionSerializer.Meta):
        fields = [*TaskDefinitionSerializer.Meta.fields, "skills_info", "connectors_info", "problems"]

    @extend_schema_field(TaskReferenceSerializer(many=True))
    def get_skills_info(self, obj: TaskDefinition) -> list[dict[str, Any]]:
        revision = obj.current_revision
        if revision is None:
            return []
        uids = [entry["skill"] for _field, entry in checks.skill_entries(revision)]
        skills = {s.uid: s for s in Skill.objects.filter(uid__in=uids).only("uid", "name", "deleted")}
        return [{"id": s.uid, "name": s.name, "deleted": s.deleted} for uid in uids if (s := skills.get(uid))]

    @extend_schema_field(TaskReferenceSerializer(many=True))
    def get_connectors_info(self, obj: TaskDefinition) -> list[dict[str, Any]]:
        revision = obj.current_revision
        if revision is None:
            return []
        connectors = {
            c.uid: c for c in Connector.objects.filter(uid__in=revision.connectors).only("uid", "name", "deleted")
        }
        return [
            {"id": c.uid, "name": c.name, "deleted": c.deleted}
            for uid in revision.connectors
            if (c := connectors.get(uid))
        ]

    def get_problems(self, obj: TaskDefinition) -> list[str]:
        revision = obj.current_revision
        if revision is None:
            return []
        content = {name: getattr(revision, name) for name in REVISION_FIELDS}
        return [message for messages in checks.content_errors(obj, content).values() for message in messages]


class TaskDefinitionUpdateSerializer(TaskDefinitionDetailSerializer):
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

    class Meta(TaskDefinitionDetailSerializer.Meta):
        pass

    def to_internal_value(self, data):
        # 別の人が版を作った後の保存は、項目ごとの検証（通知先・モデル設定等の参照）より先に409にする。今の版に対する
        # 誤りを古い画面に見せないため。保存の直前にも、行をロックしてから revise() が照合し直す
        version = data.get("version") if hasattr(data, "get") else None
        if self.instance is not None and isinstance(version, int) and version != self.instance.version_number:
            raise Conflict()
        return super().to_internal_value(data)

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
    destination: "serializers.SlugRelatedField[Destination]" = serializers.SlugRelatedField(
        slug_field="uid", read_only=True, allow_null=True
    )
    created_by = MinimumUserSerializer(read_only=True)

    class Meta:
        model = TaskDefinitionRevision
        fields = ["id", "number", *REVISION_FIELDS, "change_reason", "created_by", "created"]


class TaskSuspendSerializer(serializers.Serializer):
    def update(self, instance: TaskDefinition, validated_data) -> TaskDefinition:
        instance.set_suspended(validated_data["suspended"], actor=validated_data.get("actor"))
        return instance

    def create(self, validated_data):
        raise NotImplementedError

    def to_representation(self, instance):
        return TaskDefinitionDetailSerializer(instance, context=self.context).data


class TaskSkillChoiceSerializer(ReadOnlyModelSerializer):
    """タスクの編集画面で選ぶSkillの候補（ID・名前・説明・範囲・版だけ。authorization.md §7.2）"""

    id = serializers.CharField(source="uid", read_only=True)
    scope_display = serializers.CharField(source="get_scope_display", read_only=True)
    department_info = MinimumDepartmentSerializer(source="department", read_only=True, allow_null=True)
    description = serializers.CharField(source="current_version.description", read_only=True)
    version = serializers.IntegerField(source="version_number", read_only=True, help_text=_("公開中の版"))
    versions = serializers.SerializerMethodField(help_text=_("固定できる版の番号（新しい順）"))

    class Meta:
        model = Skill
        fields = ["id", "name", "description", "scope", "scope_display", "department_info", "version", "versions"]

    def get_versions(self, obj: Skill) -> list[int]:
        return sorted((v.number for v in obj.versions.all()), reverse=True)


class InputValuesField(serializers.DictField):
    """入力の値。multipart/form-dataではJSONの文字列で送る"""

    def get_value(self, dictionary):
        if html.is_html_input(dictionary):
            return dictionary.get(self.field_name, empty)
        return super().get_value(dictionary)

    def to_internal_value(self, data):
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                raise serializers.ValidationError(_("JSONのオブジェクトで入力してください")) from None
        return super().to_internal_value(data)


class TaskRunCreateSerializer(serializers.Serializer):
    """手動実行の入力。型がファイルの項目があれば multipart/form-data で送り、`inputs` はJSONの文字列にして、
    ファイルは項目の名前をフィールドの名前にして付ける"""

    inputs = InputValuesField(
        child=serializers.JSONField(allow_null=True),
        required=False,
        default=dict,
        help_text=_(
            "入力の項目の名前と値。タスクの定義で決めた項目だけを受け付ける。multipart/form-dataではJSONの"
            "文字列にし、型がファイルの項目は項目の名前でファイルを付ける"
        ),
    )

    def validate(self, attrs):
        files: dict[str, InputFile] = {}
        data = self.initial_data
        for name, items in data.lists() if hasattr(data, "lists") else ():
            uploaded = [i for i in items if isinstance(i, UploadedFile)]
            if not uploaded:
                if name != "inputs":
                    # 文字の値は inputs に入れる。ほかのフィールドを黙って捨てない
                    error = _("ファイルを送るか、値は inputs に入れて送ってください")
                    raise serializers.ValidationError({"inputs": {name: error}})
                continue
            if len(items) > 1 or name == "inputs":
                # 2つ目のファイルや、ファイルと一緒に送った文字を黙って捨てない
                raise serializers.ValidationError({"inputs": {name: _("1つの項目にはファイルを1つだけ送ってください")}})
            limit = settings.YORIKI_UPLOAD_MAX_MB
            if (uploaded[0].size or 0) > limit * 1024 * 1024:
                raise serializers.ValidationError(
                    {"inputs": {name: _("ファイルが大きすぎます（%(limit)sMBまで）") % {"limit": limit}}}
                )
            files[name] = InputFile(name=uploaded[0].name or "file", upload=uploaded[0])
        if len(files) > (limit := settings.YORIKI_ATTACHMENTS_PER_MESSAGE):
            raise serializers.ValidationError(_("ファイルは%(limit)s個までです") % {"limit": limit})
        if both := set(files) & set(attrs["inputs"]):
            errors = {name: _("値とファイルの両方が送られました") for name in both}
            raise serializers.ValidationError({"inputs": errors})
        attrs["inputs"] = {**attrs["inputs"], **files}
        return attrs


class TriggerInputSerializer(serializers.Serializer):
    name = serializers.RegexField(checks.INPUT_NAME_RE, help_text=_("タスクの入力の項目の名前"))
    path = serializers.CharField(max_length=200, help_text=_("payloadの中の場所（JSONのpath。例：$.data.vendor_name）"))

    def validate_path(self, value: str) -> str:
        try:
            parse_path(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return value


class TriggerTaskInputSerializer(serializers.Serializer):
    """入口の画面に出すタスクの入力の項目。取り出しの設定に要るもの（名前・表示名・型・必須）だけを返し、説明・
    選択肢・文字数の上限等のタスクの定義の中身は返さない（連携の管理者がタスクを管理できるとは限らない。
    authorization.md §5.3）"""

    name = serializers.CharField(read_only=True)
    label = serializers.CharField(read_only=True)  # type: ignore[assignment]
    type = serializers.ChoiceField(choices=TaskInputType.choices, read_only=True)
    required = serializers.BooleanField(read_only=True, default=False)  # type: ignore[assignment]


class TriggerTaskSerializer(ReadOnlyModelSerializer):
    """入口の画面に出すタスク（名前と範囲と入力の項目。項目の取り出しの設定に使う）"""

    id = serializers.CharField(source="uid", read_only=True)
    scope_display = serializers.CharField(source="get_scope_display", read_only=True)
    department = MinimumDepartmentSerializer(read_only=True, allow_null=True)
    inputs = TriggerTaskInputSerializer(source="current_revision.inputs", many=True, read_only=True)

    class Meta:
        model = TaskDefinition
        fields = ["id", "name", "scope", "scope_display", "department", "suspended", "inputs"]


def _check_trigger_inputs(value: list[dict[str, str]]) -> list[dict[str, str]]:
    """項目の取り出しの数と重複（入口の保存と、サンプルを試すときに共通）"""
    if len(value) > checks.MAX_INPUTS:
        raise serializers.ValidationError(_("取り出す項目は%(limit)s個までです") % {"limit": checks.MAX_INPUTS})
    if len({i["name"] for i in value}) != len(value):
        raise serializers.ValidationError(_("同じ項目を2回指定しています"))
    return [{"name": i["name"], "path": i["path"]} for i in value]


def unknown_trigger_inputs(task: TaskDefinition, mappings: list[dict[str, str]]) -> list[str]:
    """タスクの今の版にない項目の名前（入口の保存と、サンプルを試すときに断る）"""
    names = {i["name"] for i in (task.current_revision.inputs if task.current_revision else [])}
    return [i["name"] for i in mappings if i["name"] not in names]


class TriggerBindingSerializer(serializers.ModelSerializer):
    """入口の作成と表示（連携の管理者。authorization.md §5.5）。受け取り方は今の版の値を平らに出す。

    タスクは作成時に決め、変えない（付け替えるときは入口を作り直す）。鍵は作成時と作り直したときに一度だけ返す。
    更新では、編集を始めた時点の `version` を送り、その間に他の人が版を作っていたら409になる
    """

    id = serializers.CharField(source="uid", read_only=True)
    kind = serializers.ChoiceField(choices=TriggerKind.choices, read_only=True)
    task: "serializers.SlugRelatedField[TaskDefinition]" = serializers.SlugRelatedField(
        slug_field="uid",
        queryset=TaskDefinition.objects.filter(deleted=False, scope__in=WEBHOOK_TASK_SCOPES),
        help_text=_("起動するタスク（部門・全社・システム）。作成した後は変えられない"),
    )
    task_info = TriggerTaskSerializer(source="task", read_only=True)
    name = serializers.CharField(max_length=100)
    version = serializers.IntegerField(
        source="version_number",
        min_value=0,
        required=False,
        help_text=_("今の版。更新では、編集を始めた時点の値を送る（作成では送らない）"),
    )
    event_id_path = serializers.CharField(
        source="current_revision.event_id_path",
        max_length=200,
        required=False,
        allow_blank=True,
        default="",
        help_text=_(
            "重複の判定に使う送り元のイベントIDの場所（JSONのpath）。空なら Idempotency-Key ヘッダーか、推奨の形式の "
            "event_id。どれもなければ重複を判定せず、送り元が再送すると二重に実行する"
        ),
    )
    inputs = TriggerInputSerializer(
        source="current_revision.inputs",
        many=True,
        required=False,
        default=list,
        help_text=_("項目の取り出し。空なら推奨の形式の inputs をそのまま使う。型と必須かはタスクの入力の項目で決まる"),
    )
    max_payload_bytes = serializers.IntegerField(
        source="current_revision.max_payload_bytes",
        min_value=1024,
        max_value=MAX_PAYLOAD_BYTES,
        required=False,
        default=DEFAULT_MAX_PAYLOAD_BYTES,
        help_text=_("payloadの大きさの上限（バイト。既定1MiB、最大10MiB）"),
    )
    change_reason = serializers.CharField(max_length=500, required=False, allow_blank=True, write_only=True)
    url = serializers.SerializerMethodField(help_text=_("送り元に設定するURL（/api/ からのパス）"))
    created_by = MinimumUserSerializer(read_only=True)

    class Meta:
        model = TriggerBinding
        fields = [
            "id",
            "kind",
            "task",
            "task_info",
            "name",
            "suspended",
            "version",
            *TRIGGER_REVISION_FIELDS,
            "change_reason",
            "url",
            "key_prefix",
            "key_rotated",
            "created_by",
            "created",
            "updated",
        ]
        read_only_fields = ["suspended", "key_prefix", "key_rotated", "created", "updated"]

    def get_url(self, obj: TriggerBinding) -> str:
        return f"/api/hooks/v1/tasks/{obj.task.uid}/"

    def validate_inputs(self, value: list[dict[str, str]]) -> list[dict[str, str]]:
        return _check_trigger_inputs(value)

    def validate_event_id_path(self, value: str) -> str:
        if value:
            try:
                parse_path(value)
            except ValueError as exc:
                raise serializers.ValidationError(str(exc)) from exc
        return value

    def validate(self, attrs):
        if self.instance is None:
            task: TaskDefinition = attrs["task"]
            if TriggerBinding.objects.alive().filter(task=task, kind=TriggerKind.WEBHOOK).exists():
                raise serializers.ValidationError({"task": _("このタスクには、もうWebhookの入口があります")})
        else:
            if "version_number" not in attrs:
                raise serializers.ValidationError({"version": _("編集を始めた時点の版を送ってください")})
            task = self.instance.task
        if "name" in attrs:
            attrs["name"] = attrs["name"].strip()
        sent = attrs.pop("current_revision", {})
        current = self.instance.current_revision if self.instance else None
        content = {name: sent[name] if name in sent else getattr(current, name) for name in TRIGGER_REVISION_FIELDS}
        # タスクの今の版にない項目は取り出せない。確かめるのは作成時と取り出しを送ったときだけ（タスクから項目を
        # 消した後も、名前や上限は変えられるように。消した項目の取り出しは受付時に使わない）
        sent_inputs = sent.get("inputs", []) if self.instance else content["inputs"]
        if unknown := unknown_trigger_inputs(task, sent_inputs):
            raise serializers.ValidationError(
                {"inputs": _("タスクにない項目です：%(names)s") % {"names": ", ".join(unknown)}}
            )
        attrs["content"] = content
        return attrs

    @transaction.atomic
    def create(self, validated_data) -> TriggerBinding:
        # 同じタスクへの同時の作成を、タスクの行のロックで直列にしてから確かめ直す（1対1）
        task = (
            TaskDefinition.objects.select_for_update(of=("self",))
            .select_related("current_revision")
            .get(pk=validated_data["task"].pk)
        )
        if task.deleted or TriggerBinding.objects.alive().filter(task=task, kind=TriggerKind.WEBHOOK).exists():
            raise serializers.ValidationError({"task": _("このタスクには、もうWebhookの入口があります")})
        _check_failure_notice(task)
        binding, key = TriggerBinding.create_with_revision(
            task=validated_data["task"],
            name=validated_data["name"],
            actor=validated_data["actor"],
            change_reason=validated_data.get("change_reason", ""),
            content=validated_data["content"],
        )
        binding.issued_key = key  # type: ignore[attr-defined]
        return binding

    @transaction.atomic
    def update(self, instance: TriggerBinding, validated_data) -> TriggerBinding:
        task = (
            TaskDefinition.objects.select_for_update(of=("self",))
            .select_related("current_revision")
            .get(pk=instance.task_id)
        )
        _check_failure_notice(task)
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
        instance.save(update_fields=fields)
        return instance


def _check_failure_notice(task: TaskDefinition) -> None:
    """Webhookで起動するタスクは、失敗を知らせる先が必須（design.md §4.2）。タスクの行をロックしてから呼ぶ"""
    if task.current_revision is None or not checks.notifies_failure(task.current_revision):
        raise serializers.ValidationError(
            {
                "task": _(
                    "Webhookで起動するタスクには、失敗（run.failed）を知らせる通知先が必要です。"
                    "タスクの結果の通知を設定してください"
                )
            }
        )


class TriggerBindingUpdateSerializer(TriggerBindingSerializer):
    """入口の更新。タスクは変えられない"""

    task = serializers.SlugRelatedField(slug_field="uid", read_only=True)

    class Meta(TriggerBindingSerializer.Meta):
        pass

    def get_fields(self):
        # 部分更新でも、省いた項目に既定値を入れて今の版を上書きしないようにする
        fields = super().get_fields()
        for field in fields.values():
            if not field.read_only:
                field.required = False
                field.default = serializers.empty
        return fields


class TriggerBindingKeySerializer(TriggerBindingSerializer):
    """作成・鍵の作り直しの応答。鍵はこのときだけ返す（再表示はできない）"""

    key = serializers.CharField(source="issued_key", read_only=True, help_text=_("入口の鍵。いま一度だけ表示する"))

    class Meta(TriggerBindingSerializer.Meta):
        fields = [*TriggerBindingSerializer.Meta.fields, "key"]


class TriggerPreviewValueSerializer(serializers.Serializer):
    name = serializers.CharField()
    value = serializers.JSONField(help_text=_("確かめた後の値（受付時にタスクの入力になる値）"))


class TriggerPreviewErrorSerializer(serializers.Serializer):
    name = serializers.CharField()
    message = serializers.CharField()


class TriggerPreviewOutcome(models.TextChoices):
    """サンプルの要求を受付がどうするか"""

    ACCEPTED = "accepted", _("受け付ける")
    REFUSED = "refused", _("断る")
    # 必須のファイルの項目があり、JSONのサンプルでは確かめられない（ファイルはmultipartで付けるため）
    UNCONFIRMED = "unconfirmed", _("確かめられない")


class TriggerPreviewSerializer(serializers.Serializer):
    """サンプルのpayloadで取り出しを試す（design.md §4.2）。保存していない受け取り方（イベントIDの場所・項目の
    取り出し）で試せるように、それらも送れる（送らなければ今の版の値）。受付と同じ処理（payloadの読み取り、重複の
    キー、項目の取り出しと確かめ）を通し、何も保存しない。payloadはJSONだけ（ファイルはmultipartで付けるので、型が
    ファイルの項目は試せない）。

    名前は項目の名前のまま返すため、値と誤りは名前のキーの辞書ではなく並びにする（APIクライアントのcamelCaseの
    変換で名前を変えない）。埋め込んだ後のプロンプトは、タスクを管理できる人にだけ返す（連携の管理者がタスクの依頼文を
    見てよいとは限らない）"""

    sample = serializers.CharField(
        max_length=MAX_PAYLOAD_BYTES,
        trim_whitespace=False,
        write_only=True,
        help_text=_("サンプルのpayload（JSON）"),
    )
    event_id_path = serializers.CharField(
        max_length=200,
        required=False,
        allow_blank=True,
        write_only=True,
        help_text=_("イベントIDの場所。送らなければ今の版の値"),
    )
    inputs = TriggerInputSerializer(
        many=True, required=False, write_only=True, help_text=_("項目の取り出し。送らなければ今の版の値")
    )
    max_payload_bytes = serializers.IntegerField(
        min_value=1024,
        max_value=MAX_PAYLOAD_BYTES,
        required=False,
        write_only=True,
        help_text=_("payloadの大きさの上限（バイト）。送らなければ今の版の値"),
    )

    outcome = serializers.ChoiceField(
        choices=TriggerPreviewOutcome.choices,
        read_only=True,
        help_text=_(
            "受付がこの要求をどうするか（入口・タスクの停止、payloadを読めない・イベントIDを使えない・項目の誤りが"
            "あれば断る。ほかに断る理由がなく、必須のファイルの項目か、タスクの定義に断られうる設定があれば確かめられない）"
        ),
    )
    format_error = serializers.CharField(read_only=True, help_text=_("payloadを読めなかった理由（読めたら空）"))
    event_id = serializers.CharField(
        read_only=True, allow_null=True, help_text=_("重複の判定に使うイベントID（なければnull）")
    )
    event_id_error = serializers.CharField(read_only=True, help_text=_("イベントIDを使えない理由（使えれば空）"))
    values = TriggerPreviewValueSerializer(many=True, read_only=True)
    input_errors = TriggerPreviewErrorSerializer(
        many=True, read_only=True, help_text=_("項目ごとの誤り（受付では400で断られる）")
    )
    file_inputs = serializers.ListField(
        child=serializers.CharField(), read_only=True, help_text=_("型がファイルの項目（試せない）")
    )
    required_file_inputs = serializers.ListField(
        child=serializers.CharField(), read_only=True, help_text=_("型がファイルの必須の項目")
    )
    run_problems = serializers.ListField(
        child=serializers.CharField(),
        read_only=True,
        help_text=_(
            "入口・タスクの今の状態で、受付が断る・断りうる理由。入口・タスクの停止は断る。タスクの定義が保存のときの"
            "検査に通らない理由（Skillの停止等）は断りうる。定義の中身は、タスクを管理できる人にだけ詳しく返す"
        ),
    )
    duplicate = serializers.BooleanField(
        read_only=True,
        help_text=_("前に受け付けた要求と同じ（送ると前のRunを返し、新しいRunは作らない）"),
    )
    can_view_prompt = serializers.BooleanField(read_only=True)
    prompt = serializers.CharField(
        read_only=True,
        allow_null=True,
        help_text=_("埋め込んだ後のプロンプト（タスクを管理できる人だけ。断る要求ではnull）"),
    )

    def validate_event_id_path(self, value: str) -> str:
        if value:
            try:
                parse_path(value)
            except ValueError as exc:
                raise serializers.ValidationError(str(exc)) from exc
        return value

    def validate_inputs(self, value: list[dict[str, str]]) -> list[dict[str, str]]:
        return _check_trigger_inputs(value)  # 保存できない取り出しでは試さない（保存と同じく400）

    def preview(self, binding: TriggerBinding, user) -> dict[str, Any]:
        """受付（task.dispatch.receive_webhook()）と同じ順で確かめる：入口の停止 → payload → 前の受付との重複（同じ
        要求なら、ここで前のRunを返して終わる） → タスクの状態とRunを始められるか → 項目"""
        data = self.validated_data
        task = binding.task
        # 送った取り出しは保存と同じく、タスクの今の版にない項目を断る（保存できない受け取り方では試さない）
        if "inputs" in data and (unknown := unknown_trigger_inputs(task, data["inputs"])):
            raise serializers.ValidationError(
                {"inputs": _("タスクにない項目です：%(names)s") % {"names": ", ".join(unknown)}}
            )
        revision = task.current_revision
        current = binding.current_revision
        definitions = revision.inputs if revision else []
        files = [d for d in definitions if d["type"] == TaskInputType.FILE]
        can_view_prompt = TaskDefinition.objects.manageable_by(user).filter(pk=task.pk).exists()
        refusals: list[str] = []
        if binding.suspended or current is None:
            refusals.append(str(_("この入口は停止中です")))
        result: dict[str, Any] = {
            "outcome": TriggerPreviewOutcome.REFUSED,
            "format_error": "",
            "event_id": None,
            "event_id_error": "",
            "values": [],
            "input_errors": [],
            "file_inputs": [d["name"] for d in files],
            "required_file_inputs": [d["name"] for d in files if d.get("required")],
            "run_problems": refusals,
            "duplicate": False,
            "can_view_prompt": can_view_prompt,
            "prompt": None,
        }
        # 対にならないサロゲートは、サンプルの欄（CharField）が400で断るので、ここではUTF-8にできる
        body = data["sample"].encode()
        limit = data.get("max_payload_bytes", current.max_payload_bytes if current else DEFAULT_MAX_PAYLOAD_BYTES)
        if len(body) > limit:
            result["format_error"] = _("payloadが上限（%(limit)sバイト）を超えています") % {"limit": limit}
            return result
        try:
            payload = parse_payload(body, "application/json")
        except PayloadError as exc:
            result["format_error"] = exc.detail
            return result
        try:
            key = dedup_key(payload, data.get("event_id_path", current.event_id_path if current else ""), None)
            result["event_id"] = key.split(":", 1)[1] if key else None
            # 前の受付との重複（受付と同じ判定。読むだけ）。同じ要求なら、タスクの状態や項目によらず前のRunを返す
            if not refusals and received_run(binding, key, payload, None) is not None:
                result["duplicate"] = True
                result["outcome"] = TriggerPreviewOutcome.ACCEPTED
                return result
        except PayloadError as exc:
            if exc.code == "event_id_reused":  # 同じイベントIDで違う内容は409で断る
                refusals.append(exc.detail)
            else:
                result["event_id_error"] = exc.detail
        doubts: list[str] = []
        if not refusals:
            task_refusals, doubts = _task_problems(task, detailed=can_view_prompt)
            refusals += task_refusals
            result["run_problems"] = [*refusals, *doubts]
        mappings = data.get("inputs", current.inputs if current else [])
        cleaned, errors, absent_files = _preview_inputs(payload, mappings, definitions)
        order = [d["name"] for d in definitions]
        result["values"] = [{"name": name, "value": cleaned[name]} for name in order if name in cleaned]
        # 誤りは項目の定義の順に並べ、定義にない項目（推奨の形式の inputs で送られたもの）は最後に置く
        ranked = sorted(errors.items(), key=lambda e: order.index(e[0]) if e[0] in order else len(order))
        result["input_errors"] = [{"name": name, "message": message} for name, message in ranked]
        if refusals or result["event_id_error"] or errors:
            return result  # 断る要求ではRunを作らないので、プロンプトもない（欠けた値で埋めたものを見せない）
        unconfirmed = bool(doubts) or bool(absent_files & set(result["required_file_inputs"]))
        result["outcome"] = TriggerPreviewOutcome.UNCONFIRMED if unconfirmed else TriggerPreviewOutcome.ACCEPTED
        if can_view_prompt and revision is not None:
            result["prompt"] = render(revision.prompt_template, cleaned)
        return result


def _task_problems(task: TaskDefinition, *, detailed: bool) -> tuple[list[str], list[str]]:
    """タスクの今の状態で、受付が断る理由と、断りうる理由。

    断るのは、タスクの停止等と、Runを始められないもの（モデル設定・実行環境・Skill・Connector・通知先。受付と同じ
    task.dispatch.plan_run()）。断りうるのは、定義が保存のときの検査に通らないもの（受付は断らないものもある。受付時に
    既定に戻すeffort等）。理由はタスクの定義の中身に触れるので、`detailed`（タスクを管理できる人）のときだけ詳しく返す"""
    revision = task.current_revision
    if problem := checks.task_problem(task) or ("" if task.in_service() else _("タスクの今の版がありません")):
        return [str(problem)], []
    assert revision is not None  # in_service() が確かめた
    try:
        plan_run(task, revision, lock=False)  # 読むだけ（受付を待たせない）
    except CannotRun as exc:
        generic = _("タスクを今は実行できません（タスクの管理者に確かめてください）")
        return [str(exc) if detailed else str(generic)], []
    content = {name: getattr(revision, name) for name in REVISION_FIELDS}
    problems = [str(m) for messages in checks.content_errors(task, content).values() for m in messages]
    if problems and not detailed:
        return [], [str(_("タスクの定義に、実行を断られうる設定があります（タスクの管理者に確かめてください）"))]
    return [], problems


def _preview_inputs(
    payload, mappings: list[dict[str, str]], definitions: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, str], set[str]]:
    """受付と同じく項目を取り出して確かめる。（確かめた値、項目ごとの誤り、値のないファイルの項目）。

    型がファイルの項目は、JSONに値があれば受付と同じく断る（JSONの値はファイルの項目の値にしない）。値がない（null・
    空を含む）項目は、multipartで付けるファイルを指すので、ここでは確かめない"""
    values = input_values(payload, mappings, definitions)
    absent_files = {
        d["name"] for d in definitions if d["type"] == TaskInputType.FILE and values.get(d["name"]) in (None, "")
    }
    cleaned, errors = clean_inputs(
        [d for d in definitions if d["name"] not in absent_files],
        {name: value for name, value in values.items() if name not in absent_files},
    )
    return cleaned, errors, absent_files


class TriggerBindingStateSerializer(serializers.Serializer):
    """停止・再開・鍵の作り直し（本文なし）"""

    def update(self, instance: TriggerBinding, validated_data) -> TriggerBinding:
        if "suspended" in validated_data:
            current = TriggerBinding.objects.select_for_update(of=("self",)).get(pk=instance.pk)
            current.suspended = validated_data["suspended"]
            current.save(update_fields=["suspended", "updated"])
            instance.suspended = current.suspended
        if validated_data.get("rotate_key"):
            instance.issued_key = instance.rotate_key()  # type: ignore[attr-defined]
        return instance

    def create(self, validated_data):
        raise NotImplementedError


class TriggerBindingRevisionSerializer(ReadOnlyModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)
    inputs = TriggerInputSerializer(many=True, read_only=True)
    created_by = MinimumUserSerializer(read_only=True)

    class Meta:
        model = TriggerBindingRevision
        fields = ["id", "number", *TRIGGER_REVISION_FIELDS, "change_reason", "created_by", "created"]


class TriggerReceiptSerializer(ReadOnlyModelSerializer):
    """入口の受付の履歴（連携の管理者）。Runの結果・ログは返さない（タスクを管理できる人がタスクの画面で見る）"""

    id = serializers.CharField(source="uid", read_only=True)
    binding_version = serializers.IntegerField(source="binding_revision.number", read_only=True)
    run = serializers.CharField(source="run.uid", read_only=True)
    run_status = serializers.ChoiceField(source="run.status", choices=RunStatus.choices, read_only=True)
    run_status_display = serializers.CharField(source="run.get_status_display", read_only=True)

    class Meta:
        model = TriggerReceipt
        fields = [
            "id",
            "binding_version",
            "dedup_key",
            "payload_size",
            "content_type",
            "run",
            "run_status",
            "run_status_display",
            "created",
        ]


class HookRunSerializer(serializers.Serializer):
    """Webhookの送り元に返すRunの状態（authorization.md §7.1）。状態と決まった失敗のコードだけで、エラーの本文や
    ログ由来の情報は返さない"""

    run_id = serializers.CharField(source="uid")
    status = serializers.SerializerMethodField(
        help_text=_("queued・running・waiting_user・retry_wait・cancelling・completed・failed・cancelled")
    )
    failure_code = serializers.SerializerMethodField(help_text=_("失敗・停止したときの終わり方（例：step_limit）"))

    def get_status(self, obj: Run) -> str:
        return RunStatus(obj.status).name.lower()

    def get_failure_code(self, obj: Run) -> str | None:
        if obj.status in (RunStatus.FAILED, RunStatus.CANCELLED) and obj.end_reason is not None:
            return RunEndReason(obj.end_reason).name.lower()
        return None


class HookReceivedSerializer(HookRunSerializer):
    duplicate = serializers.BooleanField(help_text=_("前に受け付けた要求の再送（新しいRunは作っていない）"))


class HookErrorSerializer(serializers.Serializer):
    code = serializers.CharField(help_text=_("決まった誤りのコード（例：invalid_inputs）"))
    detail = serializers.CharField()
    inputs = serializers.DictField(
        child=serializers.CharField(), required=False, help_text=_("入力の項目ごとの誤り（invalid_inputs のとき）")
    )
