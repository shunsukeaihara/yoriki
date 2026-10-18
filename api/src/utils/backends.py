from django.contrib.auth.backends import ModelBackend


class PlatformAdminPasswordBackend(ModelBackend):
    """パスワードでのログインを基盤管理者だけに許可する。

    社員はSSO（django-allauth）でログインする。SSOの設定前でも管理画面へ入れるよう、
    `createsuperuser` で作った基盤管理者だけはパスワードでログインできる（backend-conventions.md §6.2）。
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        user = super().authenticate(request, username=username, password=password, **kwargs)
        if user is None or not user.is_superuser:
            return None
        return user
