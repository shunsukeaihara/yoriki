from collections import defaultdict
from typing import Any

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.choices import DataClassification
from utils.crypto import is_encryption_configured
from utils.exceptions import Conflict

from .errors import VersionConflict
from .models import (
    TARGET_IS_FIXED,
    LLMConnection,
    ModelProfile,
    ModelProfileVersion,
    ProviderKind,
)


class LLMConnectionSerializer(serializers.ModelSerializer):
    """プロバイダ接続。API keyは書き込み専用で、応答には末尾の4文字（api_key_hint）だけを出す"""

    id = serializers.CharField(source="uid", read_only=True)
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    api_key = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        max_length=1000,
        trim_whitespace=True,
        help_text=_("送らなければ変更しない。空文字はAPI keyなし（OpenAI互換だけ）"),
    )
    has_api_key = serializers.BooleanField(read_only=True)
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
        read_only_fields = ["api_key_hint", "created", "updated"]
        extra_kwargs = {
            "timeout_seconds": {"min_value": 1, "max_value": 3600},
            "max_concurrency": {"min_value": 1, "max_value": 1000},
            "requests_per_minute": {"min_value": 1},
        }

    def validate_name(self, value: str) -> str:
        value = value.strip()
        others = LLMConnection.objects.filter(deleted=False, name=value)
        if self.instance:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise serializers.ValidationError(_("同じ名前のプロバイダ接続があります"))
        return value

    def validate(self, attrs):
        # 部分更新（PATCH）では、送られなかった項目を既存の値で補って検証する
        kind = ProviderKind(attrs.get("kind", getattr(self.instance, "kind", ProviderKind.OPENAI)))
        base_url = attrs.get("base_url", getattr(self.instance, "base_url", ""))
        api_key = attrs.get("api_key")

        errors = defaultdict(list)
        if kind.uses_base_url and not base_url:
            errors["base_url"].append(_("OpenAI互換の接続では、base URLを入力してください"))
        if not kind.uses_base_url and base_url:
            errors["base_url"].append(_("この種別では、base URLを指定できません"))

        if self.instance is not None:
            # LLMConnection.check_target_change() と同じ制限。ここではフィールドごとのエラーにする
            if kind != self.instance.kind:
                errors["kind"].append(TARGET_IS_FIXED)
            if base_url != self.instance.base_url:
                errors["base_url"].append(TARGET_IS_FIXED)
        has_key_after = bool(api_key) if api_key is not None else bool(self.instance and self.instance.has_api_key)
        if kind.requires_api_key and not has_key_after:
            errors["api_key"].append(_("この種別では、API keyを入力してください"))
        if api_key and not is_encryption_configured():
            errors["api_key"].append(
                _("API keyを保存するための暗号化の鍵（YORIKI_SECRET_ENCRYPTION_KEYS）が設定されていません")
            )
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def create(self, validated_data) -> LLMConnection:
        api_key = validated_data.pop("api_key", "")
        connection = LLMConnection(**validated_data)
        connection.set_api_key(api_key)
        connection.save()
        return connection

    def update(self, instance: LLMConnection, validated_data) -> LLMConnection:
        api_key = validated_data.pop("api_key", None)
        for key, value in validated_data.items():
            setattr(instance, key, value)
        if api_key is not None:
            instance.set_api_key(api_key)
        instance.save()
        return instance


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


VERSION_FIELDS = [
    "connection",
    "model_name",
    "max_input_tokens",
    "max_output_tokens",
    "input_price",
    "cached_input_price",
    "output_price",
    "supports_tools",
    "supports_images",
    "supports_structured_output",
    "supports_prompt_caching",
    "supports_reasoning",
    "data_classification",
    "change_reason",
]


class ModelProfileVersionSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)
    connection: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(slug_field="uid", read_only=True)
    connection_info = MinimumLLMConnectionSerializer(source="connection", read_only=True)
    data_classification_display = serializers.CharField(source="get_data_classification_display", read_only=True)
    created_by: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(slug_field="uid", read_only=True)

    class Meta:
        model = ModelProfileVersion
        fields = [
            "id",
            "number",
            *VERSION_FIELDS,
            "connection_info",
            "data_classification_display",
            "created_by",
            "created",
        ]
        read_only_fields = fields


