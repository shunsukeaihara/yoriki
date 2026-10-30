import logging
from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models import Prefetch
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, ModelViewSet, ReadOnlyModelViewSet

from connector.serializers import AppConnectorSerializer
from run.models import Run, RunLaunch
from run.serializers import RunSerializer
from skill.models import SkillVersion
from user.models import Department
from utils.exceptions import Conflict
from utils.permissions import AppUser, ModelPermissions
from utils.views import SoftDeleteMixin

from . import checks
from .authentication import WebhookKeyAuthentication, WebhookTaskPermission
from .dispatch import InvalidInputs, receive_webhook, start_task_run
from .errors import CannotRun, VersionConflict
from .models import (
    BUSINESS_SCOPES,
    WEBHOOK_TASK_SCOPES,
    TaskAction,
    TaskDefinition,
    TaskDefinitionRevision,
    TaskScope,
    TriggerBinding,
    TriggerBindingRevision,
    TriggerKind,
    TriggerReceipt,
)
from .serializers import (
    HookErrorSerializer,
    HookReceivedSerializer,
    HookRunSerializer,
    TaskDefinitionDetailSerializer,
    TaskDefinitionRevisionSerializer,
    TaskDefinitionSerializer,
    TaskDefinitionUpdateSerializer,
    TaskRunCreateSerializer,
    TaskSkillChoiceSerializer,
    TaskSuspendSerializer,
    TriggerBindingKeySerializer,
    TriggerBindingRevisionSerializer,
    TriggerBindingSerializer,
    TriggerBindingStateSerializer,
    TriggerBindingUpdateSerializer,
    TriggerReceiptSerializer,
    TriggerTaskSerializer,
)
from .webhook import PayloadError

if TYPE_CHECKING:
    from django_stubs_ext import StrPromise

logger = logging.getLogger(__name__)

CHOICE_PARAMETERS = [
    OpenApiParameter("scope", int, required=True, description="タスクの範囲（1：個人、2：部門、3：全社、4：システム）"),
    OpenApiParameter("department", str, description="部門のID（部門の範囲のとき）"),
    OpenApiParameter("search", str, description="名前の一部"),
]

CONFLICT = OpenApiResponse(description="他の変更と競合した（編集を始めた後に、他の人が版を作った）")


class TaskFilter(filters.FilterSet):
    name = filters.CharFilter(label=_("名前"), lookup_expr="icontains")
    scope = filters.ChoiceFilter(label=_("範囲"), choices=TaskScope.choices)
    department = filters.CharFilter(label=_("部門"), field_name="department__uid")

    class Meta:
        model = TaskDefinition
        fields = ["name", "scope", "department", "suspended"]


# 手動実行のmultipart/form-data。ファイルのフィールドの名前は項目の名前なので、名前を決めずに書く
TASK_RUN_MULTIPART = {
    "type": "object",
    "properties": {
        "inputs": {
            "type": "string",
            "description": "ファイル以外の入力の項目の名前と値（JSONのオブジェクトの文字列）",
        },
    },
    "additionalProperties": {
        "type": "string",
        "format": "binary",
        "description": "型がファイルの項目。フィールドの名前は項目の名前",
    },
}


