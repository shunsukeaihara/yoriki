import re
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

import jsonschema
from django.conf import settings
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from connector.models import Connector, ConnectorAction, Operation
from harness.tools import SKILL_TOOLS
from harness.tools.base import schema_problem
from llm.models import ModelProfile
from resources import formats
from resources.models import Resource, ResourceVersion
from user.models import Department
from user.serializers import MinimumDepartmentSerializer, MinimumUserSerializer
from utils.exceptions import Conflict
from utils.fields import NormalizedTextField
from utils.serializers import ReadOnlyModelSerializer

from . import connectors as skill_connectors
from .errors import CannotEvaluate, DraftExists, InvalidDraft, VersionConflict
from .models import (
    MAX_CASES_PER_EVALUATION,
    MAX_EVALUATION_CASES,
    Skill,
    SkillAction,
    SkillAssetBinding,
    SkillChange,
    SkillContent,
    SkillDraft,
    SkillDraftAsset,
    SkillEvaluation,
    SkillEvaluationCase,
    SkillEvaluationResult,
    SkillRequirement,
    SkillScope,
    SkillVersion,
)

# 資料のkey。Sandboxの中のパス（/workspace/master/skills/<Skill>/<key>/）にも使う
ASSET_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
TOOL_CHOICES = [(t.name, t.name) for t in SKILL_TOOLS]
MAX_INSTRUCTIONS = 100_000


def _schema_or_none(value: Any) -> Any:
    """入出力のSchema。JSON Schemaとして正しいことを確かめる（業務結果は submit_result の引数になる）"""
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
    return value


def _can(skill: Skill, context: Mapping[str, Any], action: SkillAction) -> bool:
    request = context.get("request")
    return request is not None and skill.can(request.user, action)


# --- 版と資料 ---


class ResourceVersionSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)
    resource = serializers.CharField(source="resource.uid", read_only=True)
    size = serializers.IntegerField(read_only=True)
    sha256 = serializers.CharField(read_only=True)
    created_by: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(slug_field="uid", read_only=True)

    class Meta:
        model = ResourceVersion
        fields = ["id", "resource", "number", "name", "content_type", "size", "sha256", "created_by", "created"]
        read_only_fields = fields


class ColumnTypeSerializer(serializers.Serializer):
    column = serializers.CharField(max_length=200)
    type = serializers.ChoiceField(choices=formats.COLUMN_TYPES)


class AssetChecksSerializer(serializers.Serializer):
    """資料の検証の条件（design.md §3.9）。CSVだけが使う。公開の権限を持つ人だけが変えられる"""

    encoding = serializers.ChoiceField(
        choices=["", *formats.CSV_ENCODINGS], required=False, allow_blank=True, help_text=_("空なら自動で判別する")
    )
    delimiter = serializers.ChoiceField(
        choices=["", *formats.CSV_DELIMITER_CHOICES],
        required=False,
        allow_blank=True,
        help_text=_("空なら自動で判別する"),
    )
    required_columns = serializers.ListField(
        child=serializers.CharField(max_length=200), required=False, max_length=100
    )
    key_columns = serializers.ListField(
        child=serializers.CharField(max_length=200),
        required=False,
        max_length=10,
        help_text=_("行を識別する列。重複・空を誤りにし、差分を行ごとに取る"),
    )
    column_types = ColumnTypeSerializer(many=True, required=False, help_text=_("列ごとの値の型（文字列なら省く）"))

    def validate(self, attrs):
        return formats.normalize_checks(attrs)


class CellDiffSerializer(serializers.Serializer):
    column = serializers.CharField()
    before = serializers.CharField(allow_null=True)
    after = serializers.CharField(allow_null=True)


class RowDiffSerializer(serializers.Serializer):
    change = serializers.ChoiceField(choices=formats.DIFF_CHANGES)
    key = serializers.CharField(help_text=_("キーの列の値。キーがなければ空"))
    line = serializers.IntegerField(allow_null=True, help_text=_("行番号（追加・変更は新しい版、削除は前の版）"))
    cells = CellDiffSerializer(many=True)


class TextDiffSerializer(serializers.Serializer):
    shape = serializers.CharField()
    before = serializers.CharField(allow_null=True)
    after = serializers.CharField(allow_null=True)


class ElementDiffSerializer(serializers.Serializer):
    kind = serializers.CharField()
    before = serializers.IntegerField()
    after = serializers.IntegerField()


class SlideDiffSerializer(serializers.Serializer):
    slide = serializers.IntegerField(help_text=_("スライドの番号（削除は前の版）"))
    change = serializers.ChoiceField(choices=formats.DIFF_CHANGES)
    layout_before = serializers.CharField(allow_null=True)
    layout_after = serializers.CharField(allow_null=True)
    texts = TextDiffSerializer(many=True)
    elements = ElementDiffSerializer(many=True)


class AssetDiffSerializer(serializers.Serializer):
    """前の版の同じ資料との差分。形式（MD・CSV・PPTX）によって持つ項目が違う"""

    identical = serializers.BooleanField(required=False)
    truncated = serializers.BooleanField(required=False, help_text=_("多すぎて一部だけを載せた"))
    lines_added = serializers.IntegerField(required=False)
    lines_removed = serializers.IntegerField(required=False)
    text = serializers.ListField(
        child=serializers.CharField(allow_blank=True), required=False, help_text=_("MDの本文の差分（unified diff）")
    )
    columns_added = serializers.ListField(child=serializers.CharField(), required=False)
    columns_removed = serializers.ListField(child=serializers.CharField(), required=False)
    rows_added = serializers.IntegerField(required=False)
    rows_removed = serializers.IntegerField(required=False)
    rows_changed = serializers.IntegerField(required=False)
    rows = RowDiffSerializer(many=True, required=False)
    slides_added = serializers.IntegerField(required=False)
    slides_removed = serializers.IntegerField(required=False)
    slides_changed = serializers.IntegerField(required=False)
    reordered = serializers.BooleanField(required=False)
    slides = SlideDiffSerializer(many=True, required=False)


