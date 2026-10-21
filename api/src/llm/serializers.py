from collections import defaultdict
from decimal import Decimal
from typing import Any

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from utils.crypto import is_encryption_configured
from utils.exceptions import Conflict

from .errors import VersionConflict
from .models import VERSION_FIELDS, LLMConnection, ModelProfile, ModelProfileVersion, ProviderKind

# --- プロバイダ接続 ---


class LLMConnectionSerializer(serializers.ModelSerializer):
    """プロバイダ接続の作成と表示。API keyは書き込み専用で、応答には末尾の4文字（api_key_hint）だけを出す"""

    id = serializers.CharField(source="uid", read_only=True)
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    api_key = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        max_length=1000,
        help_text=_("更新では、送らなければ変更しない。空文字はAPI keyなし（ローカルLLMだけ）"),
    )
    has_api_key = serializers.SerializerMethodField()
    api_key_hint = serializers.SerializerMethodField()
    created_by: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(slug_field="uid", read_only=True)

    class Meta:
        model = LLMConnection
        fields = [
            "id",
            "name",
            "kind",
            "kind_display",
            "base_url",
            "api_key",
            "has_api_key",
            "api_key_hint",
            "timeout_seconds",
            "max_concurrency",
            "requests_per_minute",
            "is_active",
            "created_by",
            "created",
            "updated",
        ]
        read_only_fields = ["created", "updated"]
        extra_kwargs = {
            "timeout_seconds": {"min_value": 1, "max_value": 3600},
            "max_concurrency": {"min_value": 1, "max_value": 1000},
            "requests_per_minute": {"min_value": 1},
        }

    def get_has_api_key(self, obj: LLMConnection) -> bool:
        return bool(obj.api_key)

    def get_api_key_hint(self, obj: LLMConnection) -> str:
        return obj.api_key.hint

    def validate_name(self, value: str) -> str:
        value = value.strip()
        others = LLMConnection.objects.filter(deleted=False, name=value)
        if self.instance:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise serializers.ValidationError(_("同じ名前のプロバイダ接続があります"))
        return value

    def validate_api_key(self, value: str) -> str:
        value = value.strip()
        if value and not is_encryption_configured():
            raise serializers.ValidationError(
                _("API keyを保存するための暗号化の鍵（YORIKI_SECRET_ENCRYPTION_KEYS）が設定されていません")
            )
        return value

    def validate(self, attrs):
        # 種別とbase URLは作成時だけ入力できる。更新（LLMConnectionUpdateSerializer）では既存の値で検証する
        kind = ProviderKind(attrs.get("kind", getattr(self.instance, "kind", ProviderKind.OPENAI)))
        base_url = attrs.get("base_url", getattr(self.instance, "base_url", ""))
        api_key = attrs.get("api_key")
        has_api_key = bool(api_key) if api_key is not None else bool(self.instance and self.instance.api_key)

        errors = defaultdict(list)
        if kind.uses_base_url and not base_url:
            errors["base_url"].append(_("ローカルLLMでは、base URLを入力してください"))
        if not kind.uses_base_url and base_url:
            errors["base_url"].append(_("この種別では、base URLを指定できません"))
        if kind.requires_api_key and not has_api_key:
            errors["api_key"].append(_("この種別では、API keyを入力してください"))
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class LLMConnectionUpdateSerializer(LLMConnectionSerializer):
    """プロバイダ接続の更新。種別とbase URL（接続先）は変えられないので、読み取り専用にする"""

    class Meta(LLMConnectionSerializer.Meta):
        read_only_fields = ["kind", "base_url", "created", "updated"]


class MinimumLLMConnectionSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = LLMConnection
        fields = ["id", "name", "kind", "kind_display", "is_active"]
        read_only_fields = fields


class LLMConnectionCheckSerializer(serializers.Serializer):
    """接続の確認の結果"""

    ok = serializers.BooleanField(read_only=True)
    detail = serializers.CharField(read_only=True, help_text=_("失敗の理由。成功したら空文字"))
    models = serializers.ListField(child=serializers.CharField(), read_only=True, help_text=_("接続先で使えるモデル名"))


