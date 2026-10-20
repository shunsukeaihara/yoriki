from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from utils.exceptions import Conflict
from utils.permissions import PlatformAdmin
from utils.views import SoftDeleteMixin

from .models import LLMConnection, ModelProfile, ModelProfileVersion, ProviderKind
from .providers import get_adapter
from .providers.base import ProviderError
from .serializers import (
    LLMConnectionCheckSerializer,
    LLMConnectionSerializer,
    LLMConnectionUpdateSerializer,
    ModelProfileSerializer,
    ModelProfileVersionSerializer,
)


class LLMConnectionFilter(filters.FilterSet):
    name = filters.CharFilter(label=_("名前"), lookup_expr="icontains")
    kind = filters.ChoiceFilter(label=_("種別"), choices=ProviderKind.choices)

    class Meta:
        model = LLMConnection
        fields = ["name", "kind", "is_active"]


@extend_schema(description="LLMのプロバイダ接続の管理API（基盤管理者だけ）", tags=["manage/llm_connection"])
@extend_schema_view(
    update=extend_schema(request=LLMConnectionUpdateSerializer, responses=LLMConnectionSerializer),
    partial_update=extend_schema(request=LLMConnectionUpdateSerializer, responses=LLMConnectionSerializer),
)
class LLMConnectionViewSet(SoftDeleteMixin, ModelViewSet):
    # API keyを扱い、接続先を決めるので、基盤管理者だけにする（design.md §4.8）
    permission_classes = [PlatformAdmin]
    lookup_field = "uid"
    queryset = LLMConnection.objects.filter(deleted=False).select_related("created_by")
    serializer_class = LLMConnectionSerializer
    filterset_class = LLMConnectionFilter
    ordering = ("name", "pk")

    def get_serializer_class(self):
        # 更新では種別とbase URL（接続先）を変えられない
        if self.action in ("update", "partial_update"):
            return LLMConnectionUpdateSerializer
        return super().get_serializer_class()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_destroy(self, instance: LLMConnection):
        if instance.is_in_use():
            raise Conflict(_("このプロバイダ接続を使っているモデル設定があるため、削除できません"))
        super().perform_destroy(instance)

    @extend_schema(
        description="接続先に問い合わせて、API keyと接続先が正しいかを確かめる。使えるモデル名も返す",
        request=None,
        responses=LLMConnectionCheckSerializer,
    )
    @action(detail=True, methods=["post"], serializer_class=LLMConnectionCheckSerializer)
    def check(self, request, *args, **kwargs):
        connection = self.get_object()
        try:
            result = {"ok": True, "detail": "", "models": get_adapter(connection).list_models()}
        except ProviderError as exc:
            result = {"ok": False, "detail": str(exc), "models": []}
        return Response(self.get_serializer(result).data)


class ModelProfileFilter(filters.FilterSet):
    name = filters.CharFilter(label=_("名前"), lookup_expr="icontains")
    connection = filters.CharFilter(label=_("プロバイダ接続"), field_name="current_version__connection__uid")

    class Meta:
        model = ModelProfile
        fields = ["name", "is_active", "connection"]


CONFLICT = OpenApiResponse(description="他の変更と競合した（編集を始めた後に、別の版ができている）")


@extend_schema(description="モデル設定の管理API（基盤管理者だけ）", tags=["manage/model_profile"])
@extend_schema_view(
    update=extend_schema(responses={200: ModelProfileSerializer, 409: CONFLICT}),
    partial_update=extend_schema(responses={200: ModelProfileSerializer, 409: CONFLICT}),
)
class ModelProfileViewSet(SoftDeleteMixin, ModelViewSet):
    """中身（接続、モデル名、上限、単価、能力）を変えて保存すると新しい版ができる。進行中のRunは受付時の版を使い続ける"""

    permission_classes = [PlatformAdmin]
    lookup_field = "uid"
    queryset = ModelProfile.objects.filter(deleted=False).select_related(
        "created_by", "current_version__connection", "current_version__created_by"
    )
    serializer_class = ModelProfileSerializer
    filterset_class = ModelProfileFilter
    ordering = ("name", "pk")

    def perform_create(self, serializer):
        # モデル設定と最初の版は ModelProfile.create_with_version() の transaction で作る
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        # 新しい版の作成は ModelProfile.revise() の transaction で行う
        serializer.save(actor=self.request.user)


class ModelProfileMixin:
    """URLのmodel_profileを、削除されていないモデル設定に限定して取得する"""

    kwargs: dict[str, str]

    @property
    def model_profile(self) -> ModelProfile:
        if not hasattr(self, "_model_profile"):
            self._model_profile = get_object_or_404(
                ModelProfile.objects.all(), uid=self.kwargs["model_profile"], deleted=False
            )
        return self._model_profile

    def get_queryset(self):
        return super().get_queryset().filter(profile=self.model_profile)  # type: ignore[misc]


@extend_schema(description="モデル設定の版の履歴（基盤管理者だけ）", tags=["manage/model_profile/version"])
class ModelProfileVersionViewSet(ModelProfileMixin, ReadOnlyModelViewSet):
    permission_classes = [PlatformAdmin]
    lookup_field = "number"  # 版番号で識別する子リソースの例外（backend-conventions.md §4.1）
    queryset = ModelProfileVersion.objects.select_related("connection", "created_by")
    serializer_class = ModelProfileVersionSerializer
    ordering = "-number"  # モデル設定の中で一意
