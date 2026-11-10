from django.db.models import Prefetch, Q
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.mixins import CreateModelMixin, ListModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, ModelViewSet

from artifact.models import Artifact
from run.models import Run, RunTrigger
from utils.permissions import AppUser

from .models import OWNED_PURPOSES, Conversation, ConversationMessage
from .serializers import ConversationSerializer, MessageSerializer


class MyConversationMixin:
    """URLのconversationを、ログインユーザーが所有する会話に限定して取得する"""

    kwargs: dict[str, str]

    @property
    def conversation(self) -> Conversation:
        if not hasattr(self, "_conversation"):
            self._conversation = get_object_or_404(
                Conversation.objects.owned_by(self.request.user),  # type: ignore[attr-defined]
                uid=self.kwargs["conversation"],
            )
        return self._conversation

    def get_queryset(self):
        return super().get_queryset().filter(conversation=self.conversation)  # type: ignore[misc]


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter(
                "archived", bool, description="trueならアーカイブした会話だけ。既定はアーカイブしていない会話"
            )
        ]
    )
)
@extend_schema(description="自分の会話に関するAPI", tags=["app/conversation"])
class AppConversationViewSet(ModelViewSet):
    permission_classes = [AppUser]
    lookup_field = "uid"
    queryset = Conversation.objects.filter(deleted=False).select_related(
        "model_profile__current_version__connection", "task__department"
    )
    serializer_class = ConversationSerializer
    search_fields = ["title"]
    ordering = ("-last_posted", "-pk")
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]  # PUTは使わない

    def get_queryset(self):
        qs = super().get_queryset().filter(owner=self.request.user, purpose__in=OWNED_PURPOSES)
        if self.action == "list":
            # アーカイブした会話は、会話の一覧から外してアーカイブの一覧に出す（design.md §8）
            qs = qs.filter(archived__isnull=self.request.query_params.get("archived") != "true")
        return qs

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    def perform_update(self, serializer):
        # 実行中のRunへの切り替えは ConversationSerializer.update() の transaction で行う
        serializer.save(actor=self.request.user)

    def perform_destroy(self, instance: Conversation):
        # 論理削除。削除した会話のRunは画面から止められなくなるので、動いているRunは止める（design.md §8）
        instance.close(delete=True, actor=self.request.user)

    @extend_schema(
        description="アーカイブする。会話の一覧から外し、動いている作業を止めて、作業ファイルを消す。履歴・添付・成果物は"
        "残る。発言するとアーカイブを解除して再開する",
        request=None,
        responses=ConversationSerializer,
    )
    @action(detail=True, methods=["post"])
    def archive(self, request, *args, **kwargs):
        conversation = self.get_object()
        if conversation.archived is None:
            conversation.close(delete=False, actor=request.user)
        return Response(self.get_serializer(conversation).data)


@extend_schema(description="会話のメッセージに関するAPI", tags=["app/conversation/message"])
class AppConversationMessageViewSet(MyConversationMixin, ListModelMixin, CreateModelMixin, GenericViewSet):
    permission_classes = [AppUser]
    queryset = ConversationMessage.objects.select_related(
        "run", "sender", "instruction", "task_run__task", "task_run__conversation"
    ).prefetch_related(
        Prefetch("attachments", queryset=Artifact.objects.select_related("blob", "run").order_by("pk")),
    )
    serializer_class = MessageSerializer
    ordering = "-pk"

    def get_queryset(self):
        qs = super().get_queryset()
        if self.conversation.is_task_fix:
            # 修正チャットのタスクのRunのメッセージ（最初の依頼、追加の指示、応答）は、そのRunのログを見られる人
            # （実行したときの範囲を管理できる人。authorization.md §7.1）にだけ見せる。Runの画面と同じ判定
            visible = Run.objects.visible_to(self.request.user).values("pk")
            qs = qs.filter(Q(run__isnull=True) | ~Q(run__trigger=RunTrigger.TASK) | Q(run__in=visible))
        return qs

    def perform_create(self, serializer):
        # 受付の処理（Runの開始、追加指示、回答）は post_message() の transaction で行う
        serializer.save(conversation=self.conversation, sender=self.request.user)