# --- モデル設定 ---


class ModelProfileVersionSerializer(serializers.ModelSerializer):
    """版の表示（履歴）"""

    id = serializers.CharField(source="uid", read_only=True)
    connection: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(slug_field="uid", read_only=True)
    connection_info = MinimumLLMConnectionSerializer(source="connection", read_only=True)
    created_by: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(slug_field="uid", read_only=True)

    class Meta:
        model = ModelProfileVersion
        fields = ["id", "number", *VERSION_FIELDS, "connection_info", "change_reason", "created_by", "created"]
        read_only_fields = fields


class ModelProfileSerializer(serializers.ModelSerializer):
    """モデル設定。中身（接続、モデル名、上限、単価、能力）は現在の版の値を平らに出す。

    中身を変えて保存すると、サーバーが新しい版を作る。更新では、編集を始めた時点の `version` を送り、
    その間に他の人が版を作っていたら409になる
    """

    id = serializers.CharField(source="uid", read_only=True)
    version = serializers.IntegerField(
        source="version_number",
        min_value=0,
        required=False,
        help_text=_("現在の版。更新では、編集を始めた時点の値を送る（作成では送らない）"),
    )
    # 中身は現在の版の項目。入力は validated_data["current_version"] にまとまる
    connection: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(
        source="current_version.connection",
        slug_field="uid",
        queryset=LLMConnection.objects.filter(deleted=False, is_active=True),
    )
    connection_info = MinimumLLMConnectionSerializer(source="current_version.connection", read_only=True)
    model_name = serializers.CharField(source="current_version.model_name", max_length=200)
    max_input_tokens = serializers.IntegerField(source="current_version.max_input_tokens", min_value=1)
    max_output_tokens = serializers.IntegerField(source="current_version.max_output_tokens", min_value=1)
    input_price = serializers.DecimalField(
        source="current_version.input_price", max_digits=12, decimal_places=6, min_value=Decimal(0), default=Decimal(0)
    )
    cached_input_price = serializers.DecimalField(
        source="current_version.cached_input_price",
        max_digits=12,
        decimal_places=6,
        min_value=Decimal(0),
        default=Decimal(0),
    )
    output_price = serializers.DecimalField(
        source="current_version.output_price", max_digits=12, decimal_places=6, min_value=Decimal(0), default=Decimal(0)
    )
    supports_tools = serializers.BooleanField(source="current_version.supports_tools", default=False)
    supports_images = serializers.BooleanField(source="current_version.supports_images", default=False)
    supports_documents = serializers.BooleanField(source="current_version.supports_documents", default=False)
    supports_audio = serializers.BooleanField(source="current_version.supports_audio", default=False)
    supports_video = serializers.BooleanField(source="current_version.supports_video", default=False)
    supports_structured_output = serializers.BooleanField(
        source="current_version.supports_structured_output", default=False
    )
    supports_prompt_caching = serializers.BooleanField(source="current_version.supports_prompt_caching", default=False)
    supports_reasoning = serializers.BooleanField(source="current_version.supports_reasoning", default=False)
    effort_values = serializers.ListField(
        source="current_version.effort_values",
        child=serializers.CharField(max_length=50),
        max_length=20,
        default=list,
        help_text=_("モデルが受け付けるeffortの値（各社の指定そのまま）。推論の制御に対応するモデルだけ"),
    )
    default_effort = serializers.CharField(
        source="current_version.default_effort", max_length=50, required=False, allow_blank=True, default=""
    )
    change_reason = serializers.CharField(max_length=500, required=False, allow_blank=True, write_only=True)
    created_by: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(slug_field="uid", read_only=True)

    class Meta:
        model = ModelProfile
        fields = [
            "id",
            "name",
            "is_active",
            "is_default",
            "version",
            *VERSION_FIELDS,
            "connection_info",
            "change_reason",
            "created_by",
            "created",
            "updated",
        ]
        read_only_fields = ["is_default", "created", "updated"]

    def validate_name(self, value: str) -> str:
        value = value.strip()
        others = ModelProfile.objects.filter(deleted=False, name=value)
        if self.instance:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise serializers.ValidationError(_("同じ名前のモデル設定があります"))
        return value

    def validate_model_name(self, value: str) -> str:
        return value.strip()

    def validate(self, attrs):
        if self.instance is not None and "version_number" not in attrs:
            raise serializers.ValidationError({"version": _("編集を始めた時点の版を送ってください")})
        # 部分更新（PATCH）では、送られなかった項目を現在の版の値で補って検証する
        content = attrs.get("current_version", {})
        current = self.instance.current_version if self.instance else None
        max_input = content.get("max_input_tokens", getattr(current, "max_input_tokens", None))
        max_output = content.get("max_output_tokens", getattr(current, "max_output_tokens", None))
        errors = defaultdict(list)
        if max_input is not None and max_output is not None and max_output > max_input:
            errors["max_output_tokens"].append(_("最大出力tokenは、最大入力token以下にしてください"))
        reasoning = content.get("supports_reasoning", getattr(current, "supports_reasoning", False))
        efforts = [v.strip() for v in content.get("effort_values", getattr(current, "effort_values", []))]
        default_effort = content.get("default_effort", getattr(current, "default_effort", "")).strip()
        if efforts and not reasoning:
            errors["effort_values"].append(_("effortの値は、推論の制御に対応するモデルだけに設定できます"))
        if len(set(efforts)) != len(efforts) or "" in efforts:
            errors["effort_values"].append(_("effortの値は、空でない別々の値にしてください"))
        if default_effort and default_effort not in efforts:
            errors["default_effort"].append(_("既定のeffortは、effortの値から選んでください"))
        if errors:
            raise serializers.ValidationError(errors)
        if "current_version" in attrs:
            for name, value in (("effort_values", efforts), ("default_effort", default_effort)):
                if name in attrs["current_version"]:
                    attrs["current_version"][name] = value
        return attrs

    def _content(self, validated_data, current: ModelProfileVersion | None) -> dict[str, Any]:
        """送られた項目を優先し、残りは現在の版の値で補う"""
        sent = validated_data.pop("current_version", {})
        return {name: sent[name] if name in sent else getattr(current, name) for name in VERSION_FIELDS}

    def create(self, validated_data) -> ModelProfile:
        validated_data.pop("version_number", None)
        return ModelProfile.create_with_version(
            name=validated_data["name"],
            actor=validated_data["created_by"],
            change_reason=validated_data.get("change_reason", ""),
            **self._content(validated_data, None),
        )

    def update(self, instance: ModelProfile, validated_data) -> ModelProfile:
        try:
            instance.revise(
                actor=validated_data.pop("actor"),
                expected_version=validated_data.pop("version_number"),
                change_reason=validated_data.pop("change_reason", ""),
                **self._content(validated_data, instance.current_version),
            )
        except VersionConflict as exc:
            raise Conflict() from exc
        for name in ("name", "is_active"):
            if name in validated_data:
                setattr(instance, name, validated_data[name])
        instance.save(update_fields=["name", "is_active", "updated"])
        return instance


class AppModelProfileSerializer(serializers.ModelSerializer):
    """社員が会話で選ぶためのモデル設定。接続先や単価は出さない"""

    id = serializers.CharField(source="uid", read_only=True)
    is_local = serializers.BooleanField(source="current_version.is_local", read_only=True)
    supports_tools = serializers.BooleanField(source="current_version.supports_tools", read_only=True)
    supports_images = serializers.BooleanField(source="current_version.supports_images", read_only=True)
    supports_documents = serializers.BooleanField(source="current_version.supports_documents", read_only=True)
    supports_reasoning = serializers.BooleanField(source="current_version.supports_reasoning", read_only=True)
    effort_values = serializers.ListField(
        source="current_version.effort_values", child=serializers.CharField(), read_only=True
    )
    default_effort = serializers.CharField(source="current_version.default_effort", read_only=True)

    class Meta:
        model = ModelProfile
        fields = [
            "id",
            "name",
            "is_default",
            "is_local",
            "supports_tools",
            "supports_images",
            "supports_documents",
            "supports_reasoning",
            "effort_values",
            "default_effort",
        ]
        read_only_fields = fields
