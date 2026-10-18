from django.conf import settings
from django.contrib.auth import logout
from django.middleware.csrf import get_token
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet

from utils.permissions import AppUser

from .serializers import AuthProviderSerializer, MyUserSerializer, SessionSerializer

PROVIDER_NAMES = {
    "microsoft": _("Microsoft 365"),
    "google": _("Google Workspace"),
}


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
            {"id": pid, "name": PROVIDER_NAMES.get(pid, pid), "login_url": reverse(f"{pid}_login")}
            for pid in settings.YORIKI_SSO_PROVIDERS
        ]
        return Response(self.get_serializer(providers, many=True).data)


@extend_schema(tags=["app/my"])
class MyUserAPIView(APIView):
    """ログイン中の利用者自身の情報"""

    permission_classes = [AppUser]

    @extend_schema(description="ログイン中の利用者と権限を返す", request=None, responses=MyUserSerializer)
    def get(self, request: Request) -> Response:
        return Response(MyUserSerializer(request.user).data)