class ModelProfileVersionWriteSerializer(serializers.ModelSerializer):
    """版の中身の入力。最初の版（ModelProfileCreateSerializer）と新しい版（ModelProfileReviseSerializer）で使う"""

    connection: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(
        slug_field="uid", queryset=LLMConnection.objects.filter(deleted=False, is_active=True)
    )

    class Meta:
        model = ModelProfileVersion
        fields = VERSION_FIELDS
        extra_kwargs = {
            "max_input_tokens": {"min_value": 1},
            "max_output_tokens": {"min_value": 1},
            "input_price": {"min_value": 0},
            "cached_input_price": {"min_value": 0},
            "output_price": {"min_value": 0},
        }

    def validate_model_name(self, value: str) -> str:
        return value.strip()

    def validate(self, attrs):
        errors = defaultdict(list)
        connection: LLMConnection = attrs["connection"]
        if (
            attrs.get("data_classification") == DataClassification.INTERNAL_ONLY
            and connection.kind != ProviderKind.OPENAI_COMPATIBLE
        ):
            # 公式APIは社外のサーバーなので「社内のみ」にできない。社内のサーバーかどうかは登録する基盤管理者が確認する
            errors["data_classification"].append(
                _("「社内のみ」は、OpenAI互換（社内のサーバー）の接続だけに指定できます")
            )
        if attrs["max_output_tokens"] > attrs["max_input_tokens"]:
            errors["max_output_tokens"].append(_("最大出力tokenは、最大入力token以下にしてください"))
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class ModelProfileSerializer(serializers.ModelSerializer):
    """モデル設定。名前と有効・無効だけを更新できる。中身を変えるときは新しい版を作る（revise）"""

    id = serializers.CharField(source="uid", read_only=True)
    current_version = ModelProfileVersionSerializer(read_only=True)
    created_by: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(slug_field="uid", read_only=True)

    class Meta:
        model = ModelProfile
        fields = ["id", "name", "is_active", "current_version", "created_by", "created", "updated"]
        read_only_fields = ["created", "updated"]

    def validate_name(self, value: str) -> str:
        value = value.strip()
        others = ModelProfile.objects.filter(deleted=False, name=value)
        if self.instance:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise serializers.ValidationError(_("同じ名前のモデル設定があります"))
        return value


class ModelProfileCreateSerializer(ModelProfileSerializer):
    """モデル設定と最初の版を作る"""

    version = ModelProfileVersionWriteSerializer(write_only=True)

    class Meta(ModelProfileSerializer.Meta):
        fields = [*ModelProfileSerializer.Meta.fields, "version"]

    def create(self, validated_data) -> ModelProfile:
        version = validated_data.pop("version")
        return ModelProfile.create_with_version(
            name=validated_data["name"], actor=validated_data.pop("created_by"), **version
        )

    def update(self, instance, validated_data):
        raise NotImplementedError("作成だけに使う")

    def to_representation(self, instance: ModelProfile):
        return ModelProfileSerializer(instance, context=self.context).data


class ModelProfileReviseSerializer(ModelProfileVersionWriteSerializer):
    """新しい版を作り、現在の版にする"""

    expected_version = serializers.IntegerField(min_value=0, help_text=_("編集を始めた時点の現在の版"))

    class Meta(ModelProfileVersionWriteSerializer.Meta):
        fields = ["expected_version", *VERSION_FIELDS]

    def create(self, validated_data) -> ModelProfileVersion:
        profile: ModelProfile = validated_data.pop("profile")
        try:
            return profile.revise(actor=validated_data.pop("actor"), **validated_data)
        except VersionConflict as exc:
            raise Conflict() from exc

    def update(self, instance, validated_data):
        raise NotImplementedError("版は更新しない")

    def to_representation(self, instance: ModelProfileVersion):
        return ModelProfileVersionSerializer(instance, context=self.context).data
