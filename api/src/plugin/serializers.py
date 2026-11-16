from datetime import timedelta

from django.db.models import Max, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from utils.serializers import ReadOnlyModelSerializer

from .models import Plugin, PluginExtension

# 管理画面に出す実行時の観察の期間
FAILURE_DAYS = 7


class PluginExtensionSerializer(ReadOnlyModelSerializer):
    id = serializers.CharField(source="extension_id", read_only=True)
    type = serializers.CharField(source="extension_type", read_only=True)
    capabilities = serializers.ListField(child=serializers.CharField(), read_only=True)
    failures = serializers.SerializerMethodField(
        help_text=_("直近7日の予期しない失敗・時間切れの件数（Pluginの版ごと）と最後のエラー")
    )

    class Meta:
        model = PluginExtension
        fields = ["id", "type", "name", "capabilities", "deployed", "failures"]

    @extend_schema_field(
        {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "pluginVersion": {"type": "string"},
                    "unexpected": {"type": "integer"},
                    "timeouts": {"type": "integer"},
                    "lastError": {"type": "string"},
                    "lastAt": {"type": "string", "format": "date-time", "nullable": True},
                },
                "required": ["pluginVersion", "unexpected", "timeouts", "lastError", "lastAt"],
            },
        }
    )
    def get_failures(self, obj: PluginExtension) -> list[dict]:
        since = timezone.localdate() - timedelta(days=FAILURE_DAYS - 1)
        rows = (
            obj.failures.filter(day__gte=since)
            .values("plugin_version")
            .annotate(unexpected=Sum("unexpected"), timeouts=Sum("timeouts"), last_at=Max("last_at"))
            .order_by("-last_at")
        )
        result = []
        for row in rows:
            last = obj.failures.filter(plugin_version=row["plugin_version"], last_at=row["last_at"]).first()
            result.append(
                {
                    "pluginVersion": row["plugin_version"],
                    "unexpected": row["unexpected"],
                    "timeouts": row["timeouts"],
                    "lastError": last.last_error if last else "",
                    "lastAt": row["last_at"],
                }
            )
        return result


class PluginSerializer(serializers.ModelSerializer):
    """Plugin（基盤管理者。design.md §3.11.1）。変えられるのは有効か（`enabled`）だけ"""

    id = serializers.CharField(source="plugin_id", read_only=True)
    usable = serializers.BooleanField(read_only=True, help_text=_("使える（有効で、配備されていて、確かめに通った）"))
    extensions = PluginExtensionSerializer(many=True, read_only=True)

    class Meta:
        model = Plugin
        fields = [
            "id",
            "name",
            "description",
            "version",
            "builtin",
            "enabled",
            "deployed",
            "problem",
            "deprecated",
            "replaced_by",
            "has_self_check",
            "usable",
            "extensions",
            "updated",
        ]
        read_only_fields = [f for f in fields if f != "enabled"]


class SelectableExtensionSerializer(serializers.Serializer):
    """新しく作る入口・出口・Connectorの接続で選べるAdapter"""

    id = serializers.CharField()
    type = serializers.CharField()
    name = serializers.CharField()
    capabilities = serializers.ListField(child=serializers.CharField())
    config_schema = serializers.JSONField(help_text=_("設定のJSON Schema（画面の既定値に使う）"))
