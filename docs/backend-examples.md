# バックエンド実装例（Model / Serializer / ViewSet）

[backend-conventions.md](backend-conventions.md) の規約に沿った典型的な実装例。新しいアプリを書くときは、まず最も近い例を探し、同じ形で書く。

| 例 | パターン | 主な見どころ |
|---|---|---|
| [例1](#例1-可変マスターのcrud通知先) | 可変マスターのCRUD | 論理削除、FilterSet、管理者向けと通常社員向けの2系統、Djangoのpermissionによる権限、部分更新での項目間検証 |
| [例2](#例2-親子リソース会話とメッセージ) | 親子リソース | URLの親をMixinで解決、関連の選択肢をログインユーザーで制限、入力の正規化 |
| [例3](#例3-システムが進める状態と利用者からの依頼run) | システムが進める状態と利用者の依頼 | 停止・回答をactionとSerializerで受ける、遷移表と行ロック、Worker側のclaim、子リソースのViewSet |
| [例4](#例4-不変の版を公開するskillとskillversion) | 不変の版の公開 | 楽観的な競合検出、409応答、読み取り専用の版ViewSetと管理画面 |

例は説明のために項目を絞っている。実際の項目は design.md §5.1 に従う（例1の `DestinationRevision` 等は省略している）。

---

## 前提とする共通部品

例の中で使う `utils` の部品。汎用の部品と、Yorikiで追加するものがある（backend-conventions.md §6.3）。

```python
# utils/fields.py（抜粋）
class NormalizedTextField(serializers.CharField):
    """改行コードをLFに揃え、行末の空白を除く"""

    def to_internal_value(self, data) -> str:
        value = super().to_internal_value(data)
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        return "\n".join(line.rstrip() for line in value.split("\n"))
```

```python
# utils/exceptions.py（抜粋）
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import APIException


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = _("他の更新と競合しました。最新の状態を取得してからやり直してください。")
    default_code = "conflict"
```

```python
# utils/permissions.py（抜粋）
from rest_framework.permissions import BasePermission, IsAuthenticated, OperandHolder


class AppPermission(Enum):
    """Yorikiで定義するpermission。値は（app_label、codename、表示名）"""

    EDIT_DEPARTMENT_SKILLS = "skill", "can_edit_department_skills", _("所属する部門のSkillを編集できる")
    PUBLISH_COMPANY_SKILLS = "skill", "can_publish_company_skills", _("全社のSkillを公開できる")
    ...  # perm（"<app_label>.<codename>"）、meta（Meta.permissionsに書く組）のproperty


AppUser = IsAuthenticated & _AppUser  # ログインしている社員
ModelPermissions = IsAuthenticated & _ModelPermission  # 管理のModelの標準のpermission（view・add・change・delete）


class DenyAll(BasePermission):
    """DEFAULT_PERMISSION_CLASSES。permission_classesを書き忘れたViewSetは誰も使えない"""

    def has_permission(self, request, view) -> bool:
        return False


def require_permission(perm: AppPermission | str) -> OperandHolder:
    """Djangoのpermission（"<app_label>.<codename>"）を持つログインユーザーだけを許可する"""

    class _HasPermission(BasePermission):
        def has_permission(self, request, view) -> bool:
            return request.user.has_perm(to_perm_str(perm))

    return IsAuthenticated & _HasPermission
```

```python
# user/migrations/00xx_initial_groups.py（抜粋）。初期のGroupを最初に一度だけ作る（authorization.md §11）
INITIAL_GROUPS = {
    "部門のSkill編集者": ["skill.can_edit_department_skills"],
    "全社のSkill公開者": ["skill.can_edit_company_skills", "skill.can_publish_company_skills"],
}
```

`utils.views.SoftDeleteMixin`、`utils.serializers.ReadOnlyModelSerializer`、`utils.serializers.CountSerializer` は汎用の部品。`User` は `uid` を持つ。

スキーマのtagは、利用者ごとに `app/<リソース>`（通常社員）、`manage/<リソース>`（管理者）とする（backend-conventions.md §4.4）。

URLは各アプリの `urls.py` では `/api/` を付けずに登録し、`app/urls.py` で `/api/` の下に取り込む（backend-conventions.md §5）。テストでは `/api/` から始まるURLで呼ぶ。

---

## 例1 可変マスターのCRUD（通知先）

通知先の管理権限を持つ利用者が通知先を登録・編集・削除し、通常社員は有効な通知先の一覧だけを見られる。

### delivery/models.py

```python
from auditlog.registry import auditlog
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from utils.fields import NanoidField
class DestinationKind(models.IntegerChoices):
    WEBHOOK = 1, _("Webhook")
    SLACK = 2, _("Slack")
    TEAMS = 3, _("Teams")
    DISCORD = 4, _("Discord")


class Destination(models.Model):
    uid = NanoidField(unique=True)
    name = models.CharField(_("名前"), max_length=100)
    kind = models.PositiveSmallIntegerField(_("種別"), choices=DestinationKind.choices)
    address = models.CharField(_("宛先"), max_length=500)
    is_active = models.BooleanField(_("有効"), default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name="+", verbose_name=_("作成者"))

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True)
    deleted = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("通知先")
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.name


auditlog.register(Destination)
```

### delivery/serializers.py

```python
from collections import defaultdict

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from utils.serializers import ReadOnlyModelSerializer
from .models import Destination, DestinationKind


class DestinationSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    created_by = serializers.SlugRelatedField(slug_field="uid", read_only=True)

    class Meta:
        model = Destination
        fields = ["id", "name", "kind", "kind_display", "address", "is_active", "created_by", "created", "updated"]
        read_only_fields = ["created", "updated"]

    def validate_address(self, value: str) -> str:
        return value.strip()

    def validate(self, attrs):
        # 部分更新（PATCH）では、送られなかった項目を既存の値で補って検証する
        kind = attrs.get("kind", getattr(self.instance, "kind", None))
        address = attrs.get("address", getattr(self.instance, "address", ""))

        errors = defaultdict(list)
        if kind in (DestinationKind.WEBHOOK, DestinationKind.TEAMS) and not address.startswith("https://"):
            errors["address"].append(_("URLはhttps://で始めてください"))
        if kind == DestinationKind.SLACK and not address.startswith("https://hooks.slack.com/"):
            errors["address"].append(_("SlackのIncoming WebhookのURLを入力してください"))
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class AppDestinationSerializer(ReadOnlyModelSerializer):
    """通常社員向け。宛先の値は見せない"""

    id = serializers.CharField(source="uid", read_only=True)
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = Destination
        fields = ["id", "name", "kind", "kind_display"]
```

### delivery/views.py

```python
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from utils.permissions import AppUser, ModelPermissions
from utils.views import SoftDeleteMixin
from .models import Destination, DestinationKind
from .serializers import AppDestinationSerializer, DestinationSerializer


class DestinationFilter(filters.FilterSet):
    name = filters.CharFilter(label=_("名前"), lookup_expr="icontains")
    kind = filters.ChoiceFilter(label=_("種別"), choices=DestinationKind.choices)

    class Meta:
        model = Destination
        fields = ["name", "kind", "is_active"]


@extend_schema(description="通知先の管理API", tags=["manage/destination"])
class DestinationViewSet(SoftDeleteMixin, ModelViewSet):
    # 基盤の管理。操作ごとにDestinationの標準のpermission（view・add・change・delete）で判定し、所属は問わない
    permission_classes = [ModelPermissions]
    lookup_field = "uid"
    queryset = Destination.objects.filter(deleted=False).select_related("created_by")
    serializer_class = DestinationSerializer
    filterset_class = DestinationFilter
    ordering = ("-created", "-pk")

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


@extend_schema(description="タスクに付けられる通知先の候補（ID・名前・種別だけ）", tags=["app/destination"])
class AppDestinationViewSet(ReadOnlyModelViewSet):
    permission_classes = [AppUser]
    lookup_field = "uid"
    queryset = Destination.objects.filter(deleted=False, is_active=True)
    serializer_class = AppDestinationSerializer
    filterset_class = DestinationFilter
    ordering = ("name", "pk")
```

### delivery/urls.py

```python
router = DefaultRouter()
router.register("v1/destinations", DestinationViewSet, basename="destination_viewset")
router.register("app/v1/destinations", AppDestinationViewSet, basename="app_destination_viewset")

urlpatterns = router.urls
```

要点：
- Viewに書いてあるのは、取得対象、権限、作成者の受け渡しだけ。検証と整形はすべてSerializerにある。
- 管理用の権限は、Groupの名前ではなくDjangoの標準のpermission（`delivery.add_destination` 等）で判定する（`ModelPermissions`）。permissionはGroup（permissionの集まり）か個別にユーザーへ付ける。
- 業務画面向けは、別のSerializer（`AppDestinationSerializer`）とViewSetで出す項目を絞る（候補のAPIは接続先や認証の設定を返さない。authorization.md §7.2）。1つのSerializerの中で利用者によって分岐させない。実際の配信先は範囲を持ち、候補は利用者のタスクの範囲で絞る（この例では省略）。
- 種別の表示名は `kind_display` としてサーバーで返す。フロントエンドはこれを表示に使う（frontend-examples.md）。
- `ordering` の最後に `pk` を置き、カーソル方式のページングで並び順が一意に決まるようにする。
- 書き換えるテーブルは1つなので、`perform_create` にtransactionは付けない。

---

## 例2 親子リソース（会話とメッセージ）

通常社員が自分の会話を作り、その会話にメッセージを投稿する。添付には、自分が所有するArtifactだけを指定できる。

### conversation/models.py

```python
from typing import Self

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from utils.fields import NanoidField


class MessageRole(models.IntegerChoices):
    USER = 1, _("ユーザー")
    AGENT = 2, _("エージェント")
    SYSTEM = 3, _("システム")


class ConversationQuerySet(models.QuerySet):
    def prefetch_messages(self) -> Self:
        return self.prefetch_related(
            models.Prefetch("messages",
                            queryset=ConversationMessage.objects.select_related("sender")
                            .prefetch_related("attachments").order_by("pk")))


class Conversation(models.Model):
    objects = ConversationQuerySet.as_manager()

    uid = NanoidField(unique=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                              related_name="conversations", verbose_name=_("所有者"))
    model_profile = models.ForeignKey("llm.ModelProfile", on_delete=models.PROTECT, verbose_name=_("モデル設定"))
    # 個人情報や機密情報を扱う会話は、ローカルLLMのモデルだけを使う（design.md §4冒頭）
    handles_sensitive_data = models.BooleanField(_("個人情報・機密情報を扱う"), default=False)
    title = models.CharField(_("タイトル"), max_length=200, blank=True, default="")
    last_posted = models.DateTimeField(_("最終投稿日時"), null=True, blank=True)

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True)
    deleted = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("会話")
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.title or self.uid


class ConversationMessage(models.Model):
    uid = NanoidField(unique=True)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                               verbose_name=_("送信者"))  # 外部スレッドからの投稿では空になりうる
    role = models.PositiveSmallIntegerField(_("送信元の種別"), choices=MessageRole.choices)
    body = models.TextField(_("本文"), blank=True, default="")
    attachments = models.ManyToManyField("artifact.Artifact", blank=True, related_name="+")

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("会話メッセージ")
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.conversation_id}:{self.pk}"
```

### conversation/serializers.py

```python
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from llm.models import ModelProfile, ProviderKind
from llm.serializers import MinimumModelProfileSerializer
from artifact.models import Artifact
from artifact.serializers import ArtifactSerializer
from utils.fields import NormalizedTextField
from .models import Conversation, ConversationMessage, MessageRole


class ConversationSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)
    model_profile = serializers.SlugRelatedField(
        slug_field="uid", queryset=ModelProfile.objects.filter(deleted=False, is_active=True))
    model_profile_info = MinimumModelProfileSerializer(source="model_profile", read_only=True)
    title = serializers.CharField(max_length=200, required=False, allow_blank=True)

    class Meta:
        model = Conversation
        fields = ["id", "model_profile", "model_profile_info", "handles_sensitive_data", "title", "last_posted",
                  "created", "updated"]
        read_only_fields = ["last_posted", "created", "updated"]

    def validate(self, attrs):
        # 部分更新（PATCH）では、送られなかった項目を今の値で補って検証する
        profile = attrs.get("model_profile", getattr(self.instance, "model_profile", None))
        sensitive = attrs.get("handles_sensitive_data", getattr(self.instance, "handles_sensitive_data", False))
        if sensitive and profile and profile.current_version.connection.kind != ProviderKind.LOCAL:
            raise serializers.ValidationError(
                {"model_profile": _("個人情報や機密情報を扱う会話では、ローカルLLMのモデルを選んでください")})
        return attrs


class MessageSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)
    conversation = serializers.SlugRelatedField(slug_field="uid", read_only=True)
    sender = serializers.SlugRelatedField(slug_field="uid", read_only=True)
    role = serializers.ChoiceField(choices=MessageRole.choices, read_only=True)
    body = NormalizedTextField(max_length=20000, required=False, allow_blank=True)
    attachments = serializers.SlugRelatedField(slug_field="uid", many=True, required=False,
                                               queryset=Artifact.objects.none())
    attachments_info = ArtifactSerializer(source="attachments", many=True, read_only=True)

    class Meta:
        model = ConversationMessage
        fields = ["id", "conversation", "sender", "role", "body", "attachments", "attachments_info", "created"]
        read_only_fields = ["created"]

    def get_fields(self):
        fields = super().get_fields()
        # 添付に指定できるのは、ログインユーザーが所有するArtifactだけ
        if request := self.context.get("request"):
            fields["attachments"].child_relation.queryset = Artifact.objects.filter(
                owner=request.user, deleted=False)
        return fields

    def validate(self, attrs):
        if not attrs.get("body") and not attrs.get("attachments"):
            raise serializers.ValidationError({"non_field_errors": _("本文または添付ファイルを入力してください")})
        return attrs

    def create(self, validated_data) -> ConversationMessage:
        with transaction.atomic():
            message = super().create(validated_data)  # ManyToManyはModelSerializerが保存する
            conversation = message.conversation
            conversation.last_posted = timezone.now()
            conversation.save(update_fields=["last_posted", "updated"])
        return message
```

### conversation/views.py

```python
from drf_spectacular.utils import extend_schema
from rest_framework.generics import get_object_or_404
from rest_framework.mixins import CreateModelMixin, ListModelMixin
from rest_framework.viewsets import GenericViewSet, ModelViewSet

from utils.permissions import AppUser
from utils.views import SoftDeleteMixin
from .models import Conversation, ConversationMessage, MessageRole
from .serializers import ConversationSerializer, MessageSerializer


class MyConversationMixin:
    """URLのconversationを、ログインユーザーが所有する会話に限定して取得する"""

    @property
    def conversation(self) -> Conversation:
        if not hasattr(self, "_conversation"):
            self._conversation = get_object_or_404(
                Conversation.objects.all(), uid=self.kwargs["conversation"],
                owner=self.request.user, deleted=False)
        return self._conversation

    def get_queryset(self):
        return super().get_queryset().filter(conversation=self.conversation)


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


@extend_schema(description="会話のメッセージに関するAPI", tags=["app/conversation/message"])
class AppConversationMessageViewSet(MyConversationMixin, ListModelMixin, CreateModelMixin, GenericViewSet):
    permission_classes = [AppUser]
    queryset = ConversationMessage.objects.select_related("conversation", "sender").prefetch_related("attachments")
    serializer_class = MessageSerializer
    ordering = "-pk"

    def perform_create(self, serializer):
        # 複数テーブルの書き換えは MessageSerializer.create() の transaction で囲んでいる
        serializer.save(conversation=self.conversation, sender=self.request.user, role=MessageRole.USER)
```

### conversation/urls.py

```python
router = DefaultRouter()
router.register("app/v1/conversations", AppConversationViewSet, basename="app_conversation_viewset")
router.register("app/v1/conversations/(?P<conversation>[^/]+)/messages", AppConversationMessageViewSet,
                basename="app_conversation_message_viewset")

urlpatterns = router.urls
```

要点：
- 親（会話）の所有者の確認はMixinで行う。他人の会話のURLにアクセスした場合は404を返し、会話が存在するかどうかを知らせない。
- 関連の選択肢（`attachments` の `queryset`）をログインユーザーで絞る。`queryset=Artifact.objects.all()` のままにすると、他人のArtifactのuidを指定して添付できてしまう。
- 改行の正規化は `NormalizedTextField` に任せ、Viewでもモデルでも行わない。
- `role` や `sender` はクライアントに指定させず、Viewの `perform_create` で決める。
- メッセージの保存と会話の `last_posted` の更新は、Serializerの `create()` の中でtransactionにまとめている。そのため `perform_create` には付けない。
- `last_posted` は同じ値が並びうるので、`ordering` の最後に `-pk` を置く。

---

## 例3 システムが進める状態と、利用者からの依頼（Run）

Runの状態は、ほとんどがWorkerとRunnerによって進む（design.md §5.3）。この例では、利用者がAPIから行う操作のうち「停止の依頼」と「質問への回答」を扱う。追加指示は、別にUserInstructionの作成として受け付ける（design.md §5.5）。どの操作でも、APIから遷移先を指定させることはない。停止と回答はそれぞれViewSetの `@action` とし、入力の検証が必要なので操作用のSerializerを付ける。

| 遷移 | 起こすもの | 例の中の場所 |
|---|---|---|
| QUEUED → RUNNING | claim（Worker） | `run/tasks.py` の `claim_run` |
| RUNNING → WAITING_USER / RETRY_WAIT / COMPLETED / FAILED | 実行結果（Runner） | 省略（fencing tokenを照合してから `set_status` を呼ぶ） |
| WAITING_USER → QUEUED | 回答（利用者） | `ClarificationAnswerSerializer` → `ClarificationRequest.accept_answer` |
| RETRY_WAIT → QUEUED | 再実行（Reconciler） | 省略 |
| QUEUED / WAITING_USER / RETRY_WAIT → CANCELLED、RUNNING → CANCELLING | 停止の依頼（利用者） | `RunCancelSerializer` → `Run.request_cancel` |
| CANCELLING → CANCELLED | 停止の確認（Worker） | 省略 |
| WAITING_USER → FAILED（回答期限切れ）、RETRY_WAIT → FAILED（上限到達）、CANCELLING → FAILED（停止できない） | システム（Reconciler・Worker） | 省略 |

### run/errors.py

```python
class InvalidTransition(Exception):
    pass


class AlreadyAnswered(Exception):
    pass
```

### run/models.py

```python
from django.conf import settings
from django.db import models, transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from utils.fields import NanoidField
from .errors import AlreadyAnswered, InvalidTransition


class RunStatus(models.IntegerChoices):
    QUEUED = 1, _("待機中")
    RUNNING = 2, _("実行中")
    WAITING_USER = 3, _("回答待ち")
    RETRY_WAIT = 4, _("再試行待ち")
    CANCELLING = 5, _("停止中")
    COMPLETED = 10, _("完了")
    FAILED = 20, _("失敗")
    CANCELLED = 30, _("取消")


ALLOWED_TRANSITIONS: dict[int, set[int]] = {
    RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.CANCELLED},
    RunStatus.RUNNING: {RunStatus.WAITING_USER, RunStatus.RETRY_WAIT, RunStatus.COMPLETED,
                        RunStatus.FAILED, RunStatus.CANCELLING},
    RunStatus.WAITING_USER: {RunStatus.QUEUED, RunStatus.CANCELLED, RunStatus.FAILED},
    RunStatus.RETRY_WAIT: {RunStatus.QUEUED, RunStatus.CANCELLED, RunStatus.FAILED},
    RunStatus.CANCELLING: {RunStatus.CANCELLED, RunStatus.FAILED},
}

CANCELLABLE_STATUSES = (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.WAITING_USER, RunStatus.RETRY_WAIT)
ACTIVE_STATUSES = CANCELLABLE_STATUSES + (RunStatus.CANCELLING,)


class Run(models.Model):
    uid = NanoidField(unique=True)
    conversation = models.ForeignKey("conversation.Conversation", on_delete=models.PROTECT,
                                     related_name="runs", verbose_name=_("会話"))
    status = models.PositiveSmallIntegerField(_("状態"), choices=RunStatus.choices, default=RunStatus.QUEUED)

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("実行")
        verbose_name_plural = verbose_name
        indexes = [models.Index(fields=["status", "-created"])]

    def __str__(self):
        return self.uid

    def can_transit(self, status: int) -> bool:
        return status in ALLOWED_TRANSITIONS.get(self.status, set())

    def is_cancellable(self) -> bool:
        return self.status in CANCELLABLE_STATUSES

    @transaction.atomic
    def set_status(self, status: RunStatus, comment: str = "", actor=None) -> "RunEvent":
        """状態を変更してイベントを記録する。API・Worker・Runnerのどこから呼ぶ場合もこのメソッドを通す"""
        # 同時の状態変更を防ぐため、行ロックを取ってから最新の状態で判定する
        current = Run.objects.select_for_update().get(pk=self.pk)
        if not current.can_transit(status):
            raise InvalidTransition(f"{current.get_status_display()} -> {RunStatus(status).label}")
        self.status = status
        self.save(update_fields=["status", "updated"])
        return RunEvent.record(self, RunEventKind.STATUS_CHANGED, status=status, comment=comment, actor=actor)

    @transaction.atomic
    def request_cancel(self, reason: str, actor) -> "RunEvent":
        """停止の依頼を受け付ける。実行中ならWorkerの停止確認を待つため停止中にする"""
        current = Run.objects.select_for_update().get(pk=self.pk)
        target = RunStatus.CANCELLING if current.status == RunStatus.RUNNING else RunStatus.CANCELLED
        return self.set_status(target, comment=reason, actor=actor)


class RunEventKind(models.IntegerChoices):
    STATUS_CHANGED = 1, _("状態変更")
    MESSAGE = 2, _("メッセージ")
    TOOL = 3, _("Tool実行")


class RunEvent(models.Model):
    """Runの履歴。作成後に変更・削除しない"""

    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="events")
    sequence = models.PositiveIntegerField(_("順序"))
    kind = models.PositiveSmallIntegerField(_("種別"), choices=RunEventKind.choices)
    status = models.PositiveSmallIntegerField(_("状態"), choices=RunStatus.choices, null=True, blank=True)
    comment = models.TextField(_("コメント"), blank=True, default="")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                              verbose_name=_("操作者"))

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("実行イベント")
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=["run", "sequence"], name="uniq_run_event_sequence")]

    def __str__(self):
        return f"{self.run_id}#{self.sequence}"

    @classmethod
    def record(cls, run: Run, kind: RunEventKind, **kwargs) -> "RunEvent":
        """呼び出し側でRunの行ロックを取っていること"""
        last = run.events.aggregate(last=Max("sequence"))["last"] or 0
        event = cls.objects.create(run=run, sequence=last + 1, kind=kind, **kwargs)
        # 同じtransactionの中でpg_notifyを実行し、SSEのストリームへ知らせる（stream/notifier.py、realtime.md §4。定義は省略）
        return event


class ClarificationStatus(models.IntegerChoices):
    OPEN = 1, _("回答待ち")
    ANSWERED = 2, _("回答済み")
    EXPIRED = 3, _("期限切れ")


class ClarificationRequest(models.Model):
    """エージェントから利用者への質問"""

    uid = NanoidField(unique=True)
    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="clarifications")
    question = models.TextField(_("質問"))
    options = models.JSONField(_("選択肢"), default=list, blank=True)
    status = models.PositiveSmallIntegerField(_("状態"), choices=ClarificationStatus.choices,
                                              default=ClarificationStatus.OPEN)
    answer = models.TextField(_("回答"), blank=True, default="")
    answered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    verbose_name=_("回答者"))
    answered = models.DateTimeField(_("回答日時"), null=True, blank=True)

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("確認質問")
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.uid

    @transaction.atomic
    def accept_answer(self, answer: str, actor) -> None:
        """回答を保存し、同じtransactionでRunを再キューする"""
        # dispatchはrun.modelsをimportするため、循環importを避けて関数内でimportする
        from .dispatch import enqueue_run

        current = ClarificationRequest.objects.select_for_update().get(pk=self.pk)
        if current.status != ClarificationStatus.OPEN:
            raise AlreadyAnswered(self.uid)
        self.status = ClarificationStatus.ANSWERED
        self.answer = answer
        self.answered_by = actor
        self.answered = timezone.now()
        self.save(update_fields=["status", "answer", "answered_by", "answered"])
        self.run.set_status(RunStatus.QUEUED, comment=_("確認への回答を受け付けました"), actor=actor)
        enqueue_run(self.run)  # Outboxに記録し、DispatcherがQ2へ通知する（design.md §5.2）
```

### run/tasks.py（Worker側の遷移）

```python
from django.db import transaction

from .models import ExecutionAttempt, Run, RunStatus
from .runner import Runner


def execute_run(run_id: int) -> None:
    """Q2から呼ばれる。同じRun IDが再配送されても安全にする"""
    attempt = claim_run(run_id)
    if attempt is None:
        return  # 他のWorkerが取得済み、または実行対象の状態ではない
    # 以降の遷移（WAITING_USER、COMPLETED等）はRunnerがfencing tokenを照合してから行う
    Runner(attempt).run()


@transaction.atomic
def claim_run(run_id: int) -> ExecutionAttempt | None:
    run = Run.objects.select_for_update(skip_locked=True).filter(pk=run_id, status=RunStatus.QUEUED).first()
    if run is None:
        return None
    run.set_status(RunStatus.RUNNING)
    return ExecutionAttempt.start(run)  # leaseとfencing tokenを発行する（定義は省略）
```

### run/serializers.py

```python
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from utils.exceptions import Conflict
from utils.fields import NormalizedTextField
from .errors import AlreadyAnswered, InvalidTransition
from .models import ClarificationRequest, ClarificationStatus, Run, RunEvent, RunStatus


class RunSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)
    conversation = serializers.SlugRelatedField(slug_field="uid", read_only=True)
    status = serializers.ChoiceField(choices=RunStatus.choices, read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Run
        fields = ["id", "conversation", "status", "status_display", "created", "updated"]
        read_only_fields = fields


class RunEventSerializer(serializers.ModelSerializer):
    actor = serializers.SlugRelatedField(slug_field="uid", read_only=True)

    class Meta:
        model = RunEvent
        fields = ["sequence", "kind", "status", "comment", "actor", "created"]
        read_only_fields = fields


class RunCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000, required=False, allow_blank=True)

    def validate(self, attrs):
        if not self.instance.is_cancellable():
            raise serializers.ValidationError({"non_field_errors": _("この実行は停止できません")})
        return attrs

    def update(self, instance: Run, validated_data) -> Run:
        actor = validated_data.pop("actor")  # Viewが save(actor=request.user) で渡す
        try:
            instance.request_cancel(validated_data.get("reason", ""), actor=actor)
        except InvalidTransition as exc:
            # validate()の後に、Workerが先に状態を変えた場合
            raise Conflict() from exc
        return instance

    def create(self, validated_data):
        raise NotImplementedError("instanceを渡して使う")

    def to_representation(self, instance: Run):
        return RunSerializer(instance, context=self.context).data


class ClarificationRequestSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)
    options = serializers.ListField(child=serializers.CharField(), read_only=True)
    answered_by = serializers.SlugRelatedField(slug_field="uid", read_only=True)

    class Meta:
        model = ClarificationRequest
        fields = ["id", "question", "options", "status", "answer", "answered_by", "answered", "created"]
        read_only_fields = fields


class ClarificationAnswerSerializer(serializers.Serializer):
    answer = NormalizedTextField(max_length=5000)

    def validate_answer(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError(_("回答を入力してください"))
        return value

    def validate(self, attrs):
        clarification: ClarificationRequest = self.instance
        if clarification.status != ClarificationStatus.OPEN:
            raise serializers.ValidationError({"non_field_errors": _("この質問には回答できません")})
        if clarification.options and attrs["answer"] not in clarification.options:
            raise serializers.ValidationError({"answer": _("選択肢から選んでください")})
        return attrs

    def update(self, instance: ClarificationRequest, validated_data) -> ClarificationRequest:
        actor = validated_data.pop("actor")
        try:
            instance.accept_answer(validated_data["answer"], actor=actor)
        except AlreadyAnswered as exc:
            # Web画面と外部スレッドから同時に回答された場合
            raise Conflict() from exc
        return instance

    def create(self, validated_data):
        raise NotImplementedError("instanceを渡して使う")

    def to_representation(self, instance: ClarificationRequest):
        return ClarificationRequestSerializer(instance, context=self.context).data
```

### run/views.py

```python
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, ReadOnlyModelViewSet

from utils.permissions import AppUser
from utils.serializers import CountSerializer
from .models import ACTIVE_STATUSES, ClarificationRequest, Run, RunEvent
from .serializers import (
    ClarificationAnswerSerializer, ClarificationRequestSerializer,
    RunCancelSerializer, RunEventSerializer, RunSerializer,
)


class MyRunMixin:
    """URLのrunを、ログインユーザーの会話のRunに限定して取得する"""

    @property
    def run(self) -> Run:
        if not hasattr(self, "_run"):
            self._run = get_object_or_404(
                Run.objects.all(), uid=self.kwargs["run"],
                conversation__owner=self.request.user, conversation__deleted=False)
        return self._run

    def get_queryset(self):
        return super().get_queryset().filter(run=self.run)


@extend_schema(description="自分のRunに関するAPI", tags=["app/run"])
class AppRunViewSet(ReadOnlyModelViewSet):
    permission_classes = [AppUser]
    lookup_field = "uid"
    queryset = Run.objects.select_related("conversation")
    serializer_class = RunSerializer
    filterset_fields = ["status"]  # 完全一致だけなのでFilterSetを作らない
    ordering = ("-created", "-pk")

    def get_queryset(self):
        return super().get_queryset().filter(conversation__owner=self.request.user, conversation__deleted=False)

    @extend_schema(
        description="実行の停止を依頼する。実行中の場合は停止中になり、Workerが停止を確認すると取消になる",
        request=RunCancelSerializer,
        responses={200: RunSerializer, 409: OpenApiResponse(description="他の処理と競合した")},
    )
    @action(detail=True, methods=["post"], serializer_class=RunCancelSerializer)
    def cancel(self, request, *args, **kwargs):
        serializer = self.get_serializer(instance=self.get_object(), data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(actor=request.user)
        return Response(serializer.data)

    @extend_schema(description="進行中のRunの件数", request=None, responses=CountSerializer)
    @action(detail=False, methods=["get"], serializer_class=CountSerializer)
    def active_count(self, request, *args, **kwargs):
        count = self.get_queryset().filter(status__in=ACTIVE_STATUSES).count()
        return Response(self.get_serializer({"count": count}).data)


@extend_schema(description="Runのイベント履歴", tags=["app/run/event"])
class AppRunEventViewSet(MyRunMixin, ListModelMixin, GenericViewSet):
    permission_classes = [AppUser]
    queryset = RunEvent.objects.select_related("actor")
    serializer_class = RunEventSerializer
    ordering = "sequence"  # Runの中で一意


@extend_schema(description="エージェントからの確認質問", tags=["app/run/clarification"])
class AppClarificationViewSet(MyRunMixin, ListModelMixin, RetrieveModelMixin, GenericViewSet):
    permission_classes = [AppUser]
    lookup_field = "uid"
    queryset = ClarificationRequest.objects.select_related("answered_by")
    serializer_class = ClarificationRequestSerializer
    filterset_fields = ["status"]
    ordering = ("-created", "-pk")

    @extend_schema(
        description="質問に回答する。回答を受け付けるとRunが再開される",
        request=ClarificationAnswerSerializer,
        responses={200: ClarificationRequestSerializer, 409: OpenApiResponse(description="すでに回答済み")},
    )
    @action(detail=True, methods=["post"], serializer_class=ClarificationAnswerSerializer)
    def answer(self, request, *args, **kwargs):
        serializer = self.get_serializer(instance=self.get_object(), data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(actor=request.user)
        return Response(serializer.data)
```

### run/urls.py

```python
router = DefaultRouter()
router.register("app/v1/runs", AppRunViewSet, basename="app_run_viewset")
router.register("app/v1/runs/(?P<run>[^/]+)/events", AppRunEventViewSet, basename="app_run_event_viewset")
router.register("app/v1/runs/(?P<run>[^/]+)/clarifications", AppClarificationViewSet,
                basename="app_run_clarification_viewset")

urlpatterns = router.urls
```

### run/tests.py

```python
import pytest
from rest_framework.test import APIClient

from .factories import ClarificationRequestFactory, RunFactory
from .models import ClarificationStatus, RunStatus


@pytest.fixture(name="api_client")
def fixture_api_client(app_user):
    api_client = APIClient()
    api_client.force_authenticate(app_user)
    return api_client


@pytest.mark.django_db
def test_cancel_queued_run(api_client, app_user):
    run = RunFactory(conversation__owner=app_user, status=RunStatus.QUEUED)
    res = api_client.post(f"/api/app/v1/runs/{run.uid}/cancel/", {"reason": "不要になった"})
    assert res.status_code == 200, res.data
    assert res.data["status"] == RunStatus.CANCELLED


@pytest.mark.django_db
def test_cancel_running_run_waits_for_worker(api_client, app_user):
    run = RunFactory(conversation__owner=app_user, status=RunStatus.RUNNING)
    res = api_client.post(f"/api/app/v1/runs/{run.uid}/cancel/", {})
    assert res.status_code == 200, res.data
    assert res.data["status"] == RunStatus.CANCELLING


@pytest.mark.django_db
def test_cannot_cancel_completed_run(api_client, app_user):
    run = RunFactory(conversation__owner=app_user, status=RunStatus.COMPLETED)
    res = api_client.post(f"/api/app/v1/runs/{run.uid}/cancel/", {})
    assert res.status_code == 400
    assert "non_field_errors" in res.data


@pytest.mark.django_db
def test_other_users_run_is_not_found(api_client, other_user):
    run = RunFactory(conversation__owner=other_user)
    res = api_client.post(f"/api/app/v1/runs/{run.uid}/cancel/", {})
    assert res.status_code == 404


@pytest.mark.django_db
def test_answer_requeues_run(api_client, app_user):
    run = RunFactory(conversation__owner=app_user, status=RunStatus.WAITING_USER)
    clarification = ClarificationRequestFactory(run=run)
    res = api_client.post(f"/api/app/v1/runs/{run.uid}/clarifications/{clarification.uid}/answer/",
                          {"answer": "A案で"})
    assert res.status_code == 200, res.data
    assert res.data["status"] == ClarificationStatus.ANSWERED
    run.refresh_from_db()
    assert run.status == RunStatus.QUEUED
```

要点：
- 利用者向けのAPIに、`status` を書き換えるendpointや `to_*` actionを作らない。この例で扱う利用者の操作は「停止の依頼（`cancel`）」と「回答（`answer`）」で、どちらもactionで受け付ける。状態はその結果としてModelのメソッドの中で変わる。
- 入力と検証がある操作なので、操作用のSerializer（`RunCancelSerializer`、`ClarificationAnswerSerializer`）を付ける。出力は既存の `RunSerializer` / `ClarificationRequestSerializer` を使い、件数は汎用の `CountSerializer` で返す。actionの本体は、DRFの標準の流れ（`get_serializer` → `is_valid` → `save` → `Response(serializer.data)`）だけにする。
- 操作者はViewから `serializer.save(actor=request.user)` で渡し、Serializerは `validated_data.pop("actor")` で受け取ってドメインメソッドに渡す。
- 遷移の可否はModelの `ALLOWED_TRANSITIONS` に集約し、行ロックの中で判定する。Serializerの `validate()` は、利用者に分かりやすいエラーを返すための事前確認として行う。
- `validate()` の後にWorkerが先に状態を変えた場合は、ドメインの例外を409に変換する。
- 複数テーブルの書き換え（状態とRunEvent、回答とRunとOutbox）は、ドメインメソッドの `@transaction.atomic` で囲んでいる。そのためactionには付けない。
- Worker側の遷移はAPIを通らず、`tasks.py` やRunnerから同じ `set_status` を呼ぶ。QClusterだけのWorkerサーバー（design.md §6.1）でも同じメソッドを呼ぶ。
- イベント履歴と質問は、`@action` の中で一覧を返さずに、子リソースのViewSetにする。そうすれば並び順もページングもDRFの標準の仕組みで動く。

---

## 例4 不変の版を公開する（SkillとSkillVersion）

> この例は、楽観的な競合検出と、範囲を持つものの判定の置き場所を示すために単純化している。実際の公開は、SkillDraftの内容から版を作る（design.md §3.2・§3.6・§3.10）。判定の規則は authorization.md §5.1。

Skillは可変の入れ物で、公開した本文はSkillVersionとして不変に残す。公開するときは、編集を始めた時点の版番号（`expected_version`）を送らせる。その間に他の人が公開していた場合は、409を返す。

### skill/errors.py

```python
class VersionConflict(Exception):
    pass
```

### skill/models.py

```python
from auditlog.registry import auditlog
from django.conf import settings
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

from utils.fields import NanoidField
from utils.permissions import AppPermission
from .errors import VersionConflict


class Skill(models.Model):
    uid = NanoidField(unique=True)
    name = models.CharField(_("名前"), max_length=100)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name=_("所有者"))
    latest_version = models.PositiveIntegerField(_("最新の版"), default=0)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    deleted = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("Skill")
        verbose_name_plural = verbose_name
        permissions = [AppPermission.PUBLISH_COMPANY_SKILLS.meta]

    def __str__(self):
        return self.name

    @transaction.atomic
    def publish(self, body: str, change_reason: str, actor, expected_version: int) -> "SkillVersion":
        current = Skill.objects.select_for_update().get(pk=self.pk)
        if current.latest_version != expected_version:
            raise VersionConflict(f"expected={expected_version} latest={current.latest_version}")
        version = SkillVersion.objects.create(
            skill=current, number=current.latest_version + 1,
            body=body, change_reason=change_reason, created_by=actor)
        current.latest_version = version.number
        current.save(update_fields=["latest_version", "updated"])
        self.latest_version = current.latest_version
        return version


class SkillVersion(models.Model):
    """公開済みのSkill本文。作成後に変更・削除しない"""

    uid = NanoidField(unique=True)
    skill = models.ForeignKey(Skill, on_delete=models.PROTECT, related_name="versions")
    number = models.PositiveIntegerField(_("版"))
    body = models.TextField(_("本文"))
    change_reason = models.CharField(_("変更理由"), max_length=500)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name=_("公開者"))

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Skillの版")
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=["skill", "number"], name="uniq_skill_version_number")]

    def __str__(self):
        return f"{self.skill} v{self.number}"


auditlog.register(Skill)
auditlog.register(SkillVersion)
```

### skill/serializers.py

```python
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from utils.exceptions import Conflict
from utils.fields import NormalizedTextField
from .errors import VersionConflict
from .models import Skill, SkillVersion


class SkillSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)
    owner = serializers.SlugRelatedField(slug_field="uid", read_only=True)

    class Meta:
        model = Skill
        fields = ["id", "name", "owner", "latest_version", "created", "updated"]
        read_only_fields = ["latest_version", "created", "updated"]


class SkillVersionSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)
    created_by = serializers.SlugRelatedField(slug_field="uid", read_only=True)

    class Meta:
        model = SkillVersion
        fields = ["id", "number", "body", "change_reason", "created_by", "created"]
        read_only_fields = fields


class SkillPublishSerializer(serializers.Serializer):
    expected_version = serializers.IntegerField(min_value=0, help_text=_("編集を始めた時点の最新の版"))
    body = NormalizedTextField(max_length=100_000)
    change_reason = serializers.CharField(max_length=500)

    def validate_body(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError(_("本文を入力してください"))
        return value

    def create(self, validated_data) -> SkillVersion:
        skill: Skill = validated_data.pop("skill")
        actor = validated_data.pop("actor")
        try:
            return skill.publish(actor=actor, **validated_data)
        except VersionConflict as exc:
            raise Conflict() from exc

    def update(self, instance, validated_data):
        raise NotImplementedError("版は更新しない")

    def to_representation(self, instance: SkillVersion):
        return SkillVersionSerializer(instance, context=self.context).data
```

### skill/views.py

```python
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from rest_framework.exceptions import PermissionDenied

from utils.permissions import AppUser
from utils.views import SoftDeleteMixin
from .models import Skill, SkillAction, SkillVersion
from .serializers import SkillPublishSerializer, SkillSerializer, SkillVersionSerializer


class SkillMixin:
    """URLのskillを、削除されていないSkillに限定して取得する"""

    @property
    def skill(self) -> Skill:
        if not hasattr(self, "_skill"):
            # 一覧と同じ判定で見えるSkillだけ（見えなければ404）
            self._skill = get_object_or_404(Skill.objects.visible_to(self.request.user), uid=self.kwargs["skill"])
        return self._skill

    def get_queryset(self):
        return super().get_queryset().filter(skill=self.skill)


@extend_schema(description="Skill（業務画面）", tags=["app/skill"])
class AppSkillViewSet(SoftDeleteMixin, ModelViewSet):
    # 範囲を持つものは、ログインだけをpermission_classesで確かめ、対象はQuerySetとModelの判定で決める
    permission_classes = [AppUser]
    lookup_field = "uid"
    queryset = Skill.objects.select_related("owner", "department")

    # 見えることと、変えられることは別。操作ごとにModelの判定で確かめる（作成の可否はSerializerで判定する）
    OBJECT_ACTIONS = {
        "update": SkillAction.EDIT,
        "partial_update": SkillAction.EDIT,
        "destroy": SkillAction.DELETE,
        "publish": SkillAction.PUBLISH,
    }

    def get_queryset(self):
        return super().get_queryset().visible_to(self.request.user)

    def get_object(self):
        skill = super().get_object()
        required = self.OBJECT_ACTIONS.get(self.action)
        if required is not None and not skill.can(self.request.user, required):
            raise PermissionDenied
        return skill
    serializer_class = SkillSerializer
    search_fields = ["name"]
    ordering = ("-updated", "-pk")

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    @extend_schema(
        description="新しい版を公開する",
        request=SkillPublishSerializer,
        responses={201: SkillVersionSerializer, 409: OpenApiResponse(description="他の公開と競合した")},
    )
    @action(detail=True, methods=["post"], serializer_class=SkillPublishSerializer)
    def publish(self, request, *args, **kwargs):
        skill = self.get_object()  # 公開の権限は get_object() で確かめた
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(skill=skill, actor=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


@extend_schema(description="Skillの公開済みの版", tags=["app/skill/version"])
class AppSkillVersionViewSet(SkillMixin, ReadOnlyModelViewSet):
    permission_classes = [AppUser]
    lookup_field = "number"  # 版番号で識別する子リソースの例外（backend-conventions.md §4.1）
    queryset = SkillVersion.objects.select_related("created_by")
    serializer_class = SkillVersionSerializer
    ordering = "-number"  # Skillの中で一意
```

### skill/urls.py

```python
router = DefaultRouter()
router.register("app/v1/skills", AppSkillViewSet, basename="app_skill_viewset")
router.register("app/v1/skills/(?P<skill>[^/]+)/versions", AppSkillVersionViewSet, basename="app_skill_version_viewset")

urlpatterns = router.urls
```

### skill/admin.py

```python
@admin.register(SkillVersion)
class SkillVersionAdmin(admin.ModelAdmin):
    raw_id_fields = ("skill", "created_by")
    readonly_fields = ("uid", "created")
    list_display = ("id", "skill", "number", "created_by", "created")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
```

要点：
- 版の作成はSerializerの `create()` で行い、Modelの `publish()` を呼ぶ。Viewは対象のSkillと操作者を `save(skill=skill, actor=request.user)` で渡すだけにする。
- 競合はドメインの例外（`VersionConflict`）として送出し、Serializerで409（`Conflict`）に変換する。
- 版とSkillの書き換えは `Skill.publish()` の `@transaction.atomic` で囲んでいる。
- 公開の入力（`SkillPublishSerializer`）と出力（`SkillVersionSerializer`）は別のSerializerにする。入力側は `to_representation` で出力用Serializerに処理を任せる。そうすることで、スキーマ上のリクエストとレスポンスの型を分けられる。
- 判定はViewに書かない。一覧と親の取得は `Skill.objects.visible_to(user)`、更新・削除・公開は `get_object()` の中で `skill.can(user, 操作)` で行い（見えるだけでは変えられない）、どちらも同じ規則表から作る（backend-conventions.md §6.3）。
- URLの親（Skill）は `SkillMixin` のpropertyで取得する。版はSkillの中の版番号で識別するので、`lookup_field = "number"` とする。
- 版は、APIでも管理画面でも変更・削除できないようにする。切り戻すときも、旧版の本文で新しい版を公開する。
