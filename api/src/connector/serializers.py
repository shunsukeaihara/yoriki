from collections import defaultdict
from typing import Any

from django.contrib.auth.models import Group
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from user.models import Department
from user.serializers import MinimumDepartmentSerializer
from utils.crypto import is_encryption_configured
from utils.exceptions import Conflict

from .adapters.base import Param, QueryResult, TableInfo, to_text
from .errors import VersionConflict
from .models import (
    DATASOURCE_OPERATIONS,
    VERSION_FIELDS,
    Connector,
    ConnectorKind,
    ConnectorPermission,
    ConnectorVersion,
    Operation,
)

MAX_TABLES = 500
MAX_PARAMS = 100


class ConnectorTableEntrySerializer(serializers.Serializer):
    """公開する表・ビュー（版の `tables` の1件）"""

    schema = serializers.CharField(
        max_length=128, required=False, allow_blank=True, default="", help_text=_("MySQLでは空（DB名がスキーマ）")
    )
    name = serializers.CharField(max_length=128)
    description = serializers.CharField(max_length=2000, required=False, allow_blank=True, default="")
    columns = serializers.DictField(
        child=serializers.CharField(max_length=1000, allow_blank=True),
        required=False,
        default=dict,
        help_text=_("列の説明（列名 → 説明）。任意"),
    )

    def create(self, validated_data):
        raise NotImplementedError("版の一部として保存する")

    def update(self, instance, validated_data):
        raise NotImplementedError("版の一部として保存する")


class ConnectorPermissionSerializer(serializers.ModelSerializer):
    """Connectorを使える役割か部門（どちらか一方）と、使える操作"""

    id = serializers.CharField(source="uid", read_only=True)
    group: "serializers.SlugRelatedField[Group]" = serializers.SlugRelatedField(
        slug_field="name", queryset=Group.objects.all(), required=False, allow_null=True, help_text=_("役割の名前")
    )
    department: "serializers.SlugRelatedField[Department]" = serializers.SlugRelatedField(
        slug_field="uid", queryset=Department.objects.filter(deleted=False), required=False, allow_null=True
    )
    department_info = MinimumDepartmentSerializer(source="department", read_only=True, allow_null=True)
    operations = serializers.ListField(
        child=serializers.ChoiceField(choices=Operation.choices),
        required=False,
        default=lambda: list(DATASOURCE_OPERATIONS),
        help_text=_("使える操作。省略するとすべて（社内DBの操作はどれも読取り）"),
    )

    class Meta:
        model = ConnectorPermission
        fields = ["id", "group", "department", "department_info", "operations"]

    def validate(self, attrs):
        if bool(attrs.get("group")) == bool(attrs.get("department")):
            raise serializers.ValidationError({"non_field_errors": _("役割か部門のどちらか一方を選んでください")})
        # 部分更新（PATCH）では入れ子の既定値が入らないので、ここで補う
        operations = list(dict.fromkeys(attrs.get("operations", DATASOURCE_OPERATIONS)))
        if not operations:
            raise serializers.ValidationError({"operations": _("使える操作を1つ以上選んでください")})
        attrs["operations"] = operations
        return attrs