class AssetDiffCountSerializer(serializers.Serializer):
    """置き換えた資料の差分の数（形式によって持つ項目が違う）"""

    key = serializers.CharField(help_text=_("資料のkey"))
    lines_added = serializers.IntegerField(required=False)
    lines_removed = serializers.IntegerField(required=False)
    rows_added = serializers.IntegerField(required=False)
    rows_removed = serializers.IntegerField(required=False)
    rows_changed = serializers.IntegerField(required=False)
    slides_added = serializers.IntegerField(required=False)
    slides_removed = serializers.IntegerField(required=False)
    slides_changed = serializers.IntegerField(required=False)


def asset_diff_items(diffs: dict[str, dict[str, int]] | None) -> list[dict[str, Any]]:
    """資料のkeyごとの差分の数を、keyを値に持つ並びにする（資料のkeyを辞書のキーのままAPIに出さない）"""
    return list(AssetDiffCountSerializer([{"key": k, **v} for k, v in sorted((diffs or {}).items())], many=True).data)


class AssetValidationSerializer(serializers.Serializer):
    """資料の検証の結果（resources.formats.inspect()）。検証する前に公開した資料では空"""

    format = serializers.ChoiceField(choices=formats.FORMATS, required=False)
    edited = serializers.BooleanField(required=False, help_text=_("エージェントが元の資料を編集して作った候補か"))
    errors = serializers.ListField(  # type: ignore[assignment]
        child=serializers.CharField(), required=False, help_text=_("誤り。あれば公開できない")
    )
    warnings = serializers.ListField(child=serializers.CharField(), required=False)
    info = serializers.DictField(required=False, help_text=_("中身の概要（文字コード・列・行数・スライド数等）"))
    diff = AssetDiffSerializer(required=False, allow_null=True)


class SkillAssetSerializer(serializers.ModelSerializer):
    """版の資料（表示）"""

    role_display = serializers.CharField(source="get_role_display", read_only=True)
    file = ResourceVersionSerializer(source="resource_version", read_only=True)
    checks = AssetChecksSerializer(read_only=True)
    validation = AssetValidationSerializer(read_only=True)

    class Meta:
        model = SkillAssetBinding
        fields = ["key", "role", "role_display", "description", "file", "checks", "validation"]
        read_only_fields = fields


class ConnectorDeclarationSerializer(serializers.Serializer):
    """Skillが宣言するConnectorと操作・表（design.md §4.9.1）"""

    connector = serializers.CharField(max_length=30, help_text=_("ConnectorのID"))
    operations = serializers.ListField(
        child=serializers.ChoiceField(choices=Operation.choices), max_length=len(Operation.choices)
    )
    tables = serializers.ListField(
        child=serializers.CharField(max_length=300),
        required=False,
        max_length=skill_connectors.MAX_DECLARED_TABLES,
        help_text=_("使う表（`スキーマ.表`）。空ならConnectorの公開する表のどれでも"),
    )


class DeclaredConnectorSerializer(serializers.Serializer):
    """宣言したConnectorの表示。消えたConnectorは名前を空にする"""

    connector = serializers.CharField()
    name = serializers.CharField()
    is_sensitive = serializers.BooleanField(help_text=_("Connectorの今の版が機密か"))
    operations = serializers.ListField(child=serializers.CharField())
    tables = serializers.ListField(child=serializers.CharField())


def declared_connectors(content) -> list[dict[str, Any]]:
    connectors = skill_connectors.load([content])
    items = []
    for declaration in content.connectors or []:
        connector = connectors.get(declaration["connector"])
        version = connector.current_version if connector and not connector.deleted else None
        items.append(
            {
                "connector": declaration["connector"],
                "name": connector.name if connector and not connector.deleted else "",
                "is_sensitive": bool(version and version.is_sensitive),
                "operations": declaration["operations"],
                "tables": declaration.get("tables", []),
            }
        )
    return items


