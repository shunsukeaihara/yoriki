import re
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

import jsonschema
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from harness.tools import ALL_TOOLS
from harness.tools.base import schema_problem
from resources import formats
from resources.models import ResourceVersion
from user.models import Department
from user.serializers import MinimumUserSerializer
from utils.exceptions import Conflict
from utils.fields import NormalizedTextField
from utils.serializers import ReadOnlyModelSerializer

from .errors import DraftExists, InvalidDraft, VersionConflict
from .models import (
    Skill,
    SkillAccess,
    SkillAssetBinding,
    SkillChange,
    SkillDraft,
    SkillDraftAsset,
    SkillRequirement,
    SkillScope,
    SkillVersion,
)

# 資料のkey。Sandboxの中のパス（/workspace/master/skills/<Skill>/<key>/）にも使う
ASSET_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
TOOL_CHOICES = [(t.name, t.name) for t in ALL_TOOLS]
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


def _access(skill: Skill, context: Mapping[str, Any]) -> SkillAccess:
    request = context.get("request")
    return skill.access_of(request.user) if request else SkillAccess.NONE


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
    """資料の検証の条件（design.md §3.9）。CSVだけが使う。公開の役割だけが変えられる"""

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

    def validate_tools(self, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))  # 重ねずに、指定の順を保つ

    def validate_requires(self, value: list[int]) -> list[int]:
        return sorted(set(value))

    def validate_input_schema(self, value: Any) -> Any:
        return _schema_or_none(value)

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
            "assets",
            "rolled_back_from",
            "change_reason",
            "created_by_info",
            "created",
        ]


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


class MinimumDepartmentSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)

    class Meta:
        model = Department
        fields = ["id", "name"]
        read_only_fields = fields


class SkillSerializer(serializers.ModelSerializer):
    """Skillと公開中の版の概要。中身は版（versions）とDraft（drafts）で扱う"""

    id = serializers.CharField(source="uid", read_only=True)
    scope_display = serializers.CharField(source="get_scope_display", read_only=True)
    owner_info = MinimumUserSerializer(source="owner", read_only=True, allow_null=True)
    department_info = MinimumDepartmentSerializer(source="department", read_only=True, allow_null=True)
    version = serializers.IntegerField(source="version_number", read_only=True, help_text=_("公開中の版。未公開は0"))
    description = serializers.CharField(source="current_version.description", read_only=True, allow_null=True)
    draft = serializers.SerializerMethodField()
    access = serializers.SerializerMethodField()

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
            "draft",
            "access",
            "created",
            "updated",
        ]
        read_only_fields = ["scope", "suspended", "created", "updated"]

    def get_draft(self, obj: Skill) -> str | None:
        """開いているDraftのID。なければnull。Draftを編集できる人にだけ出す"""
        if _access(obj, self.context) < SkillAccess.EDIT:
            return None
        drafts = getattr(obj, "open_drafts", None)
        if drafts is None:
            drafts = list(obj.drafts.all())
        return drafts[0].uid if drafts else None

    def get_access(self, obj: Skill) -> int:
        """利用者ができること（1：使う、2：Draftを変更する、3：公開・切戻し・停止・削除・Toolの変更）"""
        return int(_access(obj, self.context))

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
        # 作る人がその範囲で持つ権限。未保存のSkillで判定する（design.md §3.6）
        access = Skill(scope=scope, owner=user if scope == SkillScope.PERSONAL else None, department=department)
        level = access.scope_access_of(user)
        if level < SkillAccess.EDIT:
            raise PermissionDenied(_("この範囲にSkillを作る権限がありません"))
        if attrs.get("tools") and level < SkillAccess.PUBLISH:
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
    """資料の検証の条件を変える（公開の役割だけ）。変えると検証し直す"""

    revision = serializers.IntegerField(min_value=1, help_text=_("編集を始めた時点のDraftのrevision"))
    checks = AssetChecksSerializer()

    def validate(self, attrs):
        asset: SkillDraftAsset = self.instance  # type: ignore[assignment]
        if (
            attrs["checks"] != formats.normalize_checks(asset.checks)
            and _access(asset.draft.skill, self.context) < SkillAccess.PUBLISH
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
            "assets",
            "updated_by_info",
            "created",
            "updated",
        ]
        read_only_fields = ["created", "updated"]

    def validate(self, attrs):
        draft = self.instance
        assert draft is not None
        if "revision" not in attrs:
            # 部分更新（PATCH）では必須の項目も省けるので、ここで確かめる
            raise serializers.ValidationError({"revision": _("編集を始めた時点のrevisionを送ってください")})
        if (
            "tools" in attrs
            and attrs["tools"] != draft.tools
            and _access(draft.skill, self.context) < SkillAccess.PUBLISH
        ):
            # 本文を編集できても、Toolは増やせない（design.md §3.6）
            raise PermissionDenied(_("使ってよいToolを変えるには、この範囲の公開の権限が要ります"))
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
