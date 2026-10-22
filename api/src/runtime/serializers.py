from collections import defaultdict
from decimal import Decimal
from typing import Any

from django.db.models import Field
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from utils.exceptions import Conflict

from .errors import RegistryError, VersionConflict
from .models import VERSION_FIELDS, RuntimeProfile, RuntimeProfileVersion
from .registry import inspect_image

# 資源の上限として入力を受け付ける項目（イメージの情報はレジストリから読む）
RESOURCE_FIELDS = (
    "memory_mb",
    "cpus",
    "pids_limit",
    "tmp_mb",
    "work_mb",
    "output_mb",
    "env_mb",
    "command_timeout_seconds",
    "job_timeout_seconds",
)
# Sandboxのコマンド・環境に残しておくメモリ（tmpfsの合計はメモリの上限からこれを引いた分まで）
RESERVED_MEMORY_MB = 512


def _default(name: str) -> Any:
    field = RuntimeProfileVersion._meta.get_field(name)
    assert isinstance(field, Field)
    return field.get_default()


class RuntimeProfileVersionSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)
    image = serializers.CharField(read_only=True)
    created_by: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(slug_field="uid", read_only=True)

    class Meta:
        model = RuntimeProfileVersion
        fields = ["id", "number", "image", *VERSION_FIELDS, "change_reason", "created_by", "created"]
        read_only_fields = fields


class RuntimeProfileSerializer(serializers.ModelSerializer):
    """Runtime Profile。中身（イメージ、資源の上限）は現在の版の値を平らに出す。

    イメージは「名前@sha256:digest」で受け取り、レジストリのAPIで契約番号と対応platformを確かめてから版に入れる
    （design.md §7.3）。中身を変えて保存すると新しい版を作る。更新では、編集を始めた時点の `version` を送る
    """

    id = serializers.CharField(source="uid", read_only=True)
    version = serializers.IntegerField(
        source="version_number",
        min_value=0,
        required=False,
        help_text=_("現在の版。更新では、編集を始めた時点の値を送る（作成では送らない）"),
    )
    image = serializers.CharField(
        source="current_version.image",
        max_length=500,
        help_text=_("「名前@sha256:digest」（例：yoriki/yoriki-sandbox-docs@sha256:…）。タグだけの指定はできない"),
    )
    contract = serializers.IntegerField(source="current_version.contract", read_only=True)
    platforms = serializers.ListField(source="current_version.platforms", child=serializers.CharField(), read_only=True)
    python_version = serializers.CharField(source="current_version.python_version", read_only=True)
    memory_mb = serializers.IntegerField(
        source="current_version.memory_mb", min_value=512, max_value=65536, required=False
    )
    cpus = serializers.DecimalField(
        source="current_version.cpus",
        max_digits=4,
        decimal_places=2,
        min_value=Decimal("0.25"),
        max_value=Decimal(64),
        required=False,
    )
    pids_limit = serializers.IntegerField(
        source="current_version.pids_limit", min_value=64, max_value=4096, required=False
    )
    tmp_mb = serializers.IntegerField(source="current_version.tmp_mb", min_value=16, max_value=8192, required=False)
    work_mb = serializers.IntegerField(source="current_version.work_mb", min_value=64, max_value=32768, required=False)
    output_mb = serializers.IntegerField(
        source="current_version.output_mb", min_value=16, max_value=32768, required=False
    )
    env_mb = serializers.IntegerField(source="current_version.env_mb", min_value=16, max_value=32768, required=False)
    command_timeout_seconds = serializers.IntegerField(
        source="current_version.command_timeout_seconds", min_value=10, max_value=3600, required=False
    )
    job_timeout_seconds = serializers.IntegerField(
        source="current_version.job_timeout_seconds", min_value=60, max_value=86400, required=False
    )
    change_reason = serializers.CharField(max_length=500, required=False, allow_blank=True, write_only=True)
    created_by: "serializers.SlugRelatedField[Any]" = serializers.SlugRelatedField(slug_field="uid", read_only=True)

    class Meta:
        model = RuntimeProfile
        fields = [
            "id",
            "name",
            "is_active",
            "is_default",
            "version",
            "image",
            "contract",
            "platforms",
            "python_version",
            *RESOURCE_FIELDS,
            "change_reason",
            "created_by",
            "created",
            "updated",
        ]
        read_only_fields = ["is_default", "created", "updated"]

    def validate_name(self, value: str) -> str:
        value = value.strip()
        others = RuntimeProfile.objects.filter(deleted=False, name=value)
        if self.instance:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise serializers.ValidationError(_("同じ名前のRuntime Profileがあります"))
        return value

    def validate(self, attrs):
        if self.instance is not None and "version_number" not in attrs:
            raise serializers.ValidationError({"version": _("編集を始めた時点の版を送ってください")})
        sent = attrs.get("current_version", {})
        current = self.instance.current_version if self.instance else None
        # 部分更新（PATCH）と作成では、送られなかった項目を現在の版の値か既定値で補う
        content = {
            name: sent[name] if name in sent else getattr(current, name, _default(name)) for name in RESOURCE_FIELDS
        }
        errors: dict[str, list[Any]] = defaultdict(list)
        tmpfs = content["tmp_mb"] + content["work_mb"] + content["output_mb"] + content["env_mb"]
        if tmpfs > content["memory_mb"] - RESERVED_MEMORY_MB:
            errors["memory_mb"].append(
                _("メモリは、/tmp・work・output・envの合計（%(tmpfs)sMB）より%(reserved)sMB以上大きくしてください")
                % {"tmpfs": tmpfs, "reserved": RESERVED_MEMORY_MB}
            )
        if content["command_timeout_seconds"] > content["job_timeout_seconds"]:
            errors["command_timeout_seconds"].append(
                _("コマンドの待ち時間は、ジョブの実行時間の上限以下にしてください")
            )
        if errors:
            raise serializers.ValidationError(errors)
        if "image" in sent and (current is None or sent["image"].strip() != current.image):
            try:
                inspected = inspect_image(sent["image"])
            except RegistryError as exc:
                raise serializers.ValidationError({"image": [exc.message]}) from exc
            attrs["inspected"] = {
                "image_repository": inspected.reference.repository,
                "image_digest": inspected.reference.digest,
                "contract": inspected.contract,
                "platforms": inspected.platforms,
                "platform_digests": inspected.platform_digests,
                "python_version": inspected.python_version,
            }
        attrs["resources"] = content
        return attrs

    def _content(self, validated_data, current: RuntimeProfileVersion | None) -> dict[str, Any]:
        validated_data.pop("current_version", None)
        image = validated_data.pop("inspected", None) or {
            name: getattr(current, name) for name in VERSION_FIELDS if name not in RESOURCE_FIELDS
        }
        return image | validated_data.pop("resources")

    def create(self, validated_data) -> RuntimeProfile:
        validated_data.pop("version_number", None)
        return RuntimeProfile.create_with_version(
            name=validated_data["name"],
            actor=validated_data["created_by"],
            change_reason=validated_data.get("change_reason", ""),
            **self._content(validated_data, None),
        )

    def update(self, instance: RuntimeProfile, validated_data) -> RuntimeProfile:
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