class ConnectorSerializer(serializers.ModelSerializer):
    """Connectorの作成と表示。境界（公開する表、上限、機密のフラグ）は現在の版の値を平らに出す。

    境界を変えて保存すると、サーバーが新しい版を作る。更新では、編集を始めた時点の `version` を送り、
    その間に他の人が版を作っていたら409になる。パスワードは書き込み専用で、応答には末尾の4文字だけを出す
    """

    id = serializers.CharField(source="uid", read_only=True)
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    port = serializers.IntegerField(
        min_value=1, max_value=65535, required=False, help_text=_("省略すると種別の既定のポート")
    )
    password = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        max_length=1000,
        help_text=_("更新では、送らなければ変更しない"),
        style={"input_type": "password"},
    )
    has_password = serializers.SerializerMethodField()
    password_hint = serializers.SerializerMethodField()
    version = serializers.IntegerField(
        source="version_number",
        min_value=0,
        required=False,
        help_text=_("現在の版。更新では、編集を始めた時点の値を送る（作成では送らない）"),
    )
    # 境界は現在の版の項目。入力は validated_data["current_version"] にまとまる
    description = serializers.CharField(
        source="current_version.description",
        max_length=5000,
        required=False,
        allow_blank=True,
        default="",
        help_text=_("エージェントに見せる、このConnectorの説明"),
    )
    default_schema = serializers.CharField(
        source="current_version.default_schema",
        max_length=128,
        required=False,
        allow_blank=True,
        help_text=_("表の名前にスキーマを書かなかったときのスキーマ。省略すると種別の既定（public、dbo）"),
    )
    tables = ConnectorTableEntrySerializer(source="current_version.tables", many=True, required=False, default=list)
    max_rows = serializers.IntegerField(
        source="current_version.max_rows", min_value=1, max_value=1_000_000, required=False, default=1000
    )
    max_result_bytes = serializers.IntegerField(
        source="current_version.max_result_bytes",
        min_value=1024,
        max_value=1024 * 1024 * 1024,
        required=False,
        default=10 * 1024 * 1024,
    )
    timeout_seconds = serializers.IntegerField(
        source="current_version.timeout_seconds", min_value=1, max_value=3600, required=False, default=30
    )
    is_sensitive = serializers.BooleanField(
        source="current_version.is_sensitive",
        required=False,
        default=False,
        help_text=_("個人情報を主な中身とする接続先。ローカルLLMのモデルを使うタスク・会話にだけ割り当てる"),
    )
    change_reason = serializers.CharField(max_length=500, required=False, allow_blank=True, write_only=True)
    permissions = ConnectorPermissionSerializer(
        many=True, required=False, help_text=_("使える役割・部門。更新では、送ると全体を置き換える")
    )
    created_by: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(slug_field="uid", read_only=True)

    class Meta:
        model = Connector
        fields = [
            "id",
            "name",
            "kind",
            "kind_display",
            "host",
            "port",
            "database",
            "username",
            "password",
            "has_password",
            "password_hint",
            "tls",
            "connect_timeout_seconds",
            "max_concurrency",
            "is_active",
            "version",
            *VERSION_FIELDS,
            "change_reason",
            "permissions",
            "created_by",
            "created",
            "updated",
        ]
        read_only_fields = ["created", "updated"]
        extra_kwargs = {
            "connect_timeout_seconds": {"min_value": 1, "max_value": 300},
            "max_concurrency": {"min_value": 1, "max_value": 1000},
        }

    def get_has_password(self, obj: Connector) -> bool:
        return bool(obj.password)

    def get_password_hint(self, obj: Connector) -> str:
        return obj.password.hint

    def validate_name(self, value: str) -> str:
        value = value.strip()
        others = Connector.objects.filter(deleted=False, name=value)
        if self.instance:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise serializers.ValidationError(_("同じ名前のConnectorがあります"))
        return value

    def validate_host(self, value: str) -> str:
        value = value.strip()
        if not value or any(ch.isspace() or ch in ",;{}/" for ch in value):
            raise serializers.ValidationError(_("ホスト名かIPアドレスを入力してください"))
        return value

    def validate_password(self, value: str) -> str:
        if value and not is_encryption_configured():
            raise serializers.ValidationError(
                _("パスワードを保存するための暗号化の鍵（YORIKI_SECRET_ENCRYPTION_KEYS）が設定されていません")
            )
        return value

    def validate_tables(self, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(value) > MAX_TABLES:
            raise serializers.ValidationError(_("公開する表・ビューは%(max)d件までです") % {"max": MAX_TABLES})
        for entry in value:
            entry["schema"] = entry.get("schema", "").strip()
            entry["name"] = entry["name"].strip()
        return value

    def validate_permissions(self, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grantees = [p.get("group") or p.get("department") for p in value]
        if len(set(grantees)) != len(grantees):
            raise serializers.ValidationError(_("同じ役割・部門が2回あります"))
        return value

    def validate(self, attrs):
        if self.instance is not None and "version_number" not in attrs:
            raise serializers.ValidationError({"version": _("編集を始めた時点の版を送ってください")})
        kind = ConnectorKind(attrs.get("kind", getattr(self.instance, "kind", ConnectorKind.POSTGRESQL)))
        errors: defaultdict[str, list[Any]] = defaultdict(list)
        if self.instance is None:
            attrs.setdefault("port", kind.default_port)
            if not attrs.get("password"):
                errors["password"].append(_("パスワードを入力してください"))
        content = attrs.get("current_version", {})
        if self.instance is None or "default_schema" in content:
            schema = content.get("default_schema", "").strip()
            if kind == ConnectorKind.MYSQL and schema:
                errors["default_schema"].append(_("MySQLでは、既定のスキーマは指定できません（DB名がスキーマ）"))
            content["default_schema"] = schema or kind.default_schema
            attrs["current_version"] = content
        current = self.instance.current_version if self.instance else None
        default_schema = content.get("default_schema") or (current.default_schema if current else kind.default_schema)
        seen = set()
        for entry in content.get("tables", []):
            # MySQLのスキーマはDB名なので持たない。他はスキーマを省いた表を既定のスキーマにそろえる
            entry["schema"] = "" if kind == ConnectorKind.MYSQL else entry["schema"] or default_schema
            key = (entry["schema"], entry["name"])
            if key in seen:
                errors["tables"].append(
                    _("同じ表・ビューが2回あります：%(name)s") % {"name": ".".join(filter(None, key))}
                )
            seen.add(key)
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def _content(self, validated_data, current: ConnectorVersion | None) -> dict[str, Any]:
        """送られた項目を優先し、残りは現在の版の値で補う"""
        sent = validated_data.pop("current_version", {})
        return {name: sent[name] if name in sent else getattr(current, name) for name in VERSION_FIELDS}

    def _save_permissions(self, connector: Connector, permissions: list[dict[str, Any]]) -> None:
        # 監査ログに残すため、1件ずつ削除・作成する（bulk_create は保存のシグナルを送らない）
        for permission in connector.permissions.all():
            permission.delete()
        for p in permissions:
            ConnectorPermission.objects.create(connector=connector, **p)

    @transaction.atomic
    def create(self, validated_data) -> Connector:
        validated_data.pop("version_number", None)
        permissions = validated_data.pop("permissions", [])
        actor = validated_data.pop("created_by")
        change_reason = validated_data.pop("change_reason", "")
        content = self._content(validated_data, None)
        connector = Connector.create_with_version(
            actor=actor, change_reason=change_reason, content=content, **validated_data
        )
        self._save_permissions(connector, permissions)
        return connector

    @transaction.atomic
    def update(self, instance: Connector, validated_data) -> Connector:
        try:
            instance.revise(
                actor=validated_data.pop("actor"),
                expected_version=validated_data.pop("version_number"),
                change_reason=validated_data.pop("change_reason", ""),
                **self._content(validated_data, instance.current_version),
            )
        except VersionConflict as exc:
            raise Conflict() from exc
        permissions = validated_data.pop("permissions", None)
        if permissions is not None:
            self._save_permissions(instance, permissions)
        if not validated_data.get("password", True):
            validated_data.pop("password")  # 空文字は「変更しない」
        for name, value in validated_data.items():
            setattr(instance, name, value)
        instance.save()
        return instance


class ConnectorUpdateSerializer(ConnectorSerializer):
    """Connectorの更新。種別と接続先（ホスト・ポート・DB名）は変えられないので、読み取り専用にする"""

    port = serializers.IntegerField(read_only=True)

    class Meta(ConnectorSerializer.Meta):
        read_only_fields = ["kind", "host", "database", "created", "updated"]


class MinimumConnectorSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = Connector
        fields = ["id", "name", "kind", "kind_display", "is_active"]
        read_only_fields = fields


class ConnectorVersionSerializer(serializers.ModelSerializer):
    """版の表示（履歴）"""

    id = serializers.CharField(source="uid", read_only=True)
    tables = ConnectorTableEntrySerializer(many=True, read_only=True)
    created_by: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(slug_field="uid", read_only=True)

    class Meta:
        model = ConnectorVersion
        fields = ["id", "number", *VERSION_FIELDS, "change_reason", "created_by", "created"]
        read_only_fields = fields


# --- 操作 ---


class ConnectorCheckSerializer(serializers.Serializer):
    """接続の確認の結果"""

    ok = serializers.BooleanField(read_only=True)
    detail = serializers.CharField(read_only=True, help_text=_("失敗の理由。成功したら空文字"))


class ConnectorColumnSerializer(serializers.Serializer):
    name = serializers.CharField(read_only=True)
    type = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True)


class ConnectorTableSerializer(serializers.Serializer):
    """DBの表・ビューと列"""

    schema = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    kind = serializers.ChoiceField(choices=["table", "view"], read_only=True)
    description = serializers.CharField(read_only=True)
    columns = ConnectorColumnSerializer(many=True, read_only=True)


class ConnectorTablesSerializer(serializers.Serializer):
    """表・ビューの一覧の取得の結果"""

    ok = serializers.BooleanField(read_only=True)
    detail = serializers.CharField(read_only=True, help_text=_("失敗の理由。成功したら空文字"))
    tables = ConnectorTableSerializer(many=True, read_only=True)

    @staticmethod
    def build(tables: list[TableInfo] | None, detail: str = "") -> dict[str, Any]:
        return {"ok": tables is not None, "detail": detail, "tables": tables or []}


class ConnectorQuerySerializer(serializers.Serializer):
    """SELECT文の試し実行（管理画面）。エージェントと同じ検査・上限を通る"""

    sql = serializers.CharField(max_length=100_000, help_text=_("SELECT文。引数の場所は ? で書く"))
    params = serializers.ListField(child=serializers.JSONField(), required=False, default=list, max_length=MAX_PARAMS)

    def validate_params(self, value: list[Any]) -> list[Param]:
        for item in value:
            if not isinstance(item, str | int | float | bool) and item is not None:
                raise serializers.ValidationError(_("引数は、文字・数値・真偽値・nullにしてください"))
        return value

    def create(self, validated_data):
        raise NotImplementedError("Viewで実行する")

    def update(self, instance, validated_data):
        raise NotImplementedError("Viewで実行する")


class ConnectorQueryResultSerializer(serializers.Serializer):
    ok = serializers.BooleanField(read_only=True)
    error_kind = serializers.CharField(read_only=True, help_text=_("失敗の種類。成功したら空文字"))
    detail = serializers.CharField(read_only=True, help_text=_("失敗の理由。成功したら空文字"))
    sql = serializers.CharField(read_only=True, help_text=_("実際に実行したSQL（検査した構文木から作り直したもの）"))
    columns = serializers.ListField(child=serializers.CharField(), read_only=True)
    rows = serializers.SerializerMethodField(help_text=_("値は文字列にしたもの（NULLは空文字）"))
    row_count = serializers.IntegerField(read_only=True)
    truncated = serializers.BooleanField(read_only=True, help_text=_("行数・サイズの上限で打ち切った"))
    preview_truncated = serializers.BooleanField(
        read_only=True, help_text=_("取得した行のうち、応答には先頭の行だけを載せた（row_count が取得した行数）")
    )
    csv_bytes = serializers.IntegerField(read_only=True)
    elapsed_ms = serializers.IntegerField(read_only=True)
    tables = serializers.ListField(child=serializers.CharField(), read_only=True)

    @extend_schema_field(serializers.ListField(child=serializers.ListField(child=serializers.CharField())))
    def get_rows(self, obj: dict[str, Any]) -> list[list[str]]:
        return [[to_text(v) for v in row] for row in obj["rows"]]

    @staticmethod
    def build(
        result: QueryResult | None, *, error_kind: str = "", detail: str = "", preview_rows: int | None = None
    ) -> dict[str, Any]:
        if result is None:
            return {
                "ok": False,
                "error_kind": error_kind,
                "detail": detail,
                "sql": "",
                "columns": [],
                "rows": [],
                "row_count": 0,
                "truncated": False,
                "preview_truncated": False,
                "csv_bytes": 0,
                "elapsed_ms": 0,
                "tables": [],
            }
        return {
            "ok": True,
            "error_kind": "",
            "detail": "",
            "sql": result.sql,
            "columns": result.columns,
            "rows": result.rows[:preview_rows],
            "row_count": len(result.rows),
            "truncated": result.truncated,
            "preview_truncated": preview_rows is not None and len(result.rows) > preview_rows,
            "csv_bytes": result.csv_bytes,
            "elapsed_ms": result.elapsed_ms,
            "tables": list(result.tables),
        }
