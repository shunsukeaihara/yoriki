from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from utils.exceptions import Conflict
from utils.permissions import ModelPermissions
from utils.views import SoftDeleteMixin

from .models import RuntimeProfile, RuntimeProfileVersion
from .serializers import RuntimeProfileSerializer, RuntimeProfileVersionSerializer

CONFLICT = OpenApiResponse(description="他の変更と競合した（編集を始めた後に、別の版ができている）")


class RuntimeProfileFilter(filters.FilterSet):
    name = filters.CharFilter(label=_("名前"), lookup_expr="icontains")

    class Meta:
        model = RuntimeProfile
        fields = ["name", "is_active"]


@extend_schema(description="Runtime Profileの管理API", tags=["manage/runtime_profile"])
@extend_schema_view(
    update=extend_schema(responses={200: RuntimeProfileSerializer, 409: CONFLICT}),
    partial_update=extend_schema(responses={200: RuntimeProfileSerializer, 409: CONFLICT}),
)
class RuntimeProfileViewSet(SoftDeleteMixin, ModelViewSet):
    """中身（イメージ、資源の上限）を変えて保存すると新しい版ができる。始まっているRunは固定した版を使い続ける"""

    permission_classes = [ModelPermissions]
    action_permissions = {"make_default": "change"}
    lookup_field = "uid"
    queryset = RuntimeProfile.objects.filter(deleted=False).select_related("created_by", "current_version__created_by")
    serializer_class = RuntimeProfileSerializer
    filterset_class = RuntimeProfileFilter
    search_fields = ("name",)
    ordering = ("name", "pk")

    def perform_create(self, serializer):
        # Runtime Profileと最初の版は RuntimeProfile.create_with_version() の transaction で作る
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        # 新しい版の作成は RuntimeProfile.revise() の transaction で行う
        serializer.save(actor=self.request.user)

    @extend_schema(
        description="既定のRuntime Profileにする（これまでの既定は外れる）。以後に始めるRunが使う",
        request=None,
        responses=RuntimeProfileSerializer,
    )
    @action(detail=True, methods=["post"], serializer_class=RuntimeProfileSerializer)
    def make_default(self, request, *args, **kwargs):
        profile = self.get_object()
        if not profile.is_active:
            raise Conflict(_("無効なRuntime Profileは既定にできません"))
        profile.make_default()
        return Response(self.get_serializer(profile).data)


class RuntimeProfileMixin:
    """URLのruntime_profileを、削除されていないRuntime Profileに限定して取得する"""

    kwargs: dict[str, str]

    @property
    def runtime_profile(self) -> RuntimeProfile:
        if not hasattr(self, "_runtime_profile"):
            self._runtime_profile = get_object_or_404(
                RuntimeProfile.objects.all(), uid=self.kwargs["runtime_profile"], deleted=False
            )
        return self._runtime_profile

    def get_queryset(self):
        return super().get_queryset().filter(profile=self.runtime_profile)  # type: ignore[misc]


@extend_schema(description="Runtime Profileの版の履歴", tags=["manage/runtime_profile/version"])
class RuntimeProfileVersionViewSet(RuntimeProfileMixin, ReadOnlyModelViewSet):
    permission_classes = [ModelPermissions]
    permission_model = RuntimeProfile
    lookup_field = "number"  # 版番号で識別する子リソースの例外（backend-conventions.md §4.1）
    queryset = RuntimeProfileVersion.objects.select_related("created_by")
    serializer_class = RuntimeProfileVersionSerializer
    ordering = "-number"  # Runtime Profileの中で一意