class SkillContentSerializer(serializers.ModelSerializer):
    """Draftと版に共通の中身"""

    description = NormalizedTextField(max_length=2000, required=False, allow_blank=True)
    instructions = NormalizedTextField(max_length=MAX_INSTRUCTIONS, required=False, allow_blank=True)
    tools = serializers.ListField(
        child=serializers.ChoiceField(choices=TOOL_CHOICES),
        required=False,
        max_length=50,
        help_text=_("使ってよいTool。空ならコマンド不要のSkill"),
    )
    requires = serializers.ListField(
        child=serializers.ChoiceField(choices=SkillRequirement.choices), required=False, max_length=10
    )
    input_schema = serializers.JSONField(required=False, allow_null=True)
    output_schema = serializers.JSONField(required=False, allow_null=True)
    connectors = ConnectorDeclarationSerializer(
        many=True,
        required=False,
        help_text=_("使うConnectorと操作・表の宣言。宣言したConnectorを使えない人には、このSkillを出さない"),
    )
    is_sensitive = serializers.BooleanField(
        required=False, help_text=_("機密。付けるのは編集の権限、外すのは公開の権限（authorization.md §5.1）")
    )

    def validate_tools(self, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))  # 重ねずに、指定の順を保つ

    def validate_requires(self, value: list[int]) -> list[int]:
        return sorted(set(value))

    def validate_input_schema(self, value: Any) -> Any:
        return _schema_or_none(value)

    def validate_connectors(self, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """宣言できるのは、宣言する人が使えるConnector（システムのConnectorは見る権限を持つ人）だけ。範囲と境界は
        公開するときに確かめる（Connectorの版は後で変わるため）"""
        request = self.context.get("request")
        uids = [d["connector"] for d in value]
        if len(uids) > skill_connectors.MAX_DECLARED_CONNECTORS:
            raise serializers.ValidationError(
                _("宣言できるConnectorは%(limit)s個までです") % {"limit": skill_connectors.MAX_DECLARED_CONNECTORS}
            )
        if len(set(uids)) != len(uids):
            raise serializers.ValidationError(_("同じConnectorを2回宣言しています"))
        found = Connector.objects.filter(uid__in=uids, deleted=False).select_related("connection", "department")
        connectors = {c.uid: c for c in found}
        declared = []
        for declaration in value:
            connector = connectors.get(declaration["connector"])
            if connector is None or request is None or not skill_connectors.usable_by_declarer(connector, request.user):
                raise serializers.ValidationError(
                    _("宣言できないConnectorがあります（使う権限がないか、削除されました）")
                )
            if not declaration["operations"]:
                raise serializers.ValidationError(
                    _("Connector「%(name)s」で使う操作を選んでください") % {"name": connector.name}
                )
            declared.append(
                {
                    "connector": connector.uid,
                    "operations": list(dict.fromkeys(declaration["operations"])),
                    "tables": list(dict.fromkeys(t.strip() for t in declaration.get("tables", []) if t.strip())),
                }
            )
        return declared

    def validate_output_schema(self, value: Any) -> Any:
        value = _schema_or_none(value)
        # 業務結果のSchemaは submit_result の引数になるので、全プロバイダで通る範囲に限る（harness.md §7.3）
        if value is not None and (problem := schema_problem(value)):
            raise serializers.ValidationError(
                _("業務結果のSchemaに使えない書き方があります：%(problem)s") % {"problem": problem}
            )
        return value


class SkillVersionSerializer(ReadOnlyModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)
    assets = SkillAssetSerializer(many=True, read_only=True)
    connectors = serializers.SerializerMethodField(help_text=_("使うConnectorと操作・表の宣言"))
    sensitive = serializers.SerializerMethodField(
        help_text=_("機密のSkillか（機密のフラグか、宣言したConnectorの今の版が機密）")
    )
    rolled_back_from = serializers.IntegerField(source="rolled_back_from.number", read_only=True, allow_null=True)
    created_by_info = MinimumUserSerializer(source="created_by", read_only=True)

    class Meta:
        model = SkillVersion
        fields = [
            "id",
            "number",
            "description",
            "instructions",
            "tools",
            "requires",
            "input_schema",
            "output_schema",
            "connectors",
            "is_sensitive",
            "sensitive",
            "assets",
            "rolled_back_from",
            "change_reason",
            "created_by_info",
            "created",
        ]

    @extend_schema_field(DeclaredConnectorSerializer(many=True))
    def get_connectors(self, obj: SkillVersion) -> list[dict[str, Any]]:
        return declared_connectors(obj)

    def get_sensitive(self, obj: SkillVersion) -> bool:
        return skill_connectors.is_sensitive(obj)


class MinimumSkillVersionSerializer(serializers.ModelSerializer):
    """版の一覧（中身は出さない）"""

    id = serializers.CharField(source="uid", read_only=True)
    rolled_back_from = serializers.IntegerField(source="rolled_back_from.number", read_only=True, allow_null=True)
    created_by_info = MinimumUserSerializer(source="created_by", read_only=True)

    class Meta:
        model = SkillVersion
        fields = ["id", "number", "rolled_back_from", "change_reason", "created_by_info", "created"]
        read_only_fields = fields


# --- Skill ---


class SkillSerializer(serializers.ModelSerializer):
    """Skillと公開中の版の概要。中身は版（versions）とDraft（drafts）で扱う"""

    id = serializers.CharField(source="uid", read_only=True)
    scope_display = serializers.CharField(source="get_scope_display", read_only=True)
    owner_info = MinimumUserSerializer(source="owner", read_only=True, allow_null=True)
    department_info = MinimumDepartmentSerializer(source="department", read_only=True, allow_null=True)
    version = serializers.IntegerField(source="version_number", read_only=True, help_text=_("公開中の版。未公開は0"))
    description = serializers.CharField(source="current_version.description", read_only=True, allow_null=True)
    draft = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField(help_text=_("Draftを変更できるか（本文・資料・評価ケース）"))
    can_publish = serializers.SerializerMethodField(
        help_text=_("公開・切戻し・停止・削除、名前とToolの変更、資料の検証の条件と必須の評価ケースの変更ができるか")
    )
    sensitive = serializers.SerializerMethodField(help_text=_("公開中の版が機密のSkillか（機密の会話でだけ使える）"))
    edit_sensitive = serializers.SerializerMethodField(
        help_text=_("公開中の版かDraftが機密のSkillか（機密の会話でだけ編集できる）")
    )
    connectors_usable = serializers.SerializerMethodField(
        help_text=_("公開中の版が宣言したConnectorを、利用者がすべて使えるか（使えなければ会話で使えない）")
    )

    class Meta:
        model = Skill
        fields = [
            "id",
            "scope",
            "scope_display",
            "owner_info",
            "department_info",
            "name",
            "version",
            "description",
            "suspended",
            "sensitive",
            "edit_sensitive",
            "connectors_usable",
            "draft",
            "can_edit",
            "can_publish",
            "created",
            "updated",
        ]
        read_only_fields = ["scope", "suspended", "created", "updated"]

    @staticmethod
    def _contents(skill: Skill) -> list[SkillContent]:
        """公開中の版と開いているDraft"""
        drafts = getattr(skill, "open_drafts", None)
        contents: list[SkillContent] = list(skill.drafts.all()) if drafts is None else list(drafts)
        if skill.current_version is not None:
            contents.append(skill.current_version)
        return contents

    def _declared(self, obj: Skill, contents: list[SkillContent]) -> dict[str, Connector]:
        """公開中の版とDraftが宣言したConnector。一覧ではまとめて1回で読む（件数でクエリを増やさない）"""
        # 一覧の各行は同じ子のSerializerで出すので、子に持たせる
        declared: dict[str, Connector] | None = getattr(self, "_declared_connectors", None)
        if declared is None:
            parent = self.parent if isinstance(self.parent, serializers.ListSerializer) else None
            skills: list[Skill] = list(parent.instance) if parent is not None and parent.instance is not None else [obj]
            declared = self._declared_connectors = skill_connectors.load(c for s in skills for c in self._contents(s))
        if any(uid not in declared for content in contents for uid in skill_connectors.declared_uids(content)):
            declared = skill_connectors.load(contents)
        return declared

    def get_sensitive(self, obj: Skill) -> bool:
        version = obj.current_version
        return version is not None and skill_connectors.is_sensitive(version, self._declared(obj, [version]))

    def get_edit_sensitive(self, obj: Skill) -> bool:
        contents = self._contents(obj)
        declared = self._declared(obj, contents)
        return any(skill_connectors.is_sensitive(content, declared) for content in contents)

    def get_connectors_usable(self, obj: Skill) -> bool:
        version = obj.current_version
        request = self.context.get("request")
        if version is None or request is None or not version.connectors:
            return True
        declared = self._declared(obj, [version])
        return all(
            (c := declared.get(uid)) is not None and c.can(request.user, ConnectorAction.USE)
            for uid in skill_connectors.declared_uids(version)
        )

    def get_draft(self, obj: Skill) -> str | None:
        """開いているDraftのID。なければnull。Draftを編集できる人にだけ出す"""
        if not _can(obj, self.context, SkillAction.EDIT):
            return None
        drafts = getattr(obj, "open_drafts", None)
        if drafts is None:
            drafts = list(obj.drafts.all())
        return drafts[0].uid if drafts else None

    def get_can_edit(self, obj: Skill) -> bool:
        return _can(obj, self.context, SkillAction.EDIT)

    def get_can_publish(self, obj: Skill) -> bool:
        return _can(obj, self.context, SkillAction.PUBLISH)

    def validate_name(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError(_("名前を入力してください"))
        if self.instance is not None:
            _check_unique_name(self.instance.scope, self.instance.owner, self.instance.department, value, self.instance)
        return value


def _check_unique_name(scope, owner, department, name: str, instance: Skill | None = None) -> None:
    others = Skill.objects.filter(deleted=False, scope=scope, name=name)
    if scope == SkillScope.PERSONAL:
        others = others.filter(owner=owner)
    elif scope == SkillScope.DEPARTMENT:
        others = others.filter(department=department)
    if instance is not None:
        others = others.exclude(pk=instance.pk)
    if others.exists():
        raise serializers.ValidationError(_("同じ範囲に同じ名前のSkillがあります"))


class SkillCreateSerializer(SkillContentSerializer):
    """Skillと最初のDraftを作る。公開するまで、誰も使えない"""

    scope = serializers.ChoiceField(choices=SkillScope.choices)
    department: "serializers.SlugRelatedField[Department]" = serializers.SlugRelatedField(
        slug_field="uid",
        queryset=Department.objects.filter(deleted=False),
        required=False,
        allow_null=True,
        help_text=_("部門の範囲では必須"),
    )
    name = serializers.CharField(max_length=100)

    class Meta:
        model = SkillDraft
        fields = [
            "scope",
            "department",
            "name",
            "description",
            "instructions",
            "tools",
            "requires",
            "input_schema",
            "output_schema",
        ]

    def validate(self, attrs):
        user = self.context["request"].user
        scope = SkillScope(attrs["scope"])
        department = attrs.get("department") if scope == SkillScope.DEPARTMENT else None
        if scope == SkillScope.DEPARTMENT and department is None:
            raise serializers.ValidationError({"department": _("部門を選んでください")})
        name = attrs["name"].strip()
        _check_unique_name(scope, user, department, name)
        # 作る人がその範囲で持つ権限。未保存のSkillで判定する（authorization.md §5.1）
        new = Skill(scope=scope, owner=user if scope == SkillScope.PERSONAL else None, department=department)
        if not new.can(user, SkillAction.EDIT):
            raise PermissionDenied(_("この範囲にSkillを作る権限がありません"))
        if attrs.get("tools") and not new.can(user, SkillAction.PUBLISH):
            raise PermissionDenied(_("使ってよいToolを決めるには、この範囲の公開の権限が要ります"))
        attrs["name"] = name
        attrs["department"] = department
        return attrs

    def create(self, validated_data) -> Skill:
        return Skill.create_with_draft(actor=validated_data.pop("actor"), **validated_data)

    def update(self, instance, validated_data):
        raise NotImplementedError("Skillの更新は SkillSerializer で行う")

    def to_representation(self, instance: Skill):
        return SkillSerializer(instance, context=self.context).data


class SkillPublishSerializer(serializers.Serializer):
    draft = serializers.CharField(max_length=30, help_text=_("公開するDraftのID"))
    revision = serializers.IntegerField(min_value=1, help_text=_("編集を始めた時点のDraftのrevision"))
    change_reason = serializers.CharField(max_length=500, required=False, allow_blank=True)
    operation_key = serializers.CharField(
        max_length=100, required=False, allow_blank=True, help_text=_("同じ操作の再送で版を重ねて作らないためのkey")
    )

    def create(self, validated_data) -> SkillVersion:
        skill: Skill = validated_data.pop("skill")
        try:
            return skill.publish_draft(
                validated_data["draft"],
                actor=validated_data["actor"],
                expected_revision=validated_data["revision"],
                change_reason=validated_data.get("change_reason", ""),
                operation_key=validated_data.get("operation_key", ""),
            )
        except VersionConflict as exc:
            raise Conflict() from exc
        except InvalidDraft as exc:
            raise serializers.ValidationError({"non_field_errors": [str(exc)]}) from exc

    def update(self, instance, validated_data):
        raise NotImplementedError("版は更新しない")

    def to_representation(self, instance: SkillVersion):
        return SkillVersionSerializer(instance, context=self.context).data


class SkillRollbackSerializer(serializers.Serializer):
    number = serializers.IntegerField(min_value=1, help_text=_("切り戻す版"))
    expected_version = serializers.IntegerField(min_value=1, help_text=_("操作を始めた時点の公開中の版"))
    change_reason = serializers.CharField(max_length=500, required=False, allow_blank=True)
    operation_key = serializers.CharField(max_length=100, required=False, allow_blank=True)

    def create(self, validated_data) -> SkillVersion:
        skill: Skill = validated_data.pop("skill")
        try:
            return skill.rollback(
                validated_data["number"],
                actor=validated_data["actor"],
                expected_version=validated_data["expected_version"],
                change_reason=validated_data.get("change_reason", ""),
                operation_key=validated_data.get("operation_key", ""),
            )
        except VersionConflict as exc:
            raise Conflict() from exc
        except InvalidDraft as exc:
            raise serializers.ValidationError({"number": [str(exc)]}) from exc

    def update(self, instance, validated_data):
        raise NotImplementedError("版は更新しない")

    def to_representation(self, instance: SkillVersion):
        return SkillVersionSerializer(instance, context=self.context).data


class SkillSuspendSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)

    def update(self, instance: Skill, validated_data) -> Skill:
        instance.set_suspended(
            validated_data["suspended"], actor=validated_data["actor"], reason=validated_data.get("reason", "")
        )
        return instance

    def create(self, validated_data):
        raise NotImplementedError("instanceを渡して使う")

    def to_representation(self, instance: Skill):
        return SkillSerializer(instance, context=self.context).data


class SkillChangeSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    version = serializers.IntegerField(source="version.number", read_only=True, allow_null=True)
    previous_version = serializers.IntegerField(source="previous_version.number", read_only=True, allow_null=True)
    actor_info = MinimumUserSerializer(source="actor", read_only=True)
    summary = serializers.SerializerMethodField()
    asset_diffs = serializers.SerializerMethodField()

    class Meta:
        model = SkillChange
        fields = [
            "id",
            "kind",
            "kind_display",
            "version",
            "previous_version",
            "actor_info",
            "reason",
            "summary",
            "asset_diffs",
            "created",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.DictField(child=serializers.ListField(child=serializers.CharField())))
    def get_summary(self, obj: SkillChange) -> dict[str, list[str]]:
        """変わった項目（fields）と資料のkey（assets_added・assets_removed・assets_replaced・assets_changed）"""
        return {k: v for k, v in obj.summary.items() if k != "asset_diffs"}

    @extend_schema_field(AssetDiffCountSerializer(many=True))
    def get_asset_diffs(self, obj: SkillChange) -> list[dict[str, Any]]:
        """置き換えた資料の差分の数"""
        return asset_diff_items(obj.summary.get("asset_diffs"))


# --- Draft ---


class SkillDraftAssetSerializer(serializers.ModelSerializer):
    """Draftの資料。作成ではファイルを送る（multipart/form-data）。更新でファイルを送ると、同じ資料の新しい版で置き換える"""

    key = serializers.CharField(max_length=63, help_text=_("Skillの中で資料を指す名前（英小文字・数字・-・_）"))
    role_display = serializers.CharField(source="get_role_display", read_only=True)
    description = NormalizedTextField(max_length=500, required=False, allow_blank=True)
    file = serializers.FileField(write_only=True, help_text=_("資料のファイル。原本のまま保存する"))
    file_info = ResourceVersionSerializer(source="resource_version", read_only=True)
    revision = serializers.IntegerField(
        min_value=1, write_only=True, help_text=_("編集を始めた時点のDraftのrevision。その間に変わっていたら409")
    )
    draft_revision = serializers.IntegerField(
        source="draft.revision", read_only=True, help_text=_("変更した後のDraftのrevision")
    )
    checks = serializers.SerializerMethodField()
    validation = serializers.SerializerMethodField()

    class Meta:
        model = SkillDraftAsset
        fields = [
            "key",
            "role",
            "role_display",
            "description",
            "file",
            "file_info",
            "revision",
            "draft_revision",
            "checks",
            "validation",
        ]

    # 入力にも使うSerializerなので、表示だけの入れ子はメソッドで出す（入力用の型を作らせない）
    @extend_schema_field(AssetChecksSerializer)
    def get_checks(self, obj: SkillDraftAsset) -> dict[str, Any]:
        """検証の条件。変えるには checks の操作を使う（公開の権限が要る）"""
        return AssetChecksSerializer(formats.normalize_checks(obj.checks)).data

    @extend_schema_field(AssetValidationSerializer)
    def get_validation(self, obj: SkillDraftAsset) -> dict[str, Any]:
        """検証の結果と、Draftの元の版の同じ資料との差分"""
        return AssetValidationSerializer(obj.validation).data

    def validate_key(self, value: str) -> str:
        if not ASSET_KEY_RE.match(value):
            raise serializers.ValidationError(_("英小文字・数字・-・_ で、英小文字か数字から始めてください"))
        return value

    def validate(self, attrs):
        if "revision" not in attrs:
            # 部分更新（PATCH）では必須の項目も省けるので、ここで確かめる
            raise serializers.ValidationError({"revision": _("編集を始めた時点のrevisionを送ってください")})
        return attrs

    def validate_file(self, value):
        limit = settings.YORIKI_UPLOAD_MAX_MB
        if value.size > limit * 1024 * 1024:
            raise serializers.ValidationError(_("ファイルが大きすぎます（%(limit)sMBまで）") % {"limit": limit})
        return value

    def create(self, validated_data) -> SkillDraftAsset:
        draft: SkillDraft = validated_data["draft"]
        file = validated_data["file"]
        try:
            return draft.add_asset(
                key=validated_data["key"],
                role=validated_data["role"],
                description=validated_data.get("description", ""),
                src=file,
                filename=file.name or validated_data["key"],
                actor=validated_data["actor"],
                expected_revision=validated_data["revision"],
            )
        except VersionConflict as exc:
            raise Conflict() from exc
        except InvalidDraft as exc:
            raise serializers.ValidationError({"key": [str(exc)]}) from exc

    def update(self, instance: SkillDraftAsset, validated_data) -> SkillDraftAsset:
        file = validated_data.get("file")
        try:
            return instance.draft.change_asset(
                instance,
                actor=validated_data["actor"],
                expected_revision=validated_data["revision"],
                role=validated_data.get("role"),
                description=validated_data.get("description"),
                src=file,
                filename=file.name if file else "",
            )
        except VersionConflict as exc:
            raise Conflict() from exc


class SkillDraftAssetChecksSerializer(serializers.Serializer):
    """資料の検証の条件を変える（公開の権限を持つ人だけ）。変えると検証し直す"""

    revision = serializers.IntegerField(min_value=1, help_text=_("編集を始めた時点のDraftのrevision"))
    checks = AssetChecksSerializer()

    def validate(self, attrs):
        asset: SkillDraftAsset = self.instance  # type: ignore[assignment]
        if attrs["checks"] != formats.normalize_checks(asset.checks) and not _can(
            asset.draft.skill, self.context, SkillAction.PUBLISH
        ):
            # 条件を緩めて誤りを隠せないように、本文を編集できる人でも条件は変えられない（design.md §3.9）
            raise PermissionDenied(_("検証の条件を変えるには、この範囲の公開の権限が要ります"))
        return attrs

    def update(self, instance: SkillDraftAsset, validated_data) -> SkillDraftAsset:
        try:
            return instance.draft.change_asset(
                instance,
                actor=validated_data["actor"],
                expected_revision=validated_data["revision"],
                checks=validated_data["checks"],
            )
        except VersionConflict as exc:
            raise Conflict() from exc

    def create(self, validated_data):
        raise NotImplementedError("instanceを渡して使う")

    def to_representation(self, instance: SkillDraftAsset):
        return SkillDraftAssetSerializer(instance, context=self.context).data


class SkillDraftRevisionSerializer(serializers.Serializer):
    """Draftを破棄する・資料を外すときの、編集を始めた時点のDraftのrevision（クエリ文字列）"""

    revision = serializers.IntegerField(min_value=1)


class SkillDraftAssetUpdateSerializer(SkillDraftAssetSerializer):
    """keyは変えない。ファイルは置き換えるときだけ送る"""

    # 宣言した項目には read_only_fields が効かないので、宣言し直す
    key = serializers.CharField(read_only=True)
    file = serializers.FileField(write_only=True, required=False, help_text=_("置き換えるファイル"))


class MinimumEvaluationCaseSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)

    class Meta:
        model = SkillEvaluationCase
        fields = ["id", "name"]
        read_only_fields = fields


