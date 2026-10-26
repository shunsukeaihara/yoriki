from django.contrib.auth import login, logout
from django.contrib.auth.models import Group
from django.db import transaction
from django.db.models import Count, Q
from django.middleware.csrf import get_token
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet, ModelViewSet, ReadOnlyModelViewSet

from utils.permissions import AppUser, CsrfProtected, ModelPermissions
from utils.views import SoftDeleteMixin

from .models import ACCOUNT_PERMISSIONS_PREFETCH, Department, DepartmentMembership, Invitation, SSOProvider, User
from .serializers import (
    AuthMethodsSerializer,
    DepartmentMembershipSerializer,
    DepartmentSerializer,
    GroupSerializer,
    InvitationAcceptSerializer,
    InvitationLinkSerializer,
    InvitationTokenSerializer,
    IssueInvitationSerializer,
    LoginSerializer,
    ManageUserCreateSerializer,
    ManageUserSerializer,
    ManageUserUpdateSerializer,
    MyUserSerializer,
    SessionSerializer,
    SSOProviderSerializer,
    SSOProviderUpdateSerializer,
    check_membership_change,
)

PASSWORD_BACKEND = "utils.backends.PasswordBackend"


@extend_schema(tags=["app/auth/session"])
class AuthSessionAPIView(APIView):
    """ログイン状態の確認、パスワードでのログイン、ログアウト。画面の起動時に呼んで、CSRFのCookieも受け取る"""

    permission_classes = [AllowAny]

    def get_permissions(self):
        # 未ログインでもCSRFを確かめる（ログインCSRFを防ぐ）
        if self.request.method == "POST":
            return [CsrfProtected()]
        return super().get_permissions()

    @extend_schema(description="ログイン状態を返し、CSRFのCookieを設定する", request=None, responses=SessionSerializer)
    def get(self, request: Request) -> Response:
        get_token(request)  # ensure_csrf_cookieと同じく、CSRFのCookieを応答に付ける
        return Response(SessionSerializer({"is_authenticated": request.user.is_authenticated}).data)

    @extend_schema(
        description="メールアドレスとパスワードでログインする。有効なSSOの設定があれば基盤管理者だけ。"
        "失敗が続くと、アカウントごと・送信元のIPアドレスごとに一定時間止める（429）",
        request=LoginSerializer,
        responses=SessionSerializer,
    )
    def post(self, request: Request) -> Response:
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        login(request, serializer.validated_data["user"])
        return Response(SessionSerializer({"is_authenticated": True}).data)

    @extend_schema(description="ログアウトする", request=None, responses={204: None})
    def delete(self, request: Request) -> Response:
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["app/auth/provider"])
class AuthMethodsAPIView(APIView):
    """ログイン画面に出すログインの方法（有効なSSOの認証基盤と、パスワードの欄を出すか）"""

    permission_classes = [AllowAny]

    @extend_schema(description="ログインの方法", request=None, responses=AuthMethodsSerializer)
    def get(self, request: Request) -> Response:
        providers = [
            {"id": sso.kind, "name": sso.get_kind_display(), "login_url": reverse(f"{sso.kind}_login")}
            for sso in SSOProvider.objects.active().order_by("created", "pk")
        ]
        return Response(AuthMethodsSerializer({"providers": providers, "password_login": not providers}).data)


