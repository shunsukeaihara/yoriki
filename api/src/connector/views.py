from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from utils.permissions import PlatformAdmin
from utils.views import SoftDeleteMixin

from .adapters import get_adapter
from .errors import ConnectorError
from .models import Connector, ConnectorKind, ConnectorVersion
from .serializers import (
    ConnectorCheckSerializer,
    ConnectorQueryResultSerializer,
    ConnectorQuerySerializer,
    ConnectorSerializer,
    ConnectorTablesSerializer,
    ConnectorUpdateSerializer,
    ConnectorVersionSerializer,
)

# 試し実行の応答に載せる行数。行数・サイズの上限はConnectorの版の値で、エージェントと同じ
PREVIEW_ROWS = 200


class ConnectorFilter(filters.FilterSet):
    name = filters.CharFilter(label=_("名前"), lookup_expr="icontains")
    kind = filters.ChoiceFilter(label=_("種別"), choices=ConnectorKind.choices)
    is_sensitive = filters.BooleanFilter(label=_("機密"), field_name="current_version__is_sensitive")

    class Meta:
        model = Connector
        fields = ["name", "kind", "is_active", "is_sensitive"]


CONFLICT = OpenApiResponse(description="他の変更と競合した（編集を始めた後に、別の版ができている）")


@extend_schema(description="Connectorの管理API（基盤管理者だけ）", tags=["manage/connector"])
@extend_schema_view(
    update=extend_schema(request=ConnectorUpdateSerializer, responses={200: ConnectorSerializer, 409: CONFLICT}),
    partial_update=extend_schema(
        request=ConnectorUpdateSerializer, responses={200: ConnectorSerializer, 409: CONFLICT}
    ),
)
class ConnectorViewSet(SoftDeleteMixin, ModelViewSet):
    """接続先と資格情報、エージェントが扱える範囲（境界）を決めるので、基盤管理者だけにする（design.md §4.9.1）。

    境界（公開する表、上限、機密のフラグ）を変えて保存すると新しい版ができる。進行中のRunは開始時の版を使い続ける
    """

    permission_classes = [PlatformAdmin]
    lookup_field = "uid"
    queryset = (
        Connector.objects.filter(deleted=False)
        .select_related("created_by", "current_version")
        .prefetch_related("permissions__group", "permissions__department")
    )
    serializer_class = ConnectorSerializer
    filterset_class = ConnectorFilter
    search_fields = ("name",)
    ordering = ("name", "pk")

    def get_serializer_class(self):
        # 更新では種別と接続先（ホスト・ポート・DB名）を変えられない
        if self.action in ("update", "partial_update"):
            return ConnectorUpdateSerializer
        return super().get_serializer_class()

    def perform_create(self, serializer):
        # Connectorと最初の版、利用の許可は ConnectorSerializer.create() の transaction で作る
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(actor=self.request.user)

    @extend_schema(
        description="接続先に接続して、ホスト・認証・DB名が正しいかを確かめる",
        request=None,
        responses=ConnectorCheckSerializer,
    )
    @action(detail=True, methods=["post"], serializer_class=ConnectorCheckSerializer)
    def check(self, request, *args, **kwargs):
        try:
            get_adapter(self.get_object()).check()
            result = {"ok": True, "detail": ""}
        except ConnectorError as exc:
            result = {"ok": False, "detail": exc.message}
        return Response(self.get_serializer(result).data)

    @extend_schema(
        description="接続先のアカウントが読める表・ビューと列の一覧。公開する表を選ぶために使う",
        request=None,
        responses=ConnectorTablesSerializer,
    )
    @action(detail=True, methods=["get"], serializer_class=ConnectorTablesSerializer)
    def tables(self, request, *args, **kwargs):
        try:
            result = ConnectorTablesSerializer.build(get_adapter(self.get_object()).list_tables())
        except ConnectorError as exc:
            result = ConnectorTablesSerializer.build(None, exc.message)
        return Response(self.get_serializer(result).data)

    @extend_schema(
        description="公開している表・ビューの列と説明。エージェントの `datasource_describe` と同じ内容",
        request=None,
        responses=ConnectorTablesSerializer,
    )
    @action(detail=True, methods=["get"], serializer_class=ConnectorTablesSerializer)
    def describe(self, request, *args, **kwargs):
        try:
            result = ConnectorTablesSerializer.build(get_adapter(self.get_object()).describe())
        except ConnectorError as exc:
            result = ConnectorTablesSerializer.build(None, exc.message)
        return Response(self.get_serializer(result).data)

    @extend_schema(
        description="SELECT文を試しに実行する。エージェントの `datasource_query` と同じ検査・上限を通る。"
        f"応答の行は先頭の{PREVIEW_ROWS}行まで",
        request=ConnectorQuerySerializer,
        responses=ConnectorQueryResultSerializer,
    )
    @action(detail=True, methods=["post"], serializer_class=ConnectorQuerySerializer)
    def query(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        adapter = get_adapter(self.get_object())
        sql, params = serializer.validated_data["sql"], serializer.validated_data["params"]
        try:
            data = ConnectorQueryResultSerializer.build(adapter.query(sql, params), preview_rows=PREVIEW_ROWS)
        except ConnectorError as exc:
            data = ConnectorQueryResultSerializer.build(None, error_kind=exc.kind, detail=exc.message)
        return Response(ConnectorQueryResultSerializer(data).data)


class ConnectorMixin:
    """URLのconnectorを、削除されていないConnectorに限定して取得する"""

    kwargs: dict[str, str]

    @property
    def connector(self) -> Connector:
        if not hasattr(self, "_connector"):
            self._connector = get_object_or_404(Connector.objects.all(), uid=self.kwargs["connector"], deleted=False)
        return self._connector

    def get_queryset(self):
        return super().get_queryset().filter(connector=self.connector)  # type: ignore[misc]


@extend_schema(description="Connectorの版の履歴（基盤管理者だけ）", tags=["manage/connector/version"])
class ConnectorVersionViewSet(ConnectorMixin, ReadOnlyModelViewSet):
    permission_classes = [PlatformAdmin]
    lookup_field = "number"  # 版番号で識別する子リソースの例外（backend-conventions.md §4.1）
    queryset = ConnectorVersion.objects.select_related("created_by")
    serializer_class = ConnectorVersionSerializer
    ordering = "-number"  # Connectorの中で一意