class SkillDraftSerializer(SkillContentSerializer):
    """編集中のSkill。更新では、編集を始めた時点の `revision` を送り、その間に変わっていたら409になる"""

    id = serializers.CharField(source="uid", read_only=True)
    base_version = serializers.IntegerField(
        source="base_version.number",
        read_only=True,
        allow_null=True,
        help_text=_("開いたときの公開中の版。公開するときに、これが今も公開中の版でなければ409"),
    )
    revision = serializers.IntegerField(min_value=1, help_text=_("更新では、編集を始めた時点の値を送る"))
    assets = SkillDraftAssetSerializer(many=True, read_only=True)
    updated_by_info = MinimumUserSerializer(source="updated_by", read_only=True)
    unevaluated_cases = serializers.SerializerMethodField(
        help_text=_("公開の前に必須の評価ケースのうち、今のrevisionで通過していないもの（あれば公開できない）")
    )
    connectors_info = serializers.SerializerMethodField(help_text=_("宣言したConnectorの名前と機密のフラグ"))
    sensitive = serializers.SerializerMethodField(
        help_text=_("機密のSkillか（機密のフラグか、宣言したConnectorの今の版が機密）")
    )
    connector_problems = serializers.SerializerMethodField(
        help_text=_("宣言したConnectorの、公開するときに止まる問題（範囲・境界・Tool）")
    )

    class Meta:
        model = SkillDraft
        fields = [
            "id",
            "base_version",
            "revision",
            "description",
            "instructions",
            "tools",
            "requires",
            "input_schema",
            "output_schema",
            "connectors",
            "connectors_info",
            "connector_problems",
            "is_sensitive",
            "sensitive",
            "assets",
            "updated_by_info",
            "unevaluated_cases",
            "created",
            "updated",
        ]
        read_only_fields = ["created", "updated"]

    @extend_schema_field(MinimumEvaluationCaseSerializer(many=True))
    def get_unevaluated_cases(self, obj: SkillDraft) -> list[dict[str, Any]]:
        return list(MinimumEvaluationCaseSerializer(obj.unevaluated_cases(), many=True).data)

    @extend_schema_field(DeclaredConnectorSerializer(many=True))
    def get_connectors_info(self, obj: SkillDraft) -> list[dict[str, Any]]:
        return declared_connectors(obj)

    def get_sensitive(self, obj: SkillDraft) -> bool:
        return skill_connectors.is_sensitive(obj)

    def get_connector_problems(self, obj: SkillDraft) -> list[str]:
        request = self.context.get("request")
        if request is None or not obj.connectors:
            return []
        return skill_connectors.publish_errors(obj, obj.skill, request.user)

    def validate(self, attrs):
        draft = self.instance
        assert draft is not None
        if "revision" not in attrs:
            # 部分更新（PATCH）では必須の項目も省けるので、ここで確かめる
            raise serializers.ValidationError({"revision": _("編集を始めた時点のrevisionを送ってください")})
        if (
            "tools" in attrs
            and attrs["tools"] != draft.tools
            and not _can(draft.skill, self.context, SkillAction.PUBLISH)
        ):
            # 本文を編集できても、Toolは増やせない（design.md §3.6）
            raise PermissionDenied(_("使ってよいToolを変えるには、この範囲の公開の権限が要ります"))
        if (
            draft.is_sensitive
            and not attrs.get("is_sensitive", True)
            and not _can(draft.skill, self.context, SkillAction.PUBLISH)
        ):
            # 編集の権限で機密の制限を緩めない（authorization.md §5.1）
            raise PermissionDenied(_("機密のフラグを外すには、この範囲の公開の権限が要ります"))
        return attrs

    def create(self, validated_data):
        raise NotImplementedError("Draftは SkillDraftOpenSerializer で開く")

    def update(self, instance: SkillDraft, validated_data) -> SkillDraft:
        actor = validated_data.pop("actor")
        expected = validated_data.pop("revision")
        try:
            instance.revise(actor=actor, expected_revision=expected, **validated_data)
        except VersionConflict as exc:
            raise Conflict() from exc
        return instance


