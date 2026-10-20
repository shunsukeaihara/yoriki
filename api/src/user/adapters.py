"""社内SSO（django-allauth）の振る舞い。自社のアカウントだけを受け付ける（backend-conventions.md §6.2）"""

import logging

from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialApp, SocialLogin
from django.conf import settings
from django.http import HttpRequest, HttpResponseRedirect

from .models import SSOKind, SSOProvider, User

logger = logging.getLogger(__name__)


def _login_error(code: str) -> ImmediateHttpResponse:
    return ImmediateHttpResponse(HttpResponseRedirect(f"{settings.LOGIN_URL}?error={code}"))


def is_allowed_account(sociallogin: SocialLogin) -> bool:
    """有効なSSOの設定の認証基盤から、許可したドメインのアカウントでログインしたか"""
    sso = SSOProvider.objects.active().filter(kind=sociallogin.account.provider).first()
    if sso is None:
        return False
    if not sso.allows_email(sociallogin.user.email or ""):
        return False

    extra = sociallogin.account.extra_data
    if sso.kind == SSOKind.GOOGLE:
        # Google Workspaceのアカウントか（個人のGoogleアカウントを除く）をIDトークンのhdで確認する。
        # ドメインが設定されていなければ、誰も受け付けない（Serializerでも必須にしている）
        if not sso.hosted_domain or extra.get("hd") != sso.hosted_domain:
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
    def list_apps(self, request: HttpRequest, provider=None, client_id=None) -> list[SocialApp]:
        """有効なSSOの設定から、保存しない SocialApp を作る。client secretはここでだけ復号する。

        allauthの SocialApp のテーブルと settings の SOCIALACCOUNT_PROVIDERS の APPS は使わない
        """
        providers = SSOProvider.objects.active()
        if provider:
            providers = providers.filter(kind=provider)
        if client_id:
            providers = providers.filter(client_id=client_id)
        return [
            SocialApp(
                provider=sso.kind,
                name=sso.get_kind_display(),
                client_id=sso.client_id,
                secret=sso.client_secret.reveal(),
                settings=sso.app_settings(),
            )
            for sso in providers
        ]

    def get_provider(self, request: HttpRequest, provider: str, client_id=None):
        # 設定のない（無効・削除した）認証基盤のログインの開始とコールバックは、500にせずログイン画面へ戻す
        try:
            return super().get_provider(request, provider, client_id=client_id)
        except SocialApp.DoesNotExist:
            raise _login_error("not_allowed") from None

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
        if sociallogin.is_existing:
            return
        # 初めてのSSOのログインで、同じメールアドレスのユーザーがいれば紐付けて、役割・会話等を引き継ぐ。
        # テナント・ドメインを固定し、許可したドメインのメールアドレスだけを受け付けているので、
        # メールアドレスの一致を本人の確認とみなす（design.md §10.0）
        user = User.objects.filter(email__iexact=sociallogin.user.email).first()
        if user is None:
            return
        if not user.is_active:
            raise _login_error("not_allowed")
        logger.info(
            "SSOのアカウントを既存のユーザーに紐付けました: provider=%s user=%s", sociallogin.account.provider, user.uid
        )
        sociallogin.connect(request, user)

    def populate_user(self, request: HttpRequest, sociallogin: SocialLogin, data: dict):
        user = super().populate_user(request, sociallogin, data)
        extra = sociallogin.account.extra_data
        user.display_name = (extra.get("displayName") or extra.get("name") or "")[:150]
        return user

    def on_authentication_error(self, request, provider, error=None, exception=None, extra_context=None) -> None:
        logger.warning("SSOのログインに失敗しました: provider=%s error=%s", provider.id, error, exc_info=exception)
        raise _login_error("failed")
