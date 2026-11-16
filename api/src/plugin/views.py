import logging

from django.db import transaction
from django.utils.translation import gettext as _
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, serializers
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from utils.permissions import AppUser, ModelPermissions

from . import registry
from .models import Plugin
from .serializers import PluginSerializer, SelectableExtensionSerializer
from .spec import ExtensionType

logger = logging.getLogger(__name__)


@extend_schema(
    description="Pluginの一覧と有効・停止（基盤管理者。design.md §3.11.1）。行は配備されているコードから作る",
    tags=["manage/plugin"],
)
class PluginViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin, GenericViewSet):
    permission_classes = [ModelPermissions]
    lookup_field = "plugin_id"
    lookup_value_regex = "[^/]+"
    queryset = Plugin.objects.prefetch_related("extensions")
    serializer_class = PluginSerializer
    filterset_fields = ["builtin", "enabled", "deployed"]
    search_fields = ("plugin_id", "name")
    ordering = ("-builtin", "plugin_id")
    http_method_names = ["get", "patch", "head", "options"]

    @transaction.atomic
    def perform_update(self, serializer):
        plugin = Plugin.objects.select_for_update().get(pk=serializer.instance.pk)
        enabled = serializer.validated_data.get("enabled", plugin.enabled)
        if enabled != plugin.enabled and plugin.extensions.filter(extension_type=ExtensionType.STORAGE).exists():
            # 保存先は環境変数で選ぶ（design.md §10.2）。保存したものを読めなくしないよう、画面からは止めない
            raise serializers.ValidationError({"enabled": _("保存先のPluginは、画面からは止められません")})
        if enabled and not plugin.enabled:
            self._check_enable(plugin)
        serializer.save()

    @staticmethod
    def _check_enable(plugin: Plugin) -> None:
        """有効にする時の確かめ（design.md §3.11.3）：配備されている、形と宣言の確かめに通った、廃止予定でない、
        自己診断（あれば）に通る"""
        if not plugin.deployed:
            raise serializers.ValidationError({"enabled": _("このPluginは今の配備にありません")})
        if plugin.problem:
            raise serializers.ValidationError({"enabled": _("登録の確かめに通っていません：%s") % plugin.problem})
        if plugin.deprecated:
            raise serializers.ValidationError({"enabled": _("廃止予定のPluginは有効にできません")})
        loaded = registry.loaded().get(plugin.plugin_id)
        if loaded is None or not loaded.valid or loaded.spec is None:
            raise serializers.ValidationError({"enabled": _("このPluginをこのサーバーで読み込めません")})
        if loaded.spec.self_check is not None:
            try:
                loaded.spec.self_check()
            except Exception as exc:
                logger.warning("plugin %s self check failed: %s", plugin.plugin_id, exc)
                raise serializers.ValidationError(
                    {"enabled": _("自己診断に失敗しました：%s") % str(exc)[:500]}
                ) from exc


@extend_schema(
    description="新しく作る入口・出口・Connectorの接続で選べる種別（使えて、廃止予定でないPluginのAdapter）",
    tags=["app/plugin"],
    parameters=[OpenApiParameter("type", str, enum=[t.value for t in ExtensionType], description="拡張種別")],
    responses=SelectableExtensionSerializer(many=True),
)
class AppPluginExtensionViewSet(GenericViewSet):
    permission_classes = [AppUser]
    serializer_class = SelectableExtensionSerializer
    pagination_class = None

    def list(self, request, *args, **kwargs):
        try:
            extension_type = ExtensionType(request.query_params.get("type", ""))
        except ValueError:
            raise serializers.ValidationError({"type": _("拡張種別を指定してください")}) from None
        ids = registry.available_ids(extension_type, for_new=True)
        items = [
            {
                "id": extension.id,
                "type": str(extension.type),
                "name": extension.name,
                "capabilities": sorted(extension.capabilities),
                "config_schema": extension.config_schema,
            }
            for extension_id, (extension, _spec) in sorted(registry.extensions().items())
            if extension_id in ids
        ]
        return Response(self.get_serializer(items, many=True).data)