class SkillDraftOpenSerializer(serializers.Serializer):
    base_version = serializers.IntegerField(
        min_value=1, required=False, help_text=_("元にする版。省けば公開中の版（未公開ならDraftは空で開く）")
    )

    def validate(self, attrs):
        skill: Skill = self.context["skill"]
        errors = defaultdict(list)
        if "base_version" in attrs:
            base = skill.versions.filter(number=attrs["base_version"]).first()
            if base is None:
                errors["base_version"].append(_("指定した版がありません"))
            attrs["base_version"] = base
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def create(self, validated_data) -> SkillDraft:
        skill: Skill = self.context["skill"]
        try:
            return SkillDraft.open(skill, actor=validated_data["actor"], base=validated_data.get("base_version"))
        except DraftExists as exc:
            raise Conflict(_("このSkillには編集中のDraftがあります")) from exc

    def update(self, instance, validated_data):
        raise NotImplementedError("Draftの更新は SkillDraftSerializer で行う")

    def to_representation(self, instance: SkillDraft):
        return SkillDraftSerializer(instance, context=self.context).data


# --- 評価（design.md §3.7.1） ---

# 1つの評価ケースに添付できるファイルの数
MAX_CASE_FILES = 5


class SkillEvaluationCaseSerializer(serializers.ModelSerializer):
    """評価ケース。添付はファイルを送る（multipart/form-data）。`expected_result` はJSONの文字列でもよい。

    必須の印の付け外しと、必須のケースの変更・削除は公開の権限が要る（編集の権限で公開の条件を緩めないため）
    """

    id = serializers.CharField(source="uid", read_only=True)
    name = NormalizedTextField(max_length=100)
    prompt = serializers.CharField(max_length=MAX_INSTRUCTIONS, help_text=_("依頼の文。評価のRunの最初の発言になる"))
    criteria = serializers.CharField(
        max_length=10_000, required=False, allow_blank=True, help_text=_("LLMの判定に使う、確認する観点")
    )
    expected_result = serializers.JSONField(
        required=False,
        allow_null=True,
        help_text=_("期待する業務結果。書いた項目だけを submit_result の値と照合する"),
    )
    files_info = ResourceVersionSerializer(source="files", many=True, read_only=True)
    add_files = serializers.ListField(
        child=serializers.FileField(), write_only=True, required=False, help_text=_("加える添付")
    )
    remove_files = serializers.ListField(
        child=serializers.CharField(max_length=30), write_only=True, required=False, help_text=_("外す添付のID")
    )
    updated_by_info = MinimumUserSerializer(source="updated_by", read_only=True)

    class Meta:
        model = SkillEvaluationCase
        fields = [
            "id",
            "name",
            "prompt",
            "criteria",
            "expected_result",
            "required",
            "files_info",
            "add_files",
            "remove_files",
            "updated_by_info",
            "created",
            "updated",
        ]
        read_only_fields = ["created", "updated"]

    def validate_expected_result(self, value: Any) -> Any:
        if value is not None and not isinstance(value, dict):
            raise serializers.ValidationError(_("JSONのオブジェクトで入力してください"))
        return value

    def validate_add_files(self, value: list[Any]) -> list[Any]:
        limit = settings.YORIKI_UPLOAD_MAX_MB
        if any(f.size > limit * 1024 * 1024 for f in value):
            raise serializers.ValidationError(_("ファイルが大きすぎます（%(limit)sMBまで）") % {"limit": limit})
        return value

    def validate(self, attrs):
        skill: Skill = self.context["skill"]
        case: SkillEvaluationCase | None = self.instance
        publisher = _can(skill, self.context, SkillAction.PUBLISH)
        required = attrs.get("required", case.required if case else False)
        if required and skill.scope == SkillScope.PERSONAL:
            raise serializers.ValidationError({"required": _("個人のSkillには、公開の前に必須の印を付けません")})
        if not publisher and (required or (case is not None and case.required)):
            raise PermissionDenied(_("公開の前に必須の評価ケースを作る・変えるには、この範囲の公開の権限が要ります"))
        name = attrs.get("name")
        if name is not None:
            same = SkillEvaluationCase.objects.filter(skill=skill, name=name)
            if case is not None:
                same = same.exclude(pk=case.pk)
            if same.exists():
                raise serializers.ValidationError({"name": _("同じ名前の評価ケースがあります")})
        if case is None and SkillEvaluationCase.objects.filter(skill=skill).count() >= MAX_EVALUATION_CASES:
            raise serializers.ValidationError(
                {"non_field_errors": [_("評価ケースは%(limit)s件までです") % {"limit": MAX_EVALUATION_CASES}]}
            )
        kept = list(case.files.all()) if case is not None else []
        removed = set(attrs.get("remove_files", []))
        if len([f for f in kept if f.uid not in removed]) + len(attrs.get("add_files", [])) > MAX_CASE_FILES:
            raise serializers.ValidationError({"add_files": _("添付は%(limit)s個までです") % {"limit": MAX_CASE_FILES}})
        return attrs

    @transaction.atomic
    def create(self, validated_data) -> SkillEvaluationCase:
        actor = validated_data.pop("actor")
        add_files = validated_data.pop("add_files", [])
        validated_data.pop("remove_files", None)
        # 同時の作成で上限を超えないよう、Skillの行をロックして数え直す
        skill = Skill.objects.select_for_update().get(pk=self.context["skill"].pk)
        if SkillEvaluationCase.objects.filter(skill=skill).count() >= MAX_EVALUATION_CASES:
            raise serializers.ValidationError(
                {"non_field_errors": [_("評価ケースは%(limit)s件までです") % {"limit": MAX_EVALUATION_CASES}]}
            )
        case = SkillEvaluationCase.objects.create(skill=skill, created_by=actor, updated_by=actor, **validated_data)
        case.files.add(*_upload(add_files, actor))
        return case

    @transaction.atomic
    def update(self, instance: SkillEvaluationCase, validated_data) -> SkillEvaluationCase:
        actor = validated_data.pop("actor")
        # 評価の開始（ケースの中身と添付を写す）とも重ならない
        # 公開の判定（Skillの行をロックする）と重ならないよう、Skill → ケースの順にロックしてから変える
        Skill.objects.select_for_update().filter(pk=instance.skill_id).first()
        instance = SkillEvaluationCase.objects.select_for_update().get(pk=instance.pk)
        if instance.required and not _can(instance.skill, self.context, SkillAction.PUBLISH):
            # 確かめた後に、公開の権限を持つ人が必須にした
            raise PermissionDenied(_("公開の前に必須の評価ケースを作る・変えるには、この範囲の公開の権限が要ります"))
        add_files = validated_data.pop("add_files", [])
        removed = set(validated_data.pop("remove_files", []))
        changed = [name for name, value in validated_data.items() if getattr(instance, name) != value]
        removing = [f for f in instance.files.all() if f.uid in removed]
        if not (changed or add_files or removing):
            return instance  # 中身を変えなければ更新日時も変えない（評価の結果との照合に使う）
        for name in changed:
            setattr(instance, name, validated_data[name])
        instance.updated_by = actor
        instance.save()
        instance.files.remove(*removing)
        instance.files.add(*_upload(add_files, actor))
        return instance