@extend_schema(description="タスクの一覧・作成・変更・停止・手動実行", tags=["app/task"])
@extend_schema_view(
    create=extend_schema(request=TaskDefinitionSerializer, responses={201: TaskDefinitionDetailSerializer}),
    update=extend_schema(
        request=TaskDefinitionUpdateSerializer, responses={200: TaskDefinitionDetailSerializer, 409: CONFLICT}
    ),
    partial_update=extend_schema(
        request=TaskDefinitionUpdateSerializer, responses={200: TaskDefinitionDetailSerializer, 409: CONFLICT}
    ),
    destroy=extend_schema(description="タスクを削除する。順番待ち・実行中のRunも次の区切りで止まる"),
)
class AppTaskViewSet(mixins.CreateModelMixin, mixins.UpdateModelMixin, mixins.DestroyModelMixin, ReadOnlyModelViewSet):
    """管理できるタスク（authorization.md §5.3）。一覧は個人・部門・全社で、システムのタスクは `scope` で指定したとき
    だけ返す（管理画面の一覧。業務画面には出さない）。定義は管理できる人だけに見せる"""

    permission_classes = [AppUser]
    lookup_field = "uid"
    queryset = TaskDefinition.objects.select_related(
        "owner", "department", "created_by", "current_revision__model_profile__current_version"
    ).prefetch_related("triggers")
    serializer_class = TaskDefinitionSerializer
    filterset_class = TaskFilter
    search_fields = ("name",)
    ordering = ("name", "pk")

    def get_queryset(self):
        qs = super().get_queryset().filter(pk__in=TaskDefinition.objects.manageable_by(self.request.user).values("pk"))
        if self.action == "list" and self.request.query_params.get("scope") != str(TaskScope.SYSTEM):
            qs = qs.filter(scope__in=BUSINESS_SCOPES)
        return qs

    def get_serializer_class(self):
        if self.action in ("update", "partial_update"):
            return TaskDefinitionUpdateSerializer
        if self.action in ("retrieve", "create"):
            return TaskDefinitionDetailSerializer
        return super().get_serializer_class()

    def perform_destroy(self, instance):
        instance.soft_delete(actor=self.request.user)

    def perform_create(self, serializer):
        serializer.save(actor=self.request.user)

    def perform_update(self, serializer):
        serializer.save(actor=self.request.user)

    @extend_schema(
        description="停止する。新しいRunを受け付けず、順番待ち・実行中のRunも次の区切りで止める",
        request=None,
        responses=TaskDefinitionDetailSerializer,
    )
    @action(detail=True, methods=["post"], serializer_class=TaskSuspendSerializer)
    def suspend(self, request, *args, **kwargs):
        return self._set_suspended(request, suspended=True)

    @extend_schema(description="停止を解く", request=None, responses=TaskDefinitionDetailSerializer)
    @action(detail=True, methods=["post"], serializer_class=TaskSuspendSerializer)
    def resume(self, request, *args, **kwargs):
        return self._set_suspended(request, suspended=False)

    def _set_suspended(self, request, *, suspended: bool) -> Response:
        serializer = self.get_serializer(instance=self.get_object(), data={})
        serializer.is_valid(raise_exception=True)
        serializer.save(suspended=suspended, actor=request.user)
        return Response(serializer.data)

    @extend_schema(
        description="タスクの今の版で、手動で実行する（タスクを管理できる人。authorization.md §6）。"
        "入力はタスクの定義で決めた項目だけを受け付け、自由な依頼文は受けない。型がファイルの項目があれば "
        "multipart/form-dataで送る（ファイルは項目の名前で付ける）",
        request={"application/json": TaskRunCreateSerializer, "multipart/form-data": TASK_RUN_MULTIPART},
        parameters=[
            OpenApiParameter(
                "version",
                int,
                description="画面で確かめた版（任意）。今の版と違えば409にし、確かめていない定義では実行しない。"
                "入力の項目の名前と重ならないよう、本文ではなくクエリで送る",
            )
        ],
        responses={201: RunSerializer, 409: CONFLICT},
    )
    @action(detail=True, methods=["post"], serializer_class=TaskRunCreateSerializer)
    def run(self, request, *args, **kwargs):
        task = self.get_object()
        version = request.query_params.get("version")
        if version is not None and not version.isdigit():
            raise ValidationError({"version": [_("版の番号を指定してください")]})
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            run = start_task_run(
                task,
                inputs=serializer.validated_data["inputs"],
                actor=request.user,
                launch=RunLaunch.MANUAL,
                expected_version=int(version) if version is not None else None,
            )
        except VersionConflict as exc:
            message = _("画面を開いた後に、タスクの版が変わりました。最新の状態を読み込んでから実行してください")
            raise Conflict(message) from exc
        except InvalidInputs as exc:
            raise ValidationError({"inputs": exc.errors}) from exc
        except CannotRun as exc:
            raise ValidationError({"non_field_errors": [str(exc)]}) from exc
        return Response(RunSerializer(run, context=self.get_serializer_context()).data, status=status.HTTP_201_CREATED)

    def _choice_task(self) -> TaskDefinition:
        """候補を求める範囲（クエリの scope・department）の、保存していないタスク。その範囲のタスクを管理できなければ
        403（作成の画面と、管理できるタスクの編集の画面だけで使う）"""
        params = self.request.query_params
        try:
            scope = TaskScope(int(params.get("scope", "")))
        except ValueError:
            raise ValidationError({"scope": [_("範囲を指定してください")]}) from None
        user = self.request.user
        department = None
        if scope == TaskScope.DEPARTMENT:
            department = Department.objects.active().filter(uid=params.get("department", "")).first()
            if department is None:
                raise ValidationError({"department": [_("部門を指定してください")]})
        owner = user if scope == TaskScope.PERSONAL else None
        task = TaskDefinition(scope=scope, owner=owner, department=department)  # type: ignore[misc]
        if not task.can(user, TaskAction.MANAGE):
            raise PermissionDenied(_("この範囲のタスクを管理する権限がありません"))
        return task

    @extend_schema(
        description="タスクの編集画面で選ぶSkillの候補（その範囲のタスクに登録できる、公開中で停止していないもの。"
        "authorization.md §5.4・§7.2）",
        parameters=CHOICE_PARAMETERS,
        responses=TaskSkillChoiceSerializer(many=True),
    )
    @action(detail=False, methods=["get"], url_path="skill_choices", serializer_class=TaskSkillChoiceSerializer)
    def skill_choices(self, request, *args, **kwargs):
        qs = (
            checks.allowed_skills(self._choice_task())
            .select_related("department", "current_version")
            .prefetch_related(Prefetch("versions", queryset=SkillVersion.objects.only("skill_id", "number")))
            .order_by("name", "pk")
        )
        return self._choices(request, qs)

    @extend_schema(
        description="タスクの編集画面で選ぶConnectorの候補（その範囲のタスクに登録できる、有効なもの。"
        "authorization.md §5.4・§7.2）",
        parameters=CHOICE_PARAMETERS,
        responses=AppConnectorSerializer(many=True),
    )
    @action(detail=False, methods=["get"], url_path="connector_choices", serializer_class=AppConnectorSerializer)
    def connector_choices(self, request, *args, **kwargs):
        qs = (
            checks.allowed_connectors(self._choice_task())
            .select_related("department", "current_version", "connection")
            .order_by("name", "pk")
        )
        return self._choices(request, qs)

    def _choices(self, request, qs) -> Response:
        if name := request.query_params.get("search"):
            qs = qs.filter(name__icontains=name)
        page = self.paginate_queryset(qs)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)


