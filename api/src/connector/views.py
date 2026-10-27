from django.db import transaction
from django.db.models import Count, Q
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.mixins import ListModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, ModelViewSet, ReadOnlyModelViewSet

from user.models import Department
from user.serializers import MinimumDepartmentSerializer
from utils.exceptions import Conflict
from utils.permissions import AppUser, ModelPermissions
from utils.views import SoftDeleteMixin

from .adapters import get_adapter
from .errors import ConnectorError
from .limits import connection_slot, run_holding
from .models import Connector, ConnectorKind, ConnectorScope, ConnectorVersion, DatabaseConnection
from .serializers import (
    AppConnectorSerializer,
    ConnectorCheckSerializer,
    ConnectorQueryResultSerializer,
    ConnectorQuerySerializer,
    ConnectorSerializer,
    ConnectorTablesSerializer,
    ConnectorUpdateSerializer,
    ConnectorVersionSerializer,
    DatabaseConnectionSerializer,
    DatabaseConnectionUpdateSerializer,
    MinimumDatabaseConnectionSerializer,
)

# 試し実行の応答に載せる行数。行数・サイズの上限はConnectorの版の値で、エージェントと同じ
PREVIEW_ROWS = 200
# 管理画面の操作（接続の確認、表の一覧、説明、試し実行）で、接続の同時実行の枠が空くのを待つ上限（秒）
PREVIEW_WAIT_SECONDS = 10
# 表の一覧と接続の確認に見込む時間（秒）。接続のタイムアウトに足して、枠の期限にする
CATALOG_SECONDS = 30


def in_slot(connection: DatabaseConnection, request, hold_seconds: int, operation):
    """管理画面の操作も、エージェントの操作と同じ接続の同時実行数の上限の中で行う（design.md §4.9.5）"""
    with connection_slot(
        connection, f"manage:{request.user.uid}", hold_seconds=hold_seconds, wait_seconds=PREVIEW_WAIT_SECONDS
    ) as renew:
        return run_holding(operation, renew)


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
    scope = filters.ChoiceFilter(label=_("範囲"), choices=ConnectorScope.choices)
    department = filters.CharFilter(label=_("部門"), field_name="department__uid")

    class Meta:
        model = Connector
        fields = ["name", "connection", "kind", "is_active", "is_sensitive", "scope", "department"]


CONFLICT = OpenApiResponse(description="他の変更と競合した（編集を始めた後に、別の版ができている）")


@extend_schema(
    description="DB接続の管理API（DB接続の標準のpermissionで判定する）", tags=["manage/connector/database_connection"]
)
@extend_schema_view(
    update=extend_schema(request=DatabaseConnectionUpdateSerializer, responses=DatabaseConnectionSerializer),
    partial_update=extend_schema(request=DatabaseConnectionUpdateSerializer, responses=DatabaseConnectionSerializer),
)
class DatabaseConnectionViewSet(SoftDeleteMixin, ModelViewSet):
    """社内DBの接続先と資格情報。接続先を変えるときはパスワードを入れ直す。変えると、この接続を使うConnectorの
    次の操作から効く（design.md §4.9.1）。

    操作ごとに `connector.<view|add|change|delete>_databaseconnection` で判定する（基盤管理者はすべて持つ）
    """

    permission_classes = [ModelPermissions]
    # 接続の確認と表の一覧は、資格情報を見せずに接続先へつなぐだけなので、見る権限で行える
    action_permissions = {"check": "view", "tables": "view"}
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
        connection = self.get_object()
        try:
            in_slot(connection, request, connection.connect_timeout_seconds, get_adapter(connection).check)
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
        connection = self.get_object()
        hold = connection.connect_timeout_seconds + CATALOG_SECONDS
        try:
            result = ConnectorTablesSerializer.build(
                in_slot(connection, request, hold, get_adapter(connection).list_tables)
            )
        except ConnectorError as exc:
            result = ConnectorTablesSerializer.build(None, exc.message)
        return Response(self.get_serializer(result).data)


