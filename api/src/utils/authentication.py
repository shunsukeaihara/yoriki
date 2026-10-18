import drf_spectacular.authentication
from rest_framework import authentication
from rest_framework.request import Request


class SessionAuthentication(authentication.SessionAuthentication):
    """DjangoのセッションCookieで認証する。

    DRF標準のSessionAuthenticationは未ログインを403で返すので、`WWW-Authenticate` を返して401にする。
    フロントエンドは401でログイン画面へ移動し、403（権限不足）と区別する（backend-conventions.md §6.2）。
    """

    def authenticate_header(self, request: Request) -> str:
        return 'Session realm="api"'


class SessionScheme(drf_spectacular.authentication.SessionScheme):
    """OpenAPIのスキーマで、上のSessionAuthenticationをCookie認証として示す"""

    target_class = "utils.authentication.SessionAuthentication"
