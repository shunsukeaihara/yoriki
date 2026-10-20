from django.contrib.auth import logout
from django.middleware.csrf import get_token
from django.urls import reverse
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet, ModelViewSet

from utils.permissions import AppUser, PlatformAdmin
from utils.views import SoftDeleteMixin

from .models import SSOProvider
from .serializers import (
    AuthProviderSerializer,
    MyUserSerializer,
    SessionSerializer,
    SSOProviderSerializer,
    SSOProviderUpdateSerializer,
)


@extend_schema(tags=["app/auth/session"])
class AuthSessionAPIView(APIView):
    """ログイン状態の確認とログアウト。画面の起動時に呼んで、CSRFのCookieも受け取る"""

    permission_classes = [AllowAny]

    @extend_schema(description="ログイン状態を返し、CSRFのCookieを設定する", request=None, responses=SessionSerializer)
    def get(self, request: Request) -> Response:
        get_token(request)  # ensure_csrf_cookieと同じく、CSRFのCookieを応答に付ける
        return Response(SessionSerializer({"is_authenticated": request.user.is_authenticated}).data)

    @extend_schema(description="ログアウトする", request=None, responses={204: None})
    def delete(self, request: Request) -> Response:
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["app/auth/provider"])
class AuthProviderViewSet(GenericViewSet):
    """ログイン画面に並べる、有効な認証基盤の一覧"""

    permission_classes = [AllowAny]
    serializer_class = AuthProviderSerializer
    pagination_class = None
    filter_backends = []

    @extend_schema(description="有効な認証基盤の一覧", responses=AuthProviderSerializer(many=True))
    def list(self, request: Request) -> Response:
        providers = [
            {"id": sso.kind, "name": sso.get_kind_display(), "login_url": reverse(f"{sso.kind}_login")}
            for sso in SSOProvider.objects.active().order_by("created", "pk")
        ]
        return Response(self.get_serializer(providers, many=True).data)


@extend_schema(tags=["app/my"])
class MyUserAPIView(APIView):
    """ログイン中の利用者自身の情報"""

    permission_classes = [AppUser]

    @extend_schema(description="ログイン中の利用者と権限を返す", request=None, responses=MyUserSerializer)
    def get(self, request: Request) -> Response:
        return Response(MyUserSerializer(request.user).data)


@extend_schema(description="社内SSOの設定の管理API（基盤管理者だけ）", tags=["manage/sso_provider"])
@extend_schema_view(
    update=extend_schema(request=SSOProviderUpdateSerializer, responses=SSOProviderSerializer),
    partial_update=extend_schema(request=SSOProviderUpdateSerializer, responses=SSOProviderSerializer),
)
class SSOProviderViewSet(SoftDeleteMixin, ModelViewSet):
    """有効な設定が1つでもあれば、社員はSSOだけでログインする（design.md §10.0）"""

    # client secretを扱い、誰がログインできるかを決めるので、基盤管理者だけにする
    permission_classes = [PlatformAdmin]
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

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