def _upload(files: list[Any], actor) -> list[ResourceVersion]:
    return [Resource.create_from_upload(f, f.name or "file", actor=actor) for f in files]


class EvaluationCheckSerializer(serializers.Serializer):
    """機械的な検証（conversation/evaluation.check_run()）"""

    completed = serializers.BooleanField(read_only=True)
    status_display = serializers.CharField(read_only=True, required=False)
    error = serializers.CharField(read_only=True, required=False)
    result_required = serializers.BooleanField(read_only=True, required=False)
    result_submitted = serializers.BooleanField(read_only=True, required=False)
    expected_checked = serializers.BooleanField(read_only=True, required=False)
    mismatches = serializers.ListField(child=serializers.JSONField(), read_only=True, required=False)
    passed = serializers.BooleanField(read_only=True)
    reply = serializers.CharField(read_only=True, required=False, help_text=_("最後の応答"))
    result = serializers.JSONField(read_only=True, required=False, allow_null=True, help_text=_("業務結果"))
    cost = serializers.CharField(read_only=True, required=False, help_text=_("利用額"))


class EvaluationCaseDataSerializer(serializers.Serializer):
    prompt = serializers.CharField(read_only=True)
    criteria = serializers.CharField(read_only=True)
    expected_result = serializers.JSONField(read_only=True, allow_null=True)
    files = serializers.ListField(child=serializers.DictField(), read_only=True)


