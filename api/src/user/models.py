from typing import ClassVar

from auditlog.registry import auditlog
from django.contrib.auth.models import AbstractUser, Permission, UserManager
from django.db import models
from django.utils.translation import gettext_lazy as _

from utils.fields import NanoidField
from utils.permissions import AppPermission, to_perm_str


class UserQuerySet(models.QuerySet):
    def with_permission(self, perm: AppPermission | str) -> "UserQuerySet":
        """permissionを持つ（直接・Group経由・基盤管理者）有効なユーザー。通知の宛先などに使う"""
        app_label, codename = to_perm_str(perm).split(".", 1)
        permission = Permission.objects.get(content_type__app_label=app_label, codename=codename)
        return self.filter(
            models.Q(user_permissions=permission)
            | models.Q(groups__permissions=permission)
            | models.Q(is_superuser=True),
            is_active=True,
        ).distinct()


# mypyは from_queryset() で作ったクラスを基底クラスとして解釈できないので、この行だけ型検査を外す
class YorikiUserManager(UserManager.from_queryset(UserQuerySet)):  # type: ignore[misc]
    pass


class User(AbstractUser):
    """社員のアカウント。SSOで初めてログインした時点で作成し、通常社員として始める。

    基盤管理者は `is_superuser` で表す（最初の1人は `createsuperuser` で作る）。
    業務管理者・Plugin公開者などの役割はDjangoのGroup（permissionの組）で表し、管理画面で割り当てる
    （`user/roles.py`、design.md §10.0）。権限の判定は、役割ではなく個々のpermissionで行う
    （`utils.permissions.AppPermission`）。
    """

    objects: ClassVar[YorikiUserManager] = YorikiUserManager()

    uid = NanoidField(unique=True)
    display_name = models.CharField(_("表示名"), max_length=150, blank=True)

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
