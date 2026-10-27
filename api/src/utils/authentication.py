import hashlib
from abc import ABC, abstractmethod
from typing import Any

import drf_spectacular.authentication
from django.contrib.auth.models import AnonymousUser
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework import authentication, exceptions
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


def hash_hook_key(key: str) -> str:
    """Webhookの鍵のハッシュ（SHA-256）。DBには鍵そのものを保存せず、これと見分け用の先頭の数文字だけを保存する。
    鍵は十分に長い乱数なので、塩やストレッチングは使わない（招待のtokenと同じ）"""
    return hashlib.sha256(key.encode()).hexdigest()


class HookKeyAuthentication(authentication.BaseAuthentication, ABC):
    """`Authorization: Bearer <鍵>` で連携（入口）を認証する `Hook*` 用の認証の土台（backend-conventions.md §4.2）。
    セッションは使わない。認証した連携は `request.auth` に入れ、`request.user` は匿名のままにする（連携は人ではない）。
    鍵がない・形が違う・どの連携のものでもない要求は401にする"""

    keyword = "Bearer"

    def authenticate(self, request: Request):
        header = authentication.get_authorization_header(request).decode("latin-1")
        scheme, _sep, key = header.partition(" ")
        if scheme.lower() != self.keyword.lower():
            return None
        key = key.strip()
        if not key or len(key) > 200:
            raise exceptions.AuthenticationFailed()
        binding = self.resolve(hash_hook_key(key))
        if binding is None:
            raise exceptions.AuthenticationFailed()
        return AnonymousUser(), binding

    @abstractmethod
    def resolve(self, key_hash: str) -> Any:
        """鍵のハッシュから連携を探す。なければ None"""

    def authenticate_header(self, request: Request) -> str:
        return f'{self.keyword} realm="hooks"'


class HookKeyScheme(OpenApiAuthenticationExtension):
    """OpenAPIのスキーマで、上のHookKeyAuthenticationをBearer認証として示す"""

    target_class = "utils.authentication.HookKeyAuthentication"
    match_subclasses = True
    name = "hookKey"

    def get_security_definition(self, auto_schema):
        return {"type": "http", "scheme": "bearer", "description": "入口の鍵（作成時・作り直したときに一度だけ表示）"}
