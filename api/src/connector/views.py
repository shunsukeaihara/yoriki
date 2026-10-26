from django.db import transaction
from django.db.models import Count, Q
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

from .adapters import get_adapter
from .errors import ConnectorError
from .models import Connector, ConnectorKind, ConnectorVersion, DatabaseConnection
from .serializers import (
    ConnectorCheckSerializer,
    ConnectorQueryResultSerializer,
    ConnectorQuerySerializer,
    ConnectorSerializer,
    ConnectorTablesSerializer,
    ConnectorUpdateSerializer,
    ConnectorVersionSerializer,
    DatabaseConnectionSerializer,
    DatabaseConnectionUpdateSerializer,
)

# 試し実行の応答に載せる行数。行数・サイズの上限はConnectorの版の値で、エージェントと同じ
PREVIEW_ROWS = 200


class DatabaseConnectionFilter(filters.FilterSet):
    name = filters.CharFilter(label=_("名前"), lookup_expr="icontains")
    kind = filters.ChoiceFilter(label=_("種別"), choices=ConnectorKind.choices)

    class Meta:
        model = DatabaseConnection
        fields = ["name", "kind", "is_active"]


class ConnectorFilter(filters.FilterSet):
    name = filters.CharFilter(label=_("名前"), lookup_expr="icontains")
    connection = filters.CharFilter(label=_("接続先"), field_name="connection__uid")
    kind = filters.ChoiceFilter(label=_("種別"), field_name="connection__kind", choices=ConnectorKind.choices)
    is_sensitive = filters.BooleanFilter(label=_("機密"), field_name="current_version__is_sensitive")

    class Meta:
        model = Connector
        fields = ["name", "connection", "kind", "is_active", "is_sensitive"]


CONFLICT = OpenApiResponse(description="他の変更と競合した（編集を始めた後に、別の版ができている）")


@extend_schema(description="DB接続の管理API（基盤管理者だけ）", tags=["manage/connector/database_connection"])
@extend_schema_view(
    update=extend_schema(request=DatabaseConnectionUpdateSerializer, responses=DatabaseConnectionSerializer),
    partial_update=extend_schema(request=DatabaseConnectionUpdateSerializer, responses=DatabaseConnectionSerializer),
)
class DatabaseConnectionViewSet(SoftDeleteMixin, ModelViewSet):
    """社内DBの接続先と資格情報。接続先を変えるときはパスワードを入れ直す。変えると、この接続を使うConnectorの
    次の操作から効く（design.md §4.9.1）
    """

    permission_classes = [PlatformAdmin]
    lookup_field = "uid"
    queryset = (
        DatabaseConnection.objects.filter(deleted=False)
        .select_related("created_by")
        .annotate(connector_count=Count("connectors", filter=Q(connectors__deleted=False)))
    )
    serializer_class = DatabaseConnectionSerializer
    filterset_class = DatabaseConnectionFilter
    search_fields = ("name", "host", "database")
    ordering = ("name", "pk")

    def get_serializer_class(self):
        # 更新では種別を変えられない
        if self.action in ("update", "partial_update"):
            return DatabaseConnectionUpdateSerializer
        return super().get_serializer_class()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @transaction.atomic
    def perform_destroy(self, instance: DatabaseConnection):
        # Connectorの作成と同じ行をロックし、削除と同時に作られたConnectorを見落とさない
        locked = DatabaseConnection.objects.select_for_update().get(pk=instance.pk)
        if locked.connectors.filter(deleted=False).exists():
            raise Conflict(_("この接続先を使っているConnectorがあるため、削除できません"))
        super().perform_destroy(locked)

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
        description="接続先のアカウントが読める表・ビューと列の一覧。Connectorで公開する表を選ぶために使う",
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


@extend_schema(description="Connectorの管理API（基盤管理者だけ）", tags=["manage/connector"])
@extend_schema_view(
    update=extend_schema(request=ConnectorUpdateSerializer, responses={200: ConnectorSerializer, 409: CONFLICT}),
    partial_update=extend_schema(
        request=ConnectorUpdateSerializer, responses={200: ConnectorSerializer, 409: CONFLICT}
    ),
)
class ConnectorViewSet(SoftDeleteMixin, ModelViewSet):
    """エージェントが扱える範囲（境界）と使える人を決めるので、基盤管理者だけにする（design.md §4.9.1）。

    境界（公開する表、上限、機密のフラグ）を変えて保存すると新しい版ができる。進行中のRunは開始時の版を使い続ける。
    接続の確認と表の一覧は、使うDB接続の操作（DatabaseConnectionViewSet）で行う
    """

    permission_classes = [PlatformAdmin]
    lookup_field = "uid"
    queryset = (
        Connector.objects.filter(deleted=False)
        .select_related("created_by", "current_version", "connection")
        .prefetch_related("permissions__group", "permissions__department")
    )
    serializer_class = ConnectorSerializer
    filterset_class = ConnectorFilter
    search_fields = ("name",)
    ordering = ("name", "pk")

    def get_serializer_class(self):
        # 更新では使うDB接続を変えられない
        if self.action in ("update", "partial_update"):
            return ConnectorUpdateSerializer
        return super().get_serializer_class()

    def perform_create(self, serializer):
        # Connectorと最初の版、利用の許可は ConnectorSerializer.create() の transaction で作る
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(actor=self.request.user)

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
            # 応答に載せる行だけをメモリに残す（上限までの全体を貯めない）
            data = ConnectorQueryResultSerializer.build(adapter.query(sql, params, keep_rows=PREVIEW_ROWS))
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
