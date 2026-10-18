import pytest
from django.contrib.auth import authenticate
from django.contrib.auth.models import AnonymousUser, Permission
from django.test import RequestFactory

from user.factories import UserFactory

from .lock import lock
from .permissions import AppUser, BusinessAdmin, PlatformAdmin, PluginPublisher, require_permission


@pytest.mark.django_db
def test_ping(api_client):
    res = api_client.get("/api/ping/")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def _allowed(permission, user) -> bool:
    request = RequestFactory().get("/")
    request.user = user
    return permission().has_permission(request, None)


class TestRolePermissions:
    @pytest.mark.django_db
    @pytest.mark.parametrize(
        ("user_kwargs", "expected"),
        [
            # AppUser, BusinessAdmin, PluginPublisher, PlatformAdmin
            ({}, (True, False, False, False)),
            ({"is_business_admin": True}, (True, True, False, False)),
            ({"is_plugin_publisher": True}, (True, False, True, False)),
            ({"is_superuser": True}, (True, True, True, True)),
            ({"is_active": False, "is_superuser": True}, (False, True, True, True)),
        ],
    )
    def test_roles(self, user_kwargs, expected):
        user = UserFactory.create(**user_kwargs)
        actual = tuple(_allowed(p, user) for p in (AppUser, BusinessAdmin, PluginPublisher, PlatformAdmin))
        assert actual == expected

    @pytest.mark.parametrize("permission", [AppUser, BusinessAdmin, PluginPublisher, PlatformAdmin])
    def test_anonymous_is_denied(self, permission):
        assert _allowed(permission, AnonymousUser()) is False

    @pytest.mark.django_db
    def test_require_permission(self, app_user):
        permission = require_permission("user.view_user")
        assert _allowed(permission, app_user) is False
        app_user.user_permissions.add(Permission.objects.get(codename="view_user"))
        app_user = type(app_user).objects.get(pk=app_user.pk)  # permissionのキャッシュを捨てる
        assert _allowed(permission, app_user) is True


class TestPasswordLogin:
    @pytest.mark.django_db
    def test_only_platform_admin_can_login_with_password(self):
        for user in (UserFactory.create(username="admin", is_superuser=True), UserFactory.create(username="taro")):
            user.set_password("pass-for-test")
            user.save()
        assert authenticate(username="admin", password="pass-for-test") is not None
        assert authenticate(username="taro", password="pass-for-test") is None


class TestAdminSite:
    @pytest.mark.django_db
    def test_only_platform_admin(self, api_client, app_user, business_admin, platform_admin):
        for user, status in ((app_user, 302), (business_admin, 302), (platform_admin, 200)):
            api_client.force_login(user)
            assert api_client.get("/api/admin/").status_code == status


@pytest.mark.django_db
def test_lock_can_be_acquired_twice_in_sequence():
    for _ in range(2):
        with lock("test-lock", timeout=1):
            pass