class TaskMixin:
    """URLのtaskを、利用者が管理できるタスクに限定して取得する"""

    kwargs: dict[str, str]

    @property
    def task(self) -> TaskDefinition:
        if not hasattr(self, "_task"):
            user = self.request.user  # type: ignore[attr-defined]
            self._task = get_object_or_404(TaskDefinition.objects.manageable_by(user), uid=self.kwargs["task"])
        return self._task


@extend_schema(description="タスクの版の履歴", tags=["app/task/revision"])
class AppTaskRevisionViewSet(TaskMixin, ReadOnlyModelViewSet):
    permission_classes = [AppUser]
    lookup_field = "number"
    queryset = TaskDefinitionRevision.objects.select_related("model_profile", "created_by")
    serializer_class = TaskDefinitionRevisionSerializer
    ordering = ("-number",)

    def get_queryset(self):
        return super().get_queryset().filter(task=self.task)


@extend_schema(
    description="タスクのRunの履歴（結果とログを見られるもの。authorization.md §7.1）", tags=["app/task/run"]
)
class AppTaskRunViewSet(TaskMixin, mixins.ListModelMixin, GenericViewSet):
    permission_classes = [AppUser]
    queryset = Run.objects.select_related("conversation", "model_version__profile")
    serializer_class = RunSerializer
    filterset_fields = ["status"]
    ordering = ("-created", "-pk")

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(task=self.task)
            .filter(pk__in=Run.objects.visible_to(self.request.user).values("pk"))
        )


