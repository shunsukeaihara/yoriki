from django.db import transaction
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework.generics import get_object_or_404
from rest_framework.mixins import CreateModelMixin, ListModelMixin
from rest_framework.viewsets import GenericViewSet, ModelViewSet

from run.models import CANCELLABLE_STATUSES, Run
from utils.permissions import AppUser
from utils.views import SoftDeleteMixin

from .models import Conversation, ConversationMessage
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


@extend_schema(description="自分の会話に関するAPI", tags=["app/conversation"])
class AppConversationViewSet(SoftDeleteMixin, ModelViewSet):
    permission_classes = [AppUser]
    lookup_field = "uid"
    queryset = Conversation.objects.filter(deleted=False).select_related("model_profile__current_version__connection")
    serializer_class = ConversationSerializer
    search_fields = ["title"]
    ordering = ("-last_posted", "-pk")
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]  # PUTは使わない

    def get_queryset(self):
        return super().get_queryset().filter(owner=self.request.user)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    def perform_update(self, serializer):
        # 実行中のRunへの切り替えは ConversationSerializer.update() の transaction で行う
        serializer.save(actor=self.request.user)

    @transaction.atomic
    def perform_destroy(self, instance: Conversation):
        # 削除した会話のRunは画面から止められなくなるので、実行中のRunは先に止める
        for run in Run.objects.filter(conversation=instance, status__in=CANCELLABLE_STATUSES):
            run.request_cancel(str(_("会話を削除しました")), actor=self.request.user)
        super().perform_destroy(instance)


@extend_schema(description="会話のメッセージに関するAPI", tags=["app/conversation/message"])
class AppConversationMessageViewSet(MyConversationMixin, ListModelMixin, CreateModelMixin, GenericViewSet):
    permission_classes = [AppUser]
    queryset = ConversationMessage.objects.select_related("run", "sender", "instruction")
    serializer_class = MessageSerializer
    ordering = "-pk"

    def perform_create(self, serializer):
        # 受付の処理（Runの開始、追加指示、回答）は post_message() の transaction で行う
        serializer.save(conversation=self.conversation, sender=self.request.user)