class SkillEvaluationResultSerializer(serializers.ModelSerializer):
    case: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(
        slug_field="uid", read_only=True, allow_null=True
    )
    case_data = EvaluationCaseDataSerializer(read_only=True, help_text=_("評価を始めた時点のケースの中身"))
    state_display = serializers.CharField(source="get_state_display", read_only=True)
    base_status = serializers.SerializerMethodField(help_text=_("公開中の版のRunの状態（公開前のSkillでは空）"))
    draft_status = serializers.SerializerMethodField(help_text=_("DraftのRunの状態"))
    base_check = EvaluationCheckSerializer(read_only=True, allow_null=True)
    draft_check = EvaluationCheckSerializer(read_only=True, allow_null=True)
    verdict_display = serializers.CharField(source="get_verdict_display", read_only=True, allow_null=True)

    class Meta:
        model = SkillEvaluationResult
        fields = [
            "order",
            "case",
            "case_name",
            "required",
            "case_data",
            "state",
            "state_display",
            "base_status",
            "draft_status",
            "base_check",
            "draft_check",
            "passed",
            "verdict",
            "verdict_display",
            "judge_reason",
            "judge_note",
        ]
        read_only_fields = fields

    def get_base_status(self, obj: SkillEvaluationResult) -> str | None:
        return obj.base_run.get_status_display() if obj.base_run else None

    def get_draft_status(self, obj: SkillEvaluationResult) -> str | None:
        return obj.draft_run.get_status_display() if obj.draft_run else None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        for side in ("base_check", "draft_check"):
            data[side] = data[side] if getattr(instance, side) else None
        return data


