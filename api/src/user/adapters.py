"""社内SSO（django-allauth）の振る舞い。自社のアカウントだけを受け付ける（backend-conventions.md §6.2）"""

import logging

from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialLogin
from django.conf import settings
from django.http import HttpRequest, HttpResponseRedirect

logger = logging.getLogger(__name__)


def _login_error(code: str) -> ImmediateHttpResponse:
    return ImmediateHttpResponse(HttpResponseRedirect(f"{settings.LOGIN_URL}?error={code}"))


def is_allowed_account(sociallogin: SocialLogin) -> bool:
    """有効な認証基盤から、許可したドメインのアカウントでログインしたか"""
    provider = sociallogin.account.provider
    if provider not in settings.YORIKI_SSO_PROVIDERS:
        return False

    email = (sociallogin.user.email or "").lower()
    domain = email.rpartition("@")[2]
    if not domain or domain not in settings.YORIKI_SSO_ALLOWED_EMAIL_DOMAINS:
        return False

    extra = sociallogin.account.extra_data
    if provider == "google":
        # Google Workspaceのアカウントか（個人のGoogleアカウントを除く）をIDトークンのhdで確認する。
        # ドメインが設定されていなければ、誰も受け付けない（settingsでも起動時に確認している）
        hosted_domain = settings.YORIKI_SSO_GOOGLE_HOSTED_DOMAIN
        if not hosted_domain or extra.get("hd") != hosted_domain:
            return False
        if not extra.get("email_verified", False):
            return False
    # Microsoftは、ログインの開始時に自社のテナントを指定しているので、他のテナントのアカウントは戻ってこない
    return True


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest) -> bool:
        # メールとパスワードによるアカウント作成は使わない
        return False


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest, sociallogin: SocialLogin) -> bool:
        # 許可したアカウントは、初回のログインでユーザーを作る（pre_social_loginで確認済み）
        return True

    def pre_social_login(self, request: HttpRequest, sociallogin: SocialLogin) -> None:
        if not is_allowed_account(sociallogin):
            logger.warning(
                "許可されていないアカウントのログインを拒否しました: provider=%s email=%s",
                sociallogin.account.provider,
                sociallogin.user.email,
            )
            raise _login_error("not_allowed")

    def populate_user(self, request: HttpRequest, sociallogin: SocialLogin, data: dict):
        user = super().populate_user(request, sociallogin, data)
        extra = sociallogin.account.extra_data
        user.display_name = (extra.get("displayName") or extra.get("name") or "")[:150]
        return user

    def on_authentication_error(self, request, provider, error=None, exception=None, extra_context=None) -> None:
        logger.warning("SSOのログインに失敗しました: provider=%s error=%s", provider.id, error, exc_info=exception)
        raise _login_error("failed")
