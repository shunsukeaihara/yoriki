from collections import defaultdict
from typing import Any

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
    VERSION_FIELDS,
    Connector,
    ConnectorKind,
    ConnectorScope,
    ConnectorVersion,
    DatabaseConnection,
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


class DatabaseConnectionSerializer(serializers.ModelSerializer):
    """DB接続の作成と表示。パスワードは書き込み専用で、応答には末尾の4文字だけを出す"""

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
        help_text=_("更新では、送らなければ変更しない（ホスト・ポート・DB名を変えるときは必須）"),
        style={"input_type": "password"},
    )
    has_password = serializers.SerializerMethodField()
    password_hint = serializers.SerializerMethodField()
    connector_count = serializers.SerializerMethodField(help_text=_("この接続を使うConnectorの数"))
    created_by: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(slug_field="uid", read_only=True)

    class Meta:
        model = DatabaseConnection
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
            "connector_count",
            "created_by",
            "created",
            "updated",
        ]
        read_only_fields = ["created", "updated"]
        extra_kwargs = {
            "connect_timeout_seconds": {"min_value": 1, "max_value": 300},
            "max_concurrency": {"min_value": 1, "max_value": 1000},
        }

    def get_has_password(self, obj: DatabaseConnection) -> bool:
        return bool(obj.password)

    def get_password_hint(self, obj: DatabaseConnection) -> str:
        return obj.password.hint

    def get_connector_count(self, obj: DatabaseConnection) -> int:
        # 一覧・詳細は件数を注釈で付けて読む（DatabaseConnectionViewSet）。作成・更新の応答では数える
        count = getattr(obj, "connector_count", None)
        return count if count is not None else obj.connectors.filter(deleted=False).count()

    def validate_name(self, value: str) -> str:
        value = value.strip()
        others = DatabaseConnection.objects.filter(deleted=False, name=value)
        if self.instance:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise serializers.ValidationError(_("同じ名前の接続先があります"))
        return value

    def validate_host(self, value: str) -> str:
        value = value.strip()
        if not value or any(ch.isspace() or ch in ",;{}/" for ch in value):
            raise serializers.ValidationError(_("ホスト名かIPアドレスを入力してください"))
        return value

    def validate_database(self, value: str) -> str:
        return value.strip()

    def validate_password(self, value: str) -> str:
        if value and not is_encryption_configured():
            raise serializers.ValidationError(
                _("パスワードを保存するための暗号化の鍵（YORIKI_SECRET_ENCRYPTION_KEYS）が設定されていません")
            )
        return value

    def validate(self, attrs):
        if self.instance is None:
            attrs.setdefault("port", ConnectorKind(attrs["kind"]).default_port)
            if not attrs.get("password"):
                raise serializers.ValidationError({"password": _("パスワードを入力してください")})
            return attrs
        # 登録済みのパスワードを別のサーバーへ送らないよう、接続先を変えるときは入れ直してもらう
        moved = any(name in attrs and attrs[name] != getattr(self.instance, name) for name in ADDRESS_FIELDS)
        if moved and not attrs.get("password"):
            raise serializers.ValidationError(
                {"password": _("ホスト・ポート・DB名を変えるときは、パスワードを入力し直してください")}
            )
        if not attrs.get("password", True):
            attrs.pop("password")  # 空文字は「変更しない」
        return attrs

    @transaction.atomic
    def update(self, instance: DatabaseConnection, validated_data) -> DatabaseConnection:
        # validate() は取得した時点の値で比べている。同時に別の要求が接続先を変えていても、パスワードなしで
        # 接続先を変えさせないよう、行をロックして最新の値で比べ直す
        latest = DatabaseConnection.objects.select_for_update().get(pk=instance.pk)
        if latest.deleted:
            raise Conflict()
        moved = any(name in validated_data and validated_data[name] != getattr(latest, name) for name in ADDRESS_FIELDS)
        if moved and "password" not in validated_data:
            raise serializers.ValidationError(
                {"password": _("ホスト・ポート・DB名を変えるときは、パスワードを入力し直してください")}
            )
        # ロックした最新の行に当てて保存し、それを返す（取得した後に他の要求が変えた項目を、応答でも古くしない）
        for name, value in validated_data.items():
            setattr(latest, name, value)
        latest.save(update_fields=[*validated_data, "updated"])
        return latest


# 接続先。変えるときはパスワードを入れ直す
ADDRESS_FIELDS = ("host", "port", "database")


class DatabaseConnectionUpdateSerializer(DatabaseConnectionSerializer):
    """DB接続の更新。種別は変えられない（SQLの方言と既定のスキーマの意味が変わるため）"""

    class Meta(DatabaseConnectionSerializer.Meta):
        read_only_fields = ["kind", "created", "updated"]


class MinimumDatabaseConnectionSerializer(serializers.ModelSerializer):
    """Connectorの応答に載せる接続先。ホスト・ポート・DB名は、接続先を見る（か変える）permissionを持つ人にだけ出す
    （Connectorは所属だけで見られるので、接続先の詳細をpermissionの判定を通さずに出さない）"""

    id = serializers.CharField(source="uid", read_only=True)
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    host = serializers.CharField(read_only=True, allow_null=True, help_text=_("接続先を見る権限がなければnull"))
    port = serializers.IntegerField(read_only=True, allow_null=True, help_text=_("接続先を見る権限がなければnull"))
    database = serializers.CharField(read_only=True, allow_null=True, help_text=_("接続先を見る権限がなければnull"))

    ADDRESS = ("host", "port", "database")

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not (user and any(user.has_perm(f"connector.{k}_databaseconnection") for k in ("view", "change"))):
            data.update(dict.fromkeys(self.ADDRESS))
        return data

    class Meta:
        model = DatabaseConnection
        fields = [
            "id",
            "name",
            "kind",
            "kind_display",
            "host",
            "port",
            "database",
            "connect_timeout_seconds",
            "is_active",
        ]
        read_only_fields = fields


