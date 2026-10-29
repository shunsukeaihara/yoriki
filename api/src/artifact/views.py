from django.db.models import Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.decorators import action
from rest_framework.mixins import CreateModelMixin, ListModelMixin, RetrieveModelMixin
from rest_framework.parsers import MultiPartParser
from rest_framework.viewsets import GenericViewSet

from conversation.views import MyConversationMixin
from run.views import MyRunMixin
from utils.permissions import AppUser

from .download import file_response
from .models import Artifact, ArtifactKind
from .serializers import ArtifactSerializer

FILE_RESPONSE = {(200, "application/octet-stream"): OpenApiResponse(OpenApiTypes.BINARY, description="ファイル")}


@extend_schema(description="会話の添付と成果物に関するAPI", tags=["app/conversation/artifact"])
class AppConversationArtifactViewSet(
    MyConversationMixin, ListModelMixin, RetrieveModelMixin, CreateModelMixin, GenericViewSet
):
    permission_classes = [AppUser]
    lookup_field = "uid"
    queryset = Artifact.objects.filter(deleted=False).select_related("blob", "run")
    serializer_class = ArtifactSerializer
    parser_classes = [MultiPartParser]
    filterset_fields = ["kind"]
    ordering = ("-created", "-pk")

    def get_queryset(self):
        # 送る前の添付（/workspace/input の中の名前がまだないもの）は、入力欄にだけある
        return super().get_queryset().filter(owner=self.request.user).filter(Q(kind=ArtifactKind.OUTPUT) | ~Q(path=""))

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user, conversation=self.conversation)

    @extend_schema(description="ファイルを取得する（ダウンロードとして返す）", request=None, responses=FILE_RESPONSE)
    @action(detail=True, methods=["get"], serializer_class=None)
    def download(self, request, *args, **kwargs):
        return file_response(self.get_object())


@extend_schema(
    description="Runの添付と成果物（Runの結果とログを見られる人。authorization.md §7.1）", tags=["app/run/artifact"]
)
class AppRunArtifactViewSet(MyRunMixin, ListModelMixin, RetrieveModelMixin, GenericViewSet):
    """タスクのRunの成果物は、実行したときのタスクの範囲を管理できる人が取得する（タスクのRunの会話は会話のAPIに
    出さない）。取得のたびにRunを見られるかを判定し直す"""

    permission_classes = [AppUser]
    lookup_field = "uid"
    queryset = Artifact.objects.filter(deleted=False).select_related("blob", "run")
    serializer_class = ArtifactSerializer
    filterset_fields = ["kind"]
    ordering = ("-created", "-pk")

    def get_queryset(self):
        return super().get_queryset().filter(Q(kind=ArtifactKind.OUTPUT) | ~Q(path=""))

    @extend_schema(description="ファイルを取得する（ダウンロードとして返す）", request=None, responses=FILE_RESPONSE)
    @action(detail=True, methods=["get"], serializer_class=None)
    def download(self, request, *args, **kwargs):
        return file_response(self.get_object())
