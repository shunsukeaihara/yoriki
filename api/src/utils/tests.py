import pytest
from django.apps import apps
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory

from llm.factories import LLMConnectionFactory
from user.factories import SSOProviderFactory, UserFactory

from .backends import PasswordBackend
from .crypto import encrypt_secret
from .fields import ENCRYPTED_SECRET_FIELDS
from .lock import lock
from .permissions import AppUser, PlatformAdmin, require_permission


@pytest.mark.django_db
def test_ping(api_client):
    res = api_client.get("/api/ping/")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def _allowed(permission, user) -> bool:
    request = RequestFactory().get("/")
    request.user = user
    return permission().has_permission(request, None)


class TestPermissions:
    @pytest.mark.django_db
    @pytest.mark.parametrize(
        ("user_kwargs", "expected"),
        [
            # AppUser, PlatformAdmin
            ({}, (True, False)),
            ({"is_superuser": True}, (True, True)),
            ({"is_active": False}, (False, False)),
        ],
    )
    def test_app_user_and_platform_admin(self, user_kwargs, expected):
        user = UserFactory.create(**user_kwargs)
        assert (_allowed(AppUser, user), _allowed(PlatformAdmin, user)) == expected

    @pytest.mark.parametrize("permission", [AppUser, PlatformAdmin, require_permission("user.view_user")])
    def test_anonymous_is_denied(self, permission):
        assert _allowed(permission, AnonymousUser()) is False

    @pytest.mark.django_db
    def test_require_permission_by_user_permission(self, app_user):
        permission = require_permission("user.view_user")
        assert _allowed(permission, app_user) is False
        app_user.user_permissions.add(Permission.objects.get(codename="view_user"))
        app_user = type(app_user).objects.get(pk=app_user.pk)  # permissionのキャッシュを捨てる
        assert _allowed(permission, app_user) is True

    @pytest.mark.django_db
    def test_require_permission_by_group(self, app_user):
        # 役割（Group）に付けたpermissionでも許可される
        group = Group.objects.create(name="テスト用の役割")
        group.permissions.add(Permission.objects.get(codename="view_user"))
        app_user.groups.add(group)
        assert _allowed(require_permission("user.view_user"), app_user) is True

    @pytest.mark.django_db
    def test_platform_admin_has_every_permission(self, platform_admin):
        assert _allowed(require_permission("user.view_user"), platform_admin) is True


class TestPasswordBackend:
    @pytest.mark.django_db
    def test_sso_limits_password_login_to_platform_admin(self):
        users = (UserFactory.create(username="admin", is_superuser=True), UserFactory.create(username="taro"))
        for user in users:
            user.set_password("pass-for-test")
            user.save()
        backend = PasswordBackend()
        # ユーザー名（Django管理画面）とメールアドレス（Yorikiのログイン画面）のどちらでもよい
        assert backend.authenticate(None, username="taro", password="pass-for-test") == users[1]
        assert backend.authenticate(None, username="TARO@example.co.jp", password="pass-for-test") == users[1]
        assert backend.authenticate(None, username="taro", password="wrong") is None

        SSOProviderFactory.create()
        assert backend.authenticate(None, username="admin", password="pass-for-test") == users[0]
        assert backend.authenticate(None, username="taro", password="pass-for-test") is None

    @pytest.mark.django_db
    def test_ambiguous_email_is_rejected(self):
        for name in ("a", "b"):
            user = UserFactory.create(username=name, email="same@example.co.jp")
            user.set_password("pass-for-test")
            user.save()
        assert PasswordBackend().authenticate(None, username="same@example.co.jp", password="pass-for-test") is None


class TestAdminSite:
    @pytest.mark.django_db
    def test_only_platform_admin(self, api_client, app_user, platform_admin):
        for user, status in ((app_user, 302), (platform_admin, 200)):
            api_client.force_login(user)
            assert api_client.get("/api/admin/").status_code == status


@pytest.mark.django_db
def test_lock_can_be_acquired_twice_in_sequence():
    for _ in range(2):
        with lock("test-lock", timeout=1):
            pass


class TestSecretField:
    """EncryptedSecretField と SecretValue（llm.models.LLMConnection の api_key で確かめる）"""

    @pytest.mark.django_db
    def test_assignment_encrypts_and_never_shows_plaintext(self):
        connection = LLMConnectionFactory.build(api_key="sk-0123456789abcd")
        assert "sk-0123" not in connection.api_key.ciphertext
        assert str(connection.api_key) == "****abcd"
        assert "sk-0123" not in repr(connection.api_key)
        assert connection.api_key.reveal() == "sk-0123456789abcd"
        connection.api_key = ""
        assert not connection.api_key
        assert str(connection.api_key) == ""

    def test_invalid_key_is_improperly_configured(self, settings):
        settings.YORIKI_SECRET_ENCRYPTION_KEYS = ["not-a-fernet-key"]
        with pytest.raises(ImproperlyConfigured):
            encrypt_secret("x")


@pytest.mark.django_db
def test_encrypted_secret_fields_are_current_models():
    # マイグレーションを当てた後でも、鍵の入れ替えの対象は今のモデルだけ（過去の形のモデルを含めない）
    assert ENCRYPTED_SECRET_FIELDS
    for model, _name in ENCRYPTED_SECRET_FIELDS:
        assert apps.get_model(model._meta.label) is model
