import hashlib
import secrets
from datetime import timedelta
from typing import ClassVar

from auditlog.registry import auditlog
from django.conf import settings
from django.contrib.auth.models import AbstractUser, Permission, UserManager
from django.contrib.postgres.fields import ArrayField
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from utils.fields import EncryptedSecretField, NanoidField
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
    同じメールアドレスのユーザーがいれば、SSOのアカウントをそのユーザーに紐付ける（user/adapters.py）。

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


class SSOKind(models.TextChoices):
    """SSOの認証基盤。値はdjango-allauthのproviderのID（外部仕様の文字列）"""

    MICROSOFT = "microsoft", _("Microsoft 365")
    GOOGLE = "google", _("Google Workspace")


class SSOProviderQuerySet(models.QuerySet):
    def active(self) -> "SSOProviderQuerySet":
        return self.filter(deleted=False, is_active=True)


class SSOProvider(models.Model):
    """社内SSOの設定。基盤管理者が管理画面で登録する（design.md §10.0）。

    有効な設定が1つでもあれば、社員はSSOだけでログインする。種別は作成した後は変えない（更新用のSerializerと
    管理画面に項目を置かない）。client secretは暗号化して保存し、django-allauthへ渡すときだけ復号する
    （user/adapters.py の list_apps()）。allauthの SocialApp のテーブルは client secret が平文なので使わない。
    """

    uid = NanoidField(unique=True)
    kind = models.CharField(_("種別"), max_length=20, choices=SSOKind.choices)
    client_id = models.CharField(_("client ID"), max_length=500)
    client_secret = EncryptedSecretField(_("client secret"))
    # Microsoft: 自社のテナントを固定し、他のテナントのアカウントを受け付けない
    tenant_id = models.CharField(_("テナントID"), max_length=200, blank=True)
    # Google: IDトークンの hd がこのドメインと一致するアカウントだけを受け付ける（個人のGoogleアカウントを除く）
    hosted_domain = models.CharField(_("Workspaceのドメイン"), max_length=253, blank=True)
    allowed_email_domains = ArrayField(
        models.CharField(max_length=253), verbose_name=_("許可するメールアドレスのドメイン"), default=list
    )
    is_active = models.BooleanField(_("有効"), default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("作成者")
    )

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True)
    deleted = models.BooleanField(default=False)

    objects = SSOProviderQuerySet.as_manager()

    class Meta:
        verbose_name = _("SSOの設定")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=["kind"], condition=models.Q(deleted=False), name="uniq_sso_provider_kind"),
        ]

    def __str__(self):
        return self.get_kind_display()

    def allows_email(self, email: str) -> bool:
        domain = email.lower().rpartition("@")[2]
        return bool(domain) and domain in self.allowed_email_domains

    def app_settings(self) -> dict:
        """django-allauthの SocialApp.settings に渡す、認証基盤ごとの設定"""
        if self.kind == SSOKind.MICROSOFT:
            return {"tenant": self.tenant_id}
        if self.kind == SSOKind.GOOGLE:
            return {"auth_params": {"hd": self.hosted_domain}}
        return {}


def is_sso_enabled() -> bool:
    """有効なSSOの設定があるか。あれば、社員はSSOだけでログインする（design.md §10.0）"""
    return SSOProvider.objects.active().exists()


def can_use_password(user: User) -> bool:
    """パスワードでログインしてよいか。SSOが有効な間は基盤管理者だけ（設定の誤りや認証基盤の停止に備える）。

    ログイン（utils.backends.PasswordBackend）と、招待・再設定のリンクの発行・使用で同じ規則を使う
    """
    return user.is_superuser or not is_sso_enabled()


# 招待・再設定のリンクの有効期限
INVITATION_TTL = timedelta(days=7)


class InvitationPurpose(models.IntegerChoices):
    INVITE = 1, _("招待")
    RESET = 2, _("パスワードの再設定")


