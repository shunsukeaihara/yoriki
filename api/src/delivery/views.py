from django.db import transaction
from django.db.models import Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet, ModelViewSet, ReadOnlyModelViewSet

from run.models import Run
from task.models import SCOPE_PERMISSIONS
from task.views import TaskMixin
from user.models import Department
from utils.permissions import AppUser, ModelPermissions
from utils.views import SoftDeleteMixin

from .files import FileRefused, fetch_file
from .models import Delivery, Destination, DestinationRevision, DestinationScope
from .outbox import enqueue_delivery
from .serializers import (
    DeliverySerializer,
    DestinationChoiceSerializer,
    DestinationRevisionSerializer,
    DestinationSecretSerializer,
    DestinationSerializer,
    DestinationStateSerializer,
    DestinationUpdateSerializer,
)

CONFLICT = OpenApiResponse(description="他の変更と競合した（編集を始めた後に、他の人が版を作った）")


@extend_schema(
    description="出口（Webhook・Zapier）の一覧・作成・変更・停止・署名の鍵の作り直し"
    "（連携の管理者。authorization.md §5.5）",
    tags=["manage/destination"],
)
@extend_schema_view(
    create=extend_schema(responses={201: DestinationSecretSerializer}),
    update=extend_schema(request=DestinationUpdateSerializer, responses={200: DestinationSerializer, 409: CONFLICT}),
    partial_update=extend_schema(
        request=DestinationUpdateSerializer, responses={200: DestinationSerializer, 409: CONFLICT}
    ),
    destroy=extend_schema(description="出口を消す。まだ送っていない配信も送らない"),
)
class DestinationViewSet(SoftDeleteMixin, ModelViewSet):
    permission_classes = [ModelPermissions]
    action_permissions = {"suspend": "change", "resume": "change", "rotate_secret": "change"}
    lookup_field = "uid"
    queryset = Destination.objects.alive().select_related("department", "current_revision", "created_by")
    serializer_class = DestinationSerializer
    filterset_fields = ["kind", "scope", "suspended"]
    search_fields = ("name",)
    ordering = ("name", "pk")

    def get_serializer_class(self):
        if self.action in ("update", "partial_update"):
            return DestinationUpdateSerializer
        return super().get_serializer_class()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        destination = serializer.save(actor=request.user)
        data = DestinationSecretSerializer(destination, context=self.get_serializer_context()).data
        return Response(data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        serializer.save(actor=self.request.user)

    @extend_schema(
        description="停止する。新しく受け付けるRunに使わず、まだ送っていない配信も送らない",
        request=None,
        responses=DestinationSerializer,
    )
    @action(detail=True, methods=["post"], serializer_class=DestinationStateSerializer)
    def suspend(self, request, *args, **kwargs):
        return self._change_state(request, DestinationSerializer, suspended=True)

    @extend_schema(description="停止を解く", request=None, responses=DestinationSerializer)
    @action(detail=True, methods=["post"], serializer_class=DestinationStateSerializer)
    def resume(self, request, *args, **kwargs):
        return self._change_state(request, DestinationSerializer, suspended=False)

    @extend_schema(
        description="署名の鍵を作り直す。次の送信から新しい鍵で署名する。新しい鍵は応答で一度だけ返す",
        request=None,
        responses=DestinationSecretSerializer,
    )
    @action(detail=True, methods=["post"], url_path="rotate_secret", serializer_class=DestinationStateSerializer)
    def rotate_secret(self, request, *args, **kwargs):
        return self._change_state(request, DestinationSecretSerializer, rotate_secret=True)

    @transaction.atomic
    def _change_state(self, request, output, **changes) -> Response:
        serializer = self.get_serializer(instance=self.get_object(), data={})
        serializer.is_valid(raise_exception=True)
        destination = serializer.save(**changes)
        return Response(output(destination, context=self.get_serializer_context()).data)


@extend_schema(description="出口の版の履歴", tags=["manage/destination/revision"])
class DestinationRevisionViewSet(ReadOnlyModelViewSet):
    permission_classes = [ModelPermissions]
    permission_model = Destination
    lookup_field = "number"
    queryset = DestinationRevision.objects.select_related("created_by")
    serializer_class = DestinationRevisionSerializer
    ordering = ("-number",)

    def get_queryset(self):
        destination = get_object_or_404(Destination.objects.alive(), uid=self.kwargs["destination"])
        return super().get_queryset().filter(destination=destination)


def choosable_condition(user) -> Q:
    """タスクの編集画面で選べる出口：利用者がタスクを管理できる範囲の出口（部門は管理できる部門のもの）"""
    condition = Q(pk__in=[])
    for scope, perm in SCOPE_PERMISSIONS.items():
        if user.has_perm(perm.perm):
            part = Q(scope=scope)
            if scope == DestinationScope.DEPARTMENT:
                part &= Q(department__in=Department.objects.manageable_by(user), department__deleted=False)
            condition |= part
    return condition


@extend_schema(
    description="タスクの編集画面で選ぶ出口の候補（停止中でなく、タスクを管理できる範囲のもの。authorization.md §7.2）",
    tags=["app/destination"],
    parameters=[
        OpenApiParameter("scope", int, description="タスクの範囲（2：部門、3：全社、4：システム）"),
        OpenApiParameter("department", str, description="部門のID（部門の範囲のとき）"),
    ],
)
class AppDestinationViewSet(mixins.ListModelMixin, GenericViewSet):
    permission_classes = [AppUser]
    queryset = Destination.objects.alive().filter(suspended=False, current_revision__isnull=False)
    serializer_class = DestinationChoiceSerializer
    search_fields = ("name",)
    ordering = ("name", "pk")

    def get_queryset(self):
        qs = (
            super()
            .get_queryset()
            .filter(choosable_condition(self.request.user))
            .select_related("department", "current_revision")
        )
        if scope := self.request.query_params.get("scope"):
            qs = qs.filter(scope=scope) if scope.isdigit() else qs.none()
        if department := self.request.query_params.get("department"):
            qs = qs.filter(department__uid=department)
        return qs


@extend_schema(
    description="タスクのRunの配信の履歴と送り直し（タスクを管理できる人。authorization.md §7.1）",
    tags=["app/task/delivery"],
    parameters=[OpenApiParameter("run", str, description="RunのID")],
)
class AppTaskDeliveryViewSet(TaskMixin, ReadOnlyModelViewSet):
    permission_classes = [AppUser]
    lookup_field = "uid"
    queryset = Delivery.objects.select_related(
        "run", "destination_revision__destination__department", "destination_revision__destination__current_revision"
    ).prefetch_related("attempts")
    serializer_class = DeliverySerializer
    filterset_fields = ["status", "event_type"]
    ordering = ("-created", "-pk")

    def get_queryset(self):
        qs = (
            super()
            .get_queryset()
            .filter(run__task=self.task, run__in=Run.objects.visible_to(self.request.user).values("pk"))
        )
        if run := self.request.query_params.get("run"):
            qs = qs.filter(run__uid=run)
        return qs

    @extend_schema(
        description="失敗・確認待ち・取消の配信を送り直す（保存した内容を送る。エージェントは動かし直さない）。"
        "送る直前に、出口が今も使えるかを確かめ直す",
        request=None,
        responses=DeliverySerializer,
    )
    @action(detail=True, methods=["post"])
    def retry(self, request, *args, **kwargs):
        delivery = self.get_object()
        with transaction.atomic():
            if problem := delivery.retry():
                raise ValidationError({"non_field_errors": [problem]})
            enqueue_delivery(delivery)
        return Response(self.get_serializer(self.get_object()).data)


@extend_schema(
    description="出口（Zapier・Webhook）へ渡した成果物を、期限付きのtokenで取得する（ログインなし。design.md §4.4）。"
    "汎用のWebhookの出口では、出口の署名の鍵を `Authorization: Bearer` で付ける。S3に保存したものは、"
    "ごく短い期限の署名付きURLへリダイレクトする",
    tags=["files"],
    responses={
        (200, "application/octet-stream"): OpenApiResponse(OpenApiTypes.BINARY, description="ファイル"),
        302: OpenApiResponse(description="保存先の署名付きURLへのリダイレクト"),
        401: None,
        404: None,
    },
)
class FileAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request: Request, token: str):
        try:
            return fetch_file(token, request)
        except FileRefused as exc:
            # 理由は取得した側に見せない（記録に残す）。鍵がないだけなら、受け手が直せるように401にする
            if exc.unauthorized:
                # 認証の仕組み（authentication_classes）を使わないので、DRFの AuthenticationFailed では403になる
                return Response(status=status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"})
            raise NotFound from None
