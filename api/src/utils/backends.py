from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q
from django.http import JsonResponse
from django.utils.translation import gettext_lazy as _

LOCKOUT_MESSAGE = _("ログインの失敗が続いたため、しばらくログインできません。時間をおいてからお試しください。")


class PasswordBackend(ModelBackend):
    """パスワードでのログイン（backend-conventions.md §6.2）。

    有効なSSOの設定があれば基盤管理者だけに、なければ有効なユーザーすべてに許可する（`user.models.can_use_password`）。
    `username` にはメールアドレス（Yorikiのログイン画面）かユーザー名（Django管理画面）を受け付ける。
    試行回数の制限は、この前に置いた django-axes のバックエンドが行う
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        from user.models import can_use_password  # noqa: PLC0415  Modelの読み込み後に使う

        if not username or password is None:
            return None
        User = get_user_model()
        users = list(User._default_manager.filter(Q(username=username) | Q(email__iexact=username))[:2])
        if len(users) != 1:
            # 該当するユーザーがいない場合も、パスワードのハッシュの計算で時間をそろえる
            User().set_password(password)
            return None
        user = users[0]
        if not user.check_password(password) or not self.user_can_authenticate(user):
            return None
        if not can_use_password(user):
            return None
        return user


def axes_username(request, credentials) -> str | None:
    """django-axes が数えるアカウントの名前。大文字・小文字を変えて制限を逃れられないようにする"""
    username = (credentials or {}).get("username")
    return username.strip().lower() if isinstance(username, str) else None


def client_ip(request) -> str | None:
    """送信元のIPアドレス。nginxの set_real_ip_from とUvicornの --proxy-headers で利用者のIPアドレスになる
    （deployment.md §8）。X-Forwarded-For を直接は読まない（偽装できるため）
    """
    return request.META.get("REMOTE_ADDR")


def lockout_response(request, original_response=None, credentials=None) -> JsonResponse:
    """django-axes がログインを止めたときの応答。DRFのエラーと同じ形（detail）のJSONにする"""
    return JsonResponse({"detail": str(LOCKOUT_MESSAGE)}, status=429)