class ConnectorSerializer(serializers.ModelSerializer):
    """Connectorの作成と表示。境界（公開する表、上限、機密のフラグ）は現在の版の値を平らに出す。

    境界を変えて保存すると、サーバーが新しい版を作る。更新では、編集を始めた時点の `version` を送り、
    その間に他の人が版を作っていたら409になる。使うDB接続は作成した後は変えられない。

    範囲（部門・全社・システム）と部門は作成時に決め、更新では変えない（authorization.md §5.2）。作成・編集は
    所属を問わず、permissionだけで判定する
    """

    id = serializers.CharField(source="uid", read_only=True)
    connection: "serializers.SlugRelatedField[DatabaseConnection]" = serializers.SlugRelatedField(
        slug_field="uid", queryset=DatabaseConnection.objects.filter(deleted=False), help_text=_("使う接続先")
    )
    connection_info = MinimumDatabaseConnectionSerializer(source="connection", read_only=True)
    kind = serializers.ChoiceField(source="connection.kind", choices=ConnectorKind.choices, read_only=True)
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
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
        help_text=_("個人情報を主な中身とする範囲。ローカルLLMのモデルを使うタスク・会話にだけ割り当てる"),
    )
    change_reason = serializers.CharField(max_length=500, required=False, allow_blank=True, write_only=True)
    scope = serializers.ChoiceField(choices=ConnectorScope.choices, help_text=_("範囲。作成した後は変えられない"))
    scope_display = serializers.CharField(source="get_scope_display", read_only=True)
    department: "serializers.SlugRelatedField[Department]" = serializers.SlugRelatedField(
        slug_field="uid",
        queryset=Department.objects.active(),
        required=False,
        allow_null=True,
        help_text=_("部門の範囲では必須。作成した後は変えられない"),
    )
    department_info = MinimumDepartmentSerializer(source="department", read_only=True, allow_null=True)
    created_by: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(slug_field="uid", read_only=True)

    class Meta:
        model = Connector
        fields = [
            "id",
            "name",
            "connection",
            "connection_info",
            "kind",
            "kind_display",
            "is_active",
            "version",
            *VERSION_FIELDS,
            "change_reason",
            "scope",
            "scope_display",
            "department",
            "department_info",
            "created_by",
            "created",
            "updated",
        ]
        read_only_fields = ["created", "updated"]

    def validate_name(self, value: str) -> str:
        value = value.strip()
        others = Connector.objects.filter(deleted=False, name=value)
        if self.instance:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise serializers.ValidationError(_("同じ名前のConnectorがあります"))
        return value

    def validate_tables(self, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(value) > MAX_TABLES:
            raise serializers.ValidationError(_("公開する表・ビューは%(max)d件までです") % {"max": MAX_TABLES})
        for entry in value:
            entry["schema"] = entry.get("schema", "").strip()
            entry["name"] = entry["name"].strip()
        return value

    def _validate_scope(self, attrs) -> None:
        if self.instance is not None:
            return
        if attrs["scope"] == ConnectorScope.DEPARTMENT:
            if attrs.get("department") is None:
                raise serializers.ValidationError({"department": _("部門を選んでください")})
        else:
            attrs["department"] = None

    def validate(self, attrs):
        if self.instance is not None and "version_number" not in attrs:
            raise serializers.ValidationError({"version": _("編集を始めた時点の版を送ってください")})
        self._validate_scope(attrs)
        connection = attrs["connection"] if self.instance is None else self.instance.connection
        kind = ConnectorKind(connection.kind)
        errors: defaultdict[str, list[Any]] = defaultdict(list)
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

    @transaction.atomic
    def create(self, validated_data) -> Connector:
        validated_data.pop("version_number", None)
        actor = validated_data.pop("created_by")
        change_reason = validated_data.pop("change_reason", "")
        # 同時にDB接続が削除されていないことを、行をロックして確かめる（削除の側も同じ行をロックする）
        connection = DatabaseConnection.objects.select_for_update().get(pk=validated_data["connection"].pk)
        if connection.deleted:
            raise serializers.ValidationError({"connection": _("接続先が削除されています")})
        content = self._content(validated_data, None)
        connector = Connector.create_with_version(
            actor=actor, change_reason=change_reason, content=content, **validated_data
        )
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
        for name, value in validated_data.items():
            setattr(instance, name, value)
        # 変えた項目だけを保存する（取得した後に他の要求が変えた項目を書き戻さない。行は revise() でロックしている）
        instance.save(update_fields=[*validated_data, "current_version", "updated"])
        return instance


class ConnectorUpdateSerializer(ConnectorSerializer):
    """Connectorの更新。使うDB接続と範囲・部門は変えられないので、読み取り専用にする"""

    connection = serializers.SlugRelatedField(slug_field="uid", read_only=True)
    scope = serializers.ChoiceField(choices=ConnectorScope.choices, read_only=True)
    department = serializers.SlugRelatedField(slug_field="uid", read_only=True)

    class Meta(ConnectorSerializer.Meta):
        pass


class MinimumConnectorSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)
    kind = serializers.ChoiceField(source="connection.kind", choices=ConnectorKind.choices, read_only=True)
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
    def build(result: QueryResult | None, *, error_kind: str = "", detail: str = "") -> dict[str, Any]:
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
            "rows": result.rows,
            "row_count": result.row_count,
            "truncated": result.truncated,
            "preview_truncated": result.row_count > len(result.rows),
            "csv_bytes": result.csv_bytes,
            "elapsed_ms": result.elapsed_ms,
            "tables": list(result.tables),
        }