@extend_schema(tags=["app/auth/invitation"])
class AuthInvitationViewSet(GenericViewSet):
    """招待・再設定のリンク。本人がパスワードを設定する（ログインしていなくても使う）。

    tokenはURLのフラグメントにあり、画面が本文で送る（アクセスログに残さない）
    """

    permission_classes = [CsrfProtected]
    serializer_class = InvitationTokenSerializer
    pagination_class = None
    filter_backends = []

    @extend_schema(description="リンクを確かめ、宛先のメールアドレスと期限を返す", responses=InvitationTokenSerializer)
    @action(detail=False, methods=["post"])
    def verify(self, request: Request) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(self.get_serializer(serializer.validated_data["invitation"]).data)

    @extend_schema(
        description="パスワードを設定してリンクを使用済みにし、ログインする",
        request=InvitationAcceptSerializer,
        responses=SessionSerializer,
    )
    @action(detail=False, methods=["post"], serializer_class=InvitationAcceptSerializer)
    def accept(self, request: Request) -> Response:
        with transaction.atomic():
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            invitation = serializer.save()
        login(request, invitation.user, backend=PASSWORD_BACKEND)
        return Response(SessionSerializer({"is_authenticated": True}).data)


@extend_schema(tags=["app/my"])
class MyUserAPIView(APIView):
    """ログイン中の利用者自身の情報"""

    permission_classes = [AppUser]

    @extend_schema(description="ログイン中の利用者と権限を返す", request=None, responses=MyUserSerializer)
    def get(self, request: Request) -> Response:
        return Response(MyUserSerializer(request.user).data)


@extend_schema(description="社内SSOの設定の管理API", tags=["manage/sso_provider"])
@extend_schema_view(
    update=extend_schema(request=SSOProviderUpdateSerializer, responses=SSOProviderSerializer),
    partial_update=extend_schema(request=SSOProviderUpdateSerializer, responses=SSOProviderSerializer),
)
class SSOProviderViewSet(SoftDeleteMixin, ModelViewSet):
    """有効な設定が1つでもあれば、社員はSSOだけでログインする（design.md §10.0）"""

    # client secretを扱い、誰がログインできるかを決める。permissionは既定では基盤管理者だけが持つ
    permission_classes = [ModelPermissions]
    lookup_field = "uid"
    queryset = SSOProvider.objects.filter(deleted=False).select_related("created_by")
    serializer_class = SSOProviderSerializer
    filterset_fields = ["kind", "is_active"]
    ordering = ("created", "pk")

    def get_serializer_class(self):
        # 更新では種別を変えられない
        if self.action in ("update", "partial_update"):
            return SSOProviderUpdateSerializer
        return super().get_serializer_class()

    @transaction.atomic
    def perform_create(self, serializer):
        self._revoke_invitations_if_active(serializer.save(created_by=self.request.user))

    @transaction.atomic
    def perform_update(self, serializer):
        self._revoke_invitations_if_active(serializer.save())

    def _revoke_invitations_if_active(self, sso: SSOProvider) -> None:
        # SSOを有効にしたら、基盤管理者以外の未使用の招待・再設定のリンクを無効にする（design.md §10.0）
        if sso.is_active and not sso.deleted:
            Invitation.revoke_for_sso()


class ManageUserFilter(filters.FilterSet):
    group = filters.CharFilter(label=_("Group"), field_name="groups__name")

    class Meta:
        model = User
        fields = ["is_active", "group"]


@extend_schema(description="ユーザーの管理API", tags=["manage/user"])
@extend_schema_view(
    create=extend_schema(request=ManageUserCreateSerializer, responses={201: ManageUserCreateSerializer}),
    update=extend_schema(request=ManageUserUpdateSerializer, responses=ManageUserSerializer),
    partial_update=extend_schema(request=ManageUserUpdateSerializer, responses=ManageUserSerializer),
)
class ManageUserViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    GenericViewSet,
):
    """Yorikiのアカウントの作成、Groupの割り当て、無効化、招待・再設定のリンクの発行。

    ユーザーは削除せず、無効にする（会話・成果物・監査ログの持ち主として残す）
    """

    permission_classes = [ModelPermissions]
    action_permissions = {"invitation": "change"}
    lookup_field = "uid"
    queryset = User.objects.prefetch_related("groups", "socialaccount_set", *ACCOUNT_PERMISSIONS_PREFETCH)
    serializer_class = ManageUserSerializer
    filterset_class = ManageUserFilter
    search_fields = ("email", "display_name")
    ordering = ("email", "pk")

    def get_serializer_class(self):
        if self.action == "create":
            return ManageUserCreateSerializer
        if self.action in ("update", "partial_update"):
            return ManageUserUpdateSerializer
        return super().get_serializer_class()

    @transaction.atomic
    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @extend_schema(
        description="招待・パスワードの再設定のリンクを発行する（同じユーザーの未使用のリンクは無効になる）。"
        "SSOが有効な間は、基盤管理者のアカウントにだけ発行できる",
        request=None,
        responses={201: InvitationLinkSerializer},
    )
    @action(detail=True, methods=["post"], serializer_class=IssueInvitationSerializer)
    def invitation(self, request: Request, *args, **kwargs) -> Response:
        user = self.get_object()
        serializer = IssueInvitationSerializer(data={}, context={"request": request, "user": user})
        serializer.is_valid(raise_exception=True)
        link = serializer.save()
        return Response(InvitationLinkSerializer(link).data, status=status.HTTP_201_CREATED)


