from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, ReadOnlyModelViewSet

from run.models import Run, RunLaunch
from run.serializers import RunSerializer
from utils.permissions import AppUser
from utils.views import SoftDeleteMixin

from .dispatch import InvalidInputs, start_task_run
from .errors import CannotRun
from .models import BUSINESS_SCOPES, TaskDefinition, TaskDefinitionRevision, TaskScope
from .serializers import (
    TaskDefinitionRevisionSerializer,
    TaskDefinitionSerializer,
    TaskDefinitionUpdateSerializer,
    TaskRunCreateSerializer,
    TaskSuspendSerializer,
)

CONFLICT = OpenApiResponse(description="他の変更と競合した（編集を始めた後に、他の人が版を作った）")


class TaskFilter(filters.FilterSet):
    name = filters.CharFilter(label=_("名前"), lookup_expr="icontains")
    scope = filters.ChoiceFilter(label=_("範囲"), choices=TaskScope.choices)
    department = filters.CharFilter(label=_("部門"), field_name="department__uid")

    class Meta:
        model = TaskDefinition
        fields = ["name", "scope", "department", "suspended"]


@extend_schema(description="タスクの一覧・作成・変更・停止・手動実行", tags=["app/task"])
@extend_schema_view(
    create=extend_schema(responses={201: TaskDefinitionSerializer}),
    update=extend_schema(
        request=TaskDefinitionUpdateSerializer, responses={200: TaskDefinitionSerializer, 409: CONFLICT}
    ),
    partial_update=extend_schema(
        request=TaskDefinitionUpdateSerializer, responses={200: TaskDefinitionSerializer, 409: CONFLICT}
    ),
    destroy=extend_schema(description="タスクを削除する。順番待ち・実行中のRunも次の区切りで止まる"),
)
class AppTaskViewSet(
    SoftDeleteMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, mixins.DestroyModelMixin, ReadOnlyModelViewSet
):
    """管理できるタスク（authorization.md §5.3）。一覧は個人・部門・全社で、システムのタスクは `scope` で指定したとき
    だけ返す（管理画面の一覧。業務画面には出さない）。定義は管理できる人だけに見せる"""

    permission_classes = [AppUser]
    lookup_field = "uid"
    queryset = TaskDefinition.objects.select_related(
        "owner", "department", "created_by", "current_revision__model_profile__current_version"
    )
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
        return super().get_serializer_class()

    def perform_create(self, serializer):
        serializer.save(actor=self.request.user)

    def perform_update(self, serializer):
        serializer.save(actor=self.request.user)

    def update(self, request, *args, **kwargs):
        response = super().update(request, *args, **kwargs)
        response.data = TaskDefinitionSerializer(self.get_object(), context=self.get_serializer_context()).data
        return response

    @extend_schema(
        description="停止する。新しいRunを受け付けず、順番待ち・実行中のRunも次の区切りで止める",
        request=None,
        responses=TaskDefinitionSerializer,
    )
    @action(detail=True, methods=["post"], serializer_class=TaskSuspendSerializer)
    def suspend(self, request, *args, **kwargs):
        return self._set_suspended(request, suspended=True)

    @extend_schema(description="停止を解く", request=None, responses=TaskDefinitionSerializer)
    @action(detail=True, methods=["post"], serializer_class=TaskSuspendSerializer)
    def resume(self, request, *args, **kwargs):
        return self._set_suspended(request, suspended=False)

    def _set_suspended(self, request, *, suspended: bool) -> Response:
        serializer = self.get_serializer(instance=self.get_object(), data={})
        serializer.is_valid(raise_exception=True)
        serializer.save(suspended=suspended)
        return Response(serializer.data)

    @extend_schema(
        description="タスクの今の版で、手動で実行する（タスクを管理できる人。authorization.md §6）。"
        "入力はタスクの定義で決めた項目だけを受け付け、自由な依頼文は受けない",
        request=TaskRunCreateSerializer,
        responses={201: RunSerializer},
    )
    @action(detail=True, methods=["post"], serializer_class=TaskRunCreateSerializer)
    def run(self, request, *args, **kwargs):
        task = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            run = start_task_run(
                task, inputs=serializer.validated_data["inputs"], actor=request.user, launch=RunLaunch.MANUAL
            )
        except InvalidInputs as exc:
            raise ValidationError({"inputs": exc.errors}) from exc
        except CannotRun as exc:
            raise ValidationError({"non_field_errors": [str(exc)]}) from exc
        return Response(RunSerializer(run, context=self.get_serializer_context()).data, status=status.HTTP_201_CREATED)


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