class InvitationQuerySet(models.QuerySet):
    def open(self) -> "InvitationQuerySet":
        """使用済みでも無効でもないもの（期限切れを含む）。ユーザーごとに1つまで（uniq_open_invitation_per_user）"""
        return self.filter(used_at__isnull=True, revoked_at__isnull=True)

    def usable(self) -> "InvitationQuerySet":
        """使用済み・無効・期限切れでないもの"""
        return self.open().filter(expires_at__gt=timezone.now())


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Invitation(models.Model):
    """Yorikiのアカウントの招待・パスワードの再設定のリンク（design.md §10.0）。

    基盤管理者が発行し、画面に一度だけ表示したURLを本人に渡す。tokenはDBにハッシュだけを保存する。
    期限付きで1回だけ使え、新しいリンクを発行すると同じユーザーの未使用のリンクは無効になる。
    SSOが有効な間は基盤管理者のアカウントにしか使えず、SSOを有効にしたときに他の未使用のリンクを無効にする
    """

    uid = NanoidField(unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="invitations", verbose_name=_("ユーザー")
    )
    purpose = models.PositiveSmallIntegerField(_("用途"), choices=InvitationPurpose.choices)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField(_("有効期限"))
    used_at = models.DateTimeField(_("使用日時"), null=True, blank=True)
    revoked_at = models.DateTimeField(_("無効にした日時"), null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("発行者")
    )

    created = models.DateTimeField(auto_now_add=True)

    objects = InvitationQuerySet.as_manager()

    class Meta:
        verbose_name = _("招待のリンク")
        verbose_name_plural = verbose_name
        constraints = [
            # 新しいリンクを発行したら前のリンクは使えない、をDBでも守らせる
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(used_at__isnull=True, revoked_at__isnull=True),
                name="uniq_open_invitation_per_user",
            ),
        ]

    def __str__(self):
        return f"{self.user} {self.get_purpose_display()}"

    @classmethod
    @transaction.atomic
    def issue(cls, *, user: User, actor: User) -> tuple["Invitation", str]:
        """リンクを発行し、（招待、token）を返す。tokenはこの戻り値でしか分からない。

        同じユーザーへの発行が同時に来ても、前のリンクの無効化と作成を1つずつ行うよう、ユーザーの行をロックする
        """
        User.objects.select_for_update().filter(pk=user.pk).first()
        cls.objects.open().filter(user=user).update(revoked_at=timezone.now())
        token = secrets.token_urlsafe(32)
        invitation = cls.objects.create(
            user=user,
            # まだパスワードを設定していなければ招待、設定済みなら再設定
            purpose=InvitationPurpose.RESET if user.has_usable_password() else InvitationPurpose.INVITE,
            token_hash=_hash_token(token),
            expires_at=timezone.now() + INVITATION_TTL,
            created_by=actor,
        )
        return invitation, token

    @classmethod
    def find_usable(cls, token: str, *, for_update: bool = False) -> "Invitation | None":
        queryset = cls.objects.usable().select_related("user")
        if for_update:
            queryset = queryset.select_for_update(of=("self",))
        return queryset.filter(token_hash=_hash_token(token)).first()

    @classmethod
    def revoke_for_sso(cls) -> int:
        """SSOを有効にしたときに、基盤管理者以外の未使用のリンクを無効にする"""
        return cls.objects.open().filter(user__is_superuser=False).update(revoked_at=timezone.now())

    @transaction.atomic
    def accept(self, password: str) -> User:
        """パスワードを設定してリンクを使用済みにする。同じユーザーの他の未使用のリンクも無効にする"""
        now = timezone.now()
        user = self.user
        user.set_password(password)
        user.save(update_fields=["password", "updated"])
        self.used_at = now
        self.save(update_fields=["used_at"])
        Invitation.objects.open().filter(user=user).exclude(pk=self.pk).update(revoked_at=now)
        return user


auditlog.register(User, exclude_fields=["password", "last_login", "updated"])
# client secretは暗号化した値でも監査ログに残さない
auditlog.register(SSOProvider, exclude_fields=["client_secret", "updated"])
auditlog.register(Invitation, exclude_fields=["token_hash"])