@extend_schema(description="Group（permissionの集まり）の一覧。ユーザーに割り当てる選択肢", tags=["manage/group"])
class GroupViewSet(ReadOnlyModelViewSet):
    # ユーザーにGroupを割り当てる人が選択肢として読む。中身の編集はDjangoの管理画面で行う
    permission_classes = [ModelPermissions]
    permission_model = User
    lookup_field = "name"
    queryset = Group.objects.all()
    serializer_class = GroupSerializer
    pagination_class = None  # Groupは少ないので、ページングしない
    filter_backends = []

    def get_queryset(self):
        return super().get_queryset().order_by("name")


@extend_schema(description="部門の管理API", tags=["manage/department"])
class DepartmentViewSet(SoftDeleteMixin, ModelViewSet):
    """部門のSkill・Connector等の所有の単位（authorization.md §4）。削除しても所属は残り、
    その部門のものは使えなくなる"""

    permission_classes = [ModelPermissions]
    lookup_field = "uid"
    queryset = Department.objects.filter(deleted=False).annotate(member_count=Count("memberships"))
    serializer_class = DepartmentSerializer
    search_fields = ("name",)
    ordering = ("name", "pk")


class DepartmentMixin:
    """URLのdepartmentを、削除されていない部門に限定して取得する"""

    kwargs: dict[str, str]

    @property
    def department(self) -> Department:
        if not hasattr(self, "_department"):
            self._department = get_object_or_404(Department.objects.all(), uid=self.kwargs["department"], deleted=False)
        return self._department

    def get_queryset(self):
        return super().get_queryset().filter(department=self.department)  # type: ignore[misc]

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "department": self.department}  # type: ignore[misc]


class DepartmentMembershipFilter(filters.FilterSet):
    q = filters.CharFilter(label=_("社員"), method="filter_user")

    class Meta:
        model = DepartmentMembership
        fields: list[str] = []

    def filter_user(self, queryset, name, value):
        return queryset.filter(Q(user__email__icontains=value) | Q(user__display_name__icontains=value))


@extend_schema(description="部門の所属の管理API", tags=["manage/department/member"])
class DepartmentMembershipViewSet(
    DepartmentMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    GenericViewSet,
):
    """所属の付け外しは、部門の変更として部門のpermissionで判定する"""

    permission_classes = [ModelPermissions]
    permission_model = Department
    action_permissions = {"create": "change", "destroy": "change"}
    lookup_field = "uid"
    queryset = DepartmentMembership.objects.select_related("user")
    serializer_class = DepartmentMembershipSerializer
    filterset_class = DepartmentMembershipFilter
    ordering = ("user__email", "pk")

    def perform_create(self, serializer):
        serializer.save(department=self.department)

    @transaction.atomic
    def perform_destroy(self, instance: DepartmentMembership):
        check_membership_change(self.request.user, instance.user, lock=True)  # type: ignore[arg-type]
        instance.delete()