class SkillEvaluationSerializer(serializers.ModelSerializer):
    """評価の1回。`current` は今のDraftの今のrevisionを評価したものか"""

    id = serializers.CharField(source="uid", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    base_version = serializers.IntegerField(source="base_version.number", read_only=True, allow_null=True)
    model_profile_name = serializers.CharField(source="model_version.profile.name", read_only=True)
    requested_by_info = MinimumUserSerializer(source="requested_by", read_only=True)
    from_conversation = serializers.SerializerMethodField(help_text=_("会話のSkillの編集から始めた"))
    current = serializers.SerializerMethodField()
    passed = serializers.SerializerMethodField(help_text=_("通過したケースの数"))
    total = serializers.SerializerMethodField(help_text=_("ケースの数"))
    results = SkillEvaluationResultSerializer(many=True, read_only=True)

    class Meta:
        model = SkillEvaluation
        fields = [
            "id",
            "status",
            "status_display",
            "draft_uid",
            "draft_revision",
            "base_version",
            "model_profile_name",
            "effort",
            "requested_by_info",
            "from_conversation",
            "current",
            "passed",
            "total",
            "results",
            "created",
            "finished",
        ]
        read_only_fields = fields

    def get_from_conversation(self, obj: SkillEvaluation) -> bool:
        return obj.run_id is not None

    def get_current(self, obj: SkillEvaluation) -> bool:
        draft = self.context.get("current_draft")
        return draft is not None and draft.uid == obj.draft_uid and draft.revision == obj.draft_revision

    def get_passed(self, obj: SkillEvaluation) -> int:
        return sum(1 for r in obj.results.all() if r.passed)

    def get_total(self, obj: SkillEvaluation) -> int:
        return len(obj.results.all())


class MinimumSkillEvaluationSerializer(SkillEvaluationSerializer):
    class Meta(SkillEvaluationSerializer.Meta):
        fields = [f for f in SkillEvaluationSerializer.Meta.fields if f != "results"]
        read_only_fields = fields


class SkillEvaluationCreateSerializer(serializers.Serializer):
    cases = serializers.ListField(
        child=serializers.CharField(max_length=30),
        min_length=1,
        max_length=MAX_CASES_PER_EVALUATION,
        help_text=_("評価するケースのID"),
    )
    model_profile: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(
        slug_field="uid",
        queryset=ModelProfile.objects.filter(
            deleted=False, is_active=True, current_version__connection__is_active=True
        ).select_related("current_version__connection"),
    )
    effort = serializers.CharField(max_length=50, required=False, allow_blank=True)

    def validate(self, attrs):
        skill: Skill = self.context["skill"]
        found = {c.uid: c for c in SkillEvaluationCase.objects.filter(skill=skill, uid__in=attrs["cases"])}
        if missing := [uid for uid in attrs["cases"] if uid not in found]:
            raise serializers.ValidationError(
                {"cases": _("評価ケースが見つかりません：%(ids)s") % {"ids": ", ".join(missing)}}
            )
        attrs["cases"] = [found[uid] for uid in dict.fromkeys(attrs["cases"])]
        if attrs["model_profile"].current_version is None:
            raise serializers.ValidationError({"model_profile": _("このモデル設定は使えません")})
        effort = attrs.get("effort", "")
        if effort and effort not in attrs["model_profile"].current_version.effort_values:
            raise serializers.ValidationError({"effort": _("このモデルが受け付けるeffortの値から選んでください")})
        return attrs

    def create(self, validated_data) -> SkillEvaluation:
        from conversation.evaluation import start_evaluation  # noqa: PLC0415  conversationがskillをimportするため

        try:
            return start_evaluation(
                self.context["skill"],
                cases=validated_data["cases"],
                actor=validated_data["actor"],
                model_version=validated_data["model_profile"].current_version,
                effort=validated_data.get("effort", ""),
            )
        except CannotEvaluate as exc:
            raise serializers.ValidationError({"non_field_errors": [str(exc)]}) from exc

    def update(self, instance, validated_data):
        raise NotImplementedError("評価は更新しない")

    def to_representation(self, instance: SkillEvaluation):
        return SkillEvaluationSerializer(instance, context=self.context).data