@extend_schema(description="Connectorの管理API（permissionで判定する）", tags=["manage/connector"])
@extend_schema_view(
    update=extend_schema(request=ConnectorUpdateSerializer, responses={200: ConnectorSerializer, 409: CONFLICT}),
    partial_update=extend_schema(
        request=ConnectorUpdateSerializer, responses={200: ConnectorSerializer, 409: CONFLICT}
    ),
)
class ConnectorViewSet(SoftDeleteMixin, ModelViewSet):
    """エージェントが扱える範囲（境界）を決める（design.md §4.9.1）。部門・全社・システムのConnectorを全件扱う。

    基盤の管理なので、所属を問わず `connector.<view|add|change|delete>_connector` だけで判定する
    （authorization.md §5.2）。試し実行は接続先のデータを読むので、編集の権限で行う。

    境界（公開する表、上限、機密のフラグ）を変えて保存すると新しい版ができ、実行中のRunにも次の操作から効く。
    接続の確認と表の一覧は、使うDB接続の操作（DatabaseConnectionViewSet）で行う
    """

    permission_classes = [ModelPermissions]
    action_permissions = {
        "describe": "view",
        "query": "change",
        # 公開する表を選ぶための、接続先の表の一覧（接続先のpermissionがなくても、Connectorを編集できるように）
        "tables": "change",
        # 作成と一覧の絞り込みで選ぶ接続先・部門の一覧（名前だけ。接続先の設定は返さない）
        "connections": "view",
        "departments": "view",
    }
    lookup_field = "uid"
    queryset = Connector.objects.filter(deleted=False).select_related(
        "created_by", "current_version", "connection", "department"
    )
    serializer_class = ConnectorSerializer
    filterset_class = ConnectorFilter
    search_fields = ("name",)
    ordering = ("name", "pk")

    def get_usable_object(self) -> Connector:
        """接続先のDBにつなぐ操作（説明、試し実行）の対象。無効なConnector・接続先、版のないものでは断る"""
        connector = self.get_object()
        if not connector.in_service():
            raise Conflict(_("このConnectorか接続先が無効なため、使えません"))
        return connector

    def get_serializer_class(self):
        # 更新では使うDB接続を変えられない
        if self.action in ("update", "partial_update"):
            return ConnectorUpdateSerializer
        return super().get_serializer_class()

    def perform_create(self, serializer):
        # Connectorと最初の版は ConnectorSerializer.create() の transaction で作る
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(actor=self.request.user)

    @extend_schema(
        description="使う接続先のアカウントが読める表・ビューと列の一覧。公開する表を選ぶために使う",
        request=None,
        responses=ConnectorTablesSerializer,
    )
    @action(detail=True, methods=["get"], serializer_class=ConnectorTablesSerializer)
    def tables(self, request, *args, **kwargs):
        connection = self.get_object().connection
        hold = connection.connect_timeout_seconds + CATALOG_SECONDS
        try:
            result = ConnectorTablesSerializer.build(
                in_slot(connection, request, hold, get_adapter(connection).list_tables)
            )
        except ConnectorError as exc:
            result = ConnectorTablesSerializer.build(None, exc.message)
        return Response(self.get_serializer(result).data)

    @extend_schema(
        description="Connectorの作成と一覧の絞り込みで選べる、有効な接続先の一覧（`search` で名前を絞り込む）",
        request=None,
        responses=MinimumDatabaseConnectionSerializer(many=True),
        parameters=[OpenApiParameter("search", str, description="名前の一部")],
    )
    @action(
        detail=False,
        methods=["get"],
        serializer_class=MinimumDatabaseConnectionSerializer,
        filterset_class=None,
        search_fields=None,
    )
    def connections(self, request, *args, **kwargs):
        qs = DatabaseConnection.objects.filter(deleted=False, is_active=True).order_by("name", "pk")
        if search := request.query_params.get("search", "").strip():
            qs = qs.filter(name__icontains=search)
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    @extend_schema(
        description="部門の範囲のConnectorの作成と一覧の絞り込みで選べる部門"
        "（有効なすべての部門。所属は問わない）。"
        "`search` で名前を絞り込む",
        request=None,
        responses=MinimumDepartmentSerializer(many=True),
        parameters=[OpenApiParameter("search", str, description="名前の一部")],
    )
    @action(
        detail=False,
        methods=["get"],
        serializer_class=MinimumDepartmentSerializer,
        filterset_class=None,
        search_fields=None,
    )
    def departments(self, request, *args, **kwargs):
        qs = Department.objects.active().order_by("name", "pk")
        if search := request.query_params.get("search", "").strip():
            qs = qs.filter(name__icontains=search)
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    @extend_schema(
        description="公開している表・ビューの列と説明。エージェントの `datasource_describe` と同じ内容",
        request=None,
        responses=ConnectorTablesSerializer,
    )
    @action(detail=True, methods=["get"], serializer_class=ConnectorTablesSerializer)
    def describe(self, request, *args, **kwargs):
        connector = self.get_usable_object()
        assert connector.current_version is not None  # get_usable_object() が確かめた
        hold = connector.connection.connect_timeout_seconds + connector.current_version.timeout_seconds
        try:
            result = ConnectorTablesSerializer.build(
                in_slot(connector.connection, request, hold, get_adapter(connector).describe)
            )
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
        connector = self.get_usable_object()
        adapter = get_adapter(connector)
        sql, params = serializer.validated_data["sql"], serializer.validated_data["params"]
        assert connector.current_version is not None  # get_usable_object() が確かめた
        hold = connector.connection.connect_timeout_seconds + connector.current_version.timeout_seconds
        try:
            # 応答に載せる行だけをメモリに残す（上限までの全体を貯めない）
            result = in_slot(
                connector.connection, request, hold, lambda: adapter.query(sql, params, keep_rows=PREVIEW_ROWS)
            )
            data = ConnectorQueryResultSerializer.build(result)
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


@extend_schema(description="Connectorの版の履歴", tags=["manage/connector/version"])
class ConnectorVersionViewSet(ConnectorMixin, ReadOnlyModelViewSet):
    permission_classes = [ModelPermissions]
    permission_model = Connector
    lookup_field = "number"  # 版番号で識別する子リソースの例外（backend-conventions.md §4.1）
    queryset = ConnectorVersion.objects.select_related("created_by")
    serializer_class = ConnectorVersionSerializer
    ordering = "-number"  # Connectorの中で一意


@extend_schema(description="会話で使えるConnector（入力欄の `@` の候補）", tags=["app/connector"])
class AppConnectorViewSet(ListModelMixin, GenericViewSet):
    """依頼した人が会話で使えるConnector（部門のものは使える部門の人、全社のものは全員。authorization.md §5.2）。
    機密のものも返す（ローカルLLMのモデルの会話でだけ使える。画面で出し分ける）"""

    permission_classes = [AppUser]
    queryset = Connector.objects.select_related("connection", "current_version", "department")
    serializer_class = AppConnectorSerializer
    search_fields = ("name",)
    ordering = ("name", "pk")

    def get_queryset(self):
        return super().get_queryset().filter(pk__in=Connector.objects.usable_by(self.request.user).values("pk"))
