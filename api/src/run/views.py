from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import get_object_or_404
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, ReadOnlyModelViewSet

from stream.serializers import RunEventSchema, RunEventSerializer
from utils.permissions import AppUser

from .models import SCRIPT_CALL_PREFIX, ClarificationRequest, Run, RunEvent, ToolInvocation
from .serializers import (
    ClarificationAnswerSerializer,
    ClarificationRequestSerializer,
    RunCancelSerializer,
    RunSerializer,
    ScriptCallSerializer,
)

CONFLICT = OpenApiResponse(description="他の処理と競合した")


def my_runs(user):
    """ログインユーザーが結果とログを見られるRun（自分の会話のRunと、管理できるタスクのRun。authorization.md §7.1）。
    SSEのストリームの権限の判定にも使う（realtime.md §4）"""
    return Run.objects.visible_to(user)


class MyRunMixin:
    """URLのrunを、ログインユーザーが見られるRunに限定して取得する"""

    kwargs: dict[str, str]

    @cached_property
    def run(self) -> Run:
        return get_object_or_404(my_runs(self.request.user), uid=self.kwargs["run"])  # type: ignore[attr-defined]

    def get_queryset(self):
        return super().get_queryset().filter(run=self.run)  # type: ignore[misc]


class RunFilter(filters.FilterSet):
    conversation = filters.CharFilter(label=_("会話"), field_name="conversation__uid")

    class Meta:
        model = Run
        fields = ["status", "conversation"]


@extend_schema(description="自分のRunに関するAPI", tags=["app/run"])
class AppRunViewSet(ReadOnlyModelViewSet):
    permission_classes = [AppUser]
    lookup_field = "uid"
    queryset = Run.objects.select_related("conversation", "model_version__profile", "task_revision__destination")
    serializer_class = RunSerializer
    filterset_class = RunFilter
    ordering = ("-created", "-pk")

    def get_queryset(self):
        return super().get_queryset().filter(pk__in=my_runs(self.request.user).values("pk"))

    @extend_schema(
        description="実行の停止を依頼する。実行中の場合は停止中になり、Workerが停止を確かめると停止済みになる",
        request=RunCancelSerializer,
        responses={200: RunSerializer, 409: CONFLICT},
    )
    @action(detail=True, methods=["post"], serializer_class=RunCancelSerializer)
    def cancel(self, request, *args, **kwargs):
        serializer = self.get_serializer(instance=self.get_object(), data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(actor=request.user)
        return Response(serializer.data)


@extend_schema(description="Runのイベントの履歴", tags=["app/run/event"])
@extend_schema_view(list=extend_schema(responses=RunEventSchema))
class AppRunEventViewSet(MyRunMixin, ListModelMixin, GenericViewSet):
    permission_classes = [AppUser]
    queryset = RunEvent.objects.all()
    serializer_class = RunEventSerializer
    ordering = "sequence"  # Runの中で一意

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "run_uid": self.run.uid}


@extend_schema(description="Skillのスクリプトからの呼出し（Connector）の記録", tags=["app/run/script_call"])
class AppScriptCallViewSet(MyRunMixin, ListModelMixin, GenericViewSet):
    """作業の記録と同じく、Runの結果とログを見られる人だけ（チャットから起動した人には出さない。
    authorization.md §7.1）。呼出しはモデルの文脈（RunEvent）に入れないので、作業の記録のAPIとは別に返す"""

    permission_classes = [AppUser]
    queryset = ToolInvocation.objects.filter(call_id__startswith=SCRIPT_CALL_PREFIX)
    serializer_class = ScriptCallSerializer
    ordering = ("created", "pk")


@extend_schema(description="エージェントからの確認", tags=["app/run/clarification"])
class AppClarificationViewSet(MyRunMixin, ListModelMixin, RetrieveModelMixin, GenericViewSet):
    permission_classes = [AppUser]
    lookup_field = "uid"
    queryset = ClarificationRequest.objects.select_related("run", "answered_by")
    serializer_class = ClarificationRequestSerializer
    filterset_fields = ["status"]
    ordering = ("-created", "-pk")

    @extend_schema(
        description="確認に回答する。回答を受け付けるとRunが再開される",
        request=ClarificationAnswerSerializer,
        responses={200: ClarificationRequestSerializer, 409: OpenApiResponse(description="すでに回答済み")},
    )
    @action(detail=True, methods=["post"], serializer_class=ClarificationAnswerSerializer)
    def answer(self, request, *args, **kwargs):
        run = self.run
        if run.is_task_fix:
            # 修正チャットの確認に答えるのは、修正チャットの持ち主で、今もタスクを管理できる人だけ（回答の自由記述は
            # 指示になるため。design.md §11.11）。ほかのタスクの管理者は、停止はできるが答えない
            from task.checks import fixer_problem  # noqa: PLC0415  taskのModelがrunを参照するため

            if run.conversation.owner_id != request.user.pk or fixer_problem(request.user, run.task):
                raise PermissionDenied(_("修正チャットの確認に答えられるのは、修正チャットを始めた人だけです"))
        serializer = self.get_serializer(instance=self.get_object(), data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(actor=request.user)
        return Response(serializer.data)