@extend_schema(
    description="タスクの入口（Webhook）の一覧・作成・変更・停止・鍵の作り直し（連携の管理者。authorization.md §5.5）",
    tags=["manage/trigger_binding"],
)
@extend_schema_view(
    create=extend_schema(responses={201: TriggerBindingKeySerializer}),
    update=extend_schema(
        request=TriggerBindingUpdateSerializer, responses={200: TriggerBindingSerializer, 409: CONFLICT}
    ),
    partial_update=extend_schema(
        request=TriggerBindingUpdateSerializer, responses={200: TriggerBindingSerializer, 409: CONFLICT}
    ),
    destroy=extend_schema(description="入口を消す。鍵はすぐに使えなくなる（受け付けたRunは止めない）"),
)
class TriggerBindingViewSet(SoftDeleteMixin, ModelViewSet):
    permission_classes = [ModelPermissions]
    action_permissions = {"suspend": "change", "resume": "change", "rotate_key": "change", "tasks": "add"}
    lookup_field = "uid"
    queryset = TriggerBinding.objects.alive().select_related(
        "task__department", "task__current_revision", "current_revision", "created_by"
    )
    serializer_class = TriggerBindingSerializer
    filterset_fields = ["suspended"]
    search_fields = ("name", "task__name")
    ordering = ("name", "pk")

    def get_serializer_class(self):
        if self.action in ("update", "partial_update"):
            return TriggerBindingUpdateSerializer
        return super().get_serializer_class()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        binding = serializer.save(actor=request.user)
        data = TriggerBindingKeySerializer(binding, context=self.get_serializer_context()).data
        return Response(data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        serializer.save(actor=self.request.user)

    @extend_schema(description="停止する。新しい要求を受け付けない", request=None, responses=TriggerBindingSerializer)
    @action(detail=True, methods=["post"], serializer_class=TriggerBindingStateSerializer)
    def suspend(self, request, *args, **kwargs):
        return self._change_state(request, TriggerBindingSerializer, suspended=True)

    @extend_schema(description="停止を解く", request=None, responses=TriggerBindingSerializer)
    @action(detail=True, methods=["post"], serializer_class=TriggerBindingStateSerializer)
    def resume(self, request, *args, **kwargs):
        return self._change_state(request, TriggerBindingSerializer, suspended=False)

    @extend_schema(
        description="鍵を作り直す。前の鍵はすぐに使えなくなる。新しい鍵は応答で一度だけ返す",
        request=None,
        responses=TriggerBindingKeySerializer,
    )
    @action(detail=True, methods=["post"], url_path="rotate_key", serializer_class=TriggerBindingStateSerializer)
    def rotate_key(self, request, *args, **kwargs):
        return self._change_state(request, TriggerBindingKeySerializer, rotate_key=True)

    @transaction.atomic
    def _change_state(self, request, output, **changes) -> Response:
        serializer = self.get_serializer(instance=self.get_object(), data={})
        serializer.is_valid(raise_exception=True)
        binding = serializer.save(**changes)
        return Response(output(binding, context=self.get_serializer_context()).data)

    @extend_schema(
        description="入口を付けられるタスク（部門・全社・システム。まだWebhookの入口がないもの）",
        responses=TriggerTaskSerializer(many=True),
    )
    @action(detail=False, methods=["get"], serializer_class=TriggerTaskSerializer)
    def tasks(self, request, *args, **kwargs):
        taken = TriggerBinding.objects.alive().filter(kind=TriggerKind.WEBHOOK).values("task")
        qs = (
            TaskDefinition.objects.filter(deleted=False, scope__in=WEBHOOK_TASK_SCOPES)
            .exclude(pk__in=taken)
            .select_related("department", "current_revision")
            .order_by("name", "pk")
        )
        if name := request.query_params.get("search"):
            qs = qs.filter(name__icontains=name)
        page = self.paginate_queryset(qs)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)


class TriggerBindingMixin:
    kwargs: dict[str, str]

    @property
    def binding(self) -> TriggerBinding:
        if not hasattr(self, "_binding"):
            self._binding = get_object_or_404(TriggerBinding.objects.alive(), uid=self.kwargs["binding"])
        return self._binding


@extend_schema(description="入口の受け取り方の版の履歴", tags=["manage/trigger_binding/revision"])
class TriggerBindingRevisionViewSet(TriggerBindingMixin, ReadOnlyModelViewSet):
    permission_classes = [ModelPermissions]
    permission_model = TriggerBinding
    lookup_field = "number"
    queryset = TriggerBindingRevision.objects.select_related("created_by")
    serializer_class = TriggerBindingRevisionSerializer
    ordering = ("-number",)

    def get_queryset(self):
        return super().get_queryset().filter(binding=self.binding)


@extend_schema(
    description="入口の受付の履歴（Runの状態まで。結果・ログはタスクの画面で、タスクを管理できる人が見る）",
    tags=["manage/trigger_binding/receipt"],
)
class TriggerReceiptViewSet(TriggerBindingMixin, mixins.ListModelMixin, GenericViewSet):
    permission_classes = [ModelPermissions]
    permission_model = TriggerBinding
    queryset = TriggerReceipt.objects.select_related("binding_revision", "run")
    serializer_class = TriggerReceiptSerializer
    ordering = ("-created", "-pk")

    def get_queryset(self):
        return super().get_queryset().filter(binding=self.binding)


