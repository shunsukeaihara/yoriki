from auditlog.registry import auditlog
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _

from utils.fields import NanoidField


class User(AbstractUser):
    """社員のアカウント。SSOで初めてログインした時点で作成し、通常社員の役割から始める。

    基盤管理者は `is_superuser` で表す（最初の1人は `createsuperuser` で作る）。
    業務管理者・Plugin公開者の役割は管理画面で割り当てる（design.md §10.0）。
    """

    uid = NanoidField(unique=True)
    display_name = models.CharField(_("表示名"), max_length=150, blank=True)
    is_business_admin = models.BooleanField(_("業務管理者"), default=False)
    is_plugin_publisher = models.BooleanField(_("Plugin公開者"), default=False)

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("ユーザー")
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.display_name or self.email or self.username

    @property
    def is_platform_admin(self) -> bool:
        return self.is_superuser


auditlog.register(User, exclude_fields=["password", "last_login", "updated"])