def hook_error(
    code: str, detail: "str | StrPromise", http_status: int, errors: dict[str, str] | None = None
) -> Response:
    body: dict[str, object] = {"code": code, "detail": str(detail)}
    if errors:
        body["inputs"] = errors
    return Response(body, status=http_status)


HOOK_ERRORS = {
    400: OpenApiResponse(HookErrorSerializer, description="payloadの形・項目の誤り（Runは作らない）"),
    401: OpenApiResponse(description="鍵がないか、正しくない"),
    403: OpenApiResponse(HookErrorSerializer, description="入口が停止中"),
    409: OpenApiResponse(
        HookErrorSerializer, description="同じイベントIDで違う内容を受け取った、またはタスクを今は実行できない"
    ),
    413: OpenApiResponse(HookErrorSerializer, description="payloadが入口の上限より大きい"),
    415: OpenApiResponse(HookErrorSerializer, description="受け付けない形式"),
}


@extend_schema(
    description="Webhookでタスクを起動する（design.md §4.2）。受け付けたら202とrun_idを返し、作業の完了を待たない。"
    "同じイベントID（入口で指定した場所・Idempotency-Key ヘッダー・推奨の形式の event_id）の再送には、前のRunを"
    "200で返す。ファイルは multipart/form-data で付ける（URLの取得はしない）",
    tags=["hooks/task"],
    request={
        "application/json": OpenApiTypes.OBJECT,
        "application/x-www-form-urlencoded": OpenApiTypes.OBJECT,
        "multipart/form-data": OpenApiTypes.OBJECT,
        "text/plain": OpenApiTypes.STR,
    },
    parameters=[OpenApiParameter("Idempotency-Key", str, OpenApiParameter.HEADER, required=False)],
    responses={202: HookReceivedSerializer, 200: HookReceivedSerializer, **HOOK_ERRORS},
)
class HookTaskViewSet(GenericViewSet):
    authentication_classes = [WebhookKeyAuthentication]
    permission_classes = [WebhookTaskPermission]
    serializer_class = HookReceivedSerializer

    def create(self, request, *args, **kwargs):
        binding: TriggerBinding = request.auth
        revision = binding.current_revision
        if not binding.in_service() or revision is None:
            return hook_error("trigger_suspended", _("この入口は停止中です"), status.HTTP_403_FORBIDDEN)
        limit = revision.max_payload_bytes
        body = self._read_body(request, limit)
        if body is None:
            return hook_error(
                "payload_too_large",
                _("payloadが大きすぎます（上限 %(limit)s バイト）") % {"limit": limit},
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        try:
            run, created = receive_webhook(
                binding,
                body=body,
                content_type=request.META.get("CONTENT_TYPE", ""),
                idempotency_key=request.headers.get("Idempotency-Key"),
            )
        except PayloadError as exc:
            return hook_error(exc.code, exc.detail, exc.status, exc.errors)
        except InvalidInputs as exc:
            return hook_error("invalid_inputs", str(exc), status.HTTP_400_BAD_REQUEST, exc.errors)
        except CannotRun as exc:
            # 理由にはタスクの中身（Skill・Connectorの名前等）が入るので、送り元には返さずログに残す
            logger.warning("webhook %s cannot start task %s: %s", binding.uid, binding.task.uid, exc)
            return hook_error("task_unavailable", _("タスクを今は実行できません"), status.HTTP_409_CONFLICT)
        data = {**HookRunSerializer(run).data, "duplicate": not created}
        return Response(data, status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK)

    @staticmethod
    def _read_body(request, limit: int) -> bytes | None:
        """本文を上限まで読む。上限を超えたら None。

        request.body は DATA_UPLOAD_MAX_MEMORY_SIZE を超えると断るので、入口の上限まで自分で読む"""
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        if length > limit:
            return None
        body = request._request.read(limit + 1)
        return None if len(body) > limit else body


@extend_schema(
    description="Webhookで起動したRunの状態（authorization.md §7.1）。状態と決まった失敗のコードだけを返す",
    tags=["hooks/task/run"],
)
class HookTaskRunViewSet(mixins.RetrieveModelMixin, GenericViewSet):
    authentication_classes = [WebhookKeyAuthentication]
    permission_classes = [WebhookTaskPermission]
    lookup_field = "uid"
    lookup_url_kwarg = "run_id"
    queryset = Run.objects.all()
    serializer_class = HookRunSerializer

    def get_queryset(self):
        # この入口が受け付けたRunだけ
        return super().get_queryset().filter(trigger_receipt__binding=self.request.auth)
