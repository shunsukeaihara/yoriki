import pytest
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount, SocialLogin
from django.contrib.auth.models import Group, Permission
from django.test import RequestFactory
from rest_framework.test import APIClient

from .adapters import SocialAccountAdapter, is_allowed_account
from .factories import UserFactory
from .models import User
from .roles import ROLES, sync_role_groups


class TestAuthSession:
    url = "/api/app/v1/auth/session/"

    @pytest.mark.django_db
    def test_anonymous_gets_csrf_cookie(self, api_client):
        res = api_client.get(self.url)
        assert res.status_code == 200
        assert res.json() == {"is_authenticated": False}
        assert "csrftoken" in res.cookies

    @pytest.mark.django_db
    def test_authenticated(self, api_client, app_user):
        api_client.force_login(app_user)
        res = api_client.get(self.url)
        assert res.json() == {"is_authenticated": True}

    @pytest.mark.django_db
    def test_logout(self, api_client, app_user):
        api_client.force_login(app_user)
        assert api_client.delete(self.url).status_code == 204
        assert api_client.get("/api/app/v1/my/").status_code == 401

    @pytest.mark.django_db
    def test_logout_requires_csrf(self, app_user):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(app_user)
        assert client.delete(self.url).status_code == 403


class TestMyUser:
    url = "/api/app/v1/my/"

    @pytest.mark.django_db
    def test_anonymous_is_401(self, api_client):
        res = api_client.get(self.url)
        assert res.status_code == 401
        assert "WWW-Authenticate" in res.headers

    @pytest.mark.django_db
    def test_returns_user_and_permissions(self, api_client, app_user):
        app_user.user_permissions.add(Permission.objects.get(codename="view_user"))
        api_client.force_login(app_user)
        res = api_client.get(self.url)
        assert res.status_code == 200
        data = res.json()
        assert data["id"] == app_user.uid
        assert data["email"] == app_user.email
        assert data["is_platform_admin"] is False
        assert data["all_permissions"] == ["user.view_user"]

    @pytest.mark.django_db
    def test_inactive_user_is_rejected(self, api_client, app_user):
        api_client.force_login(app_user)
        User.objects.filter(pk=app_user.pk).update(is_active=False)
        assert api_client.get(self.url).status_code in (401, 403)


class TestRoleGroups:
    @pytest.mark.django_db
    def test_role_groups_are_created(self):
        assert set(Group.objects.filter(name__in=ROLES).values_list("name", flat=True)) == set(ROLES)

    @pytest.mark.django_db
    def test_sync_adds_permissions_and_keeps_manual_ones(self, monkeypatch):
        group = Group.objects.get(name="業務管理者")
        manual = Permission.objects.get(codename="change_user")
        group.permissions.add(manual)
        monkeypatch.setitem(ROLES, "業務管理者", ["user.view_user"])
        sync_role_groups()
        assert set(group.permissions.values_list("codename", flat=True)) == {"view_user", "change_user"}


class TestWithPermission:
    @pytest.mark.django_db
    def test_direct_group_and_superuser(self, app_user, other_user, platform_admin):
        view_user = Permission.objects.get(codename="view_user")
        app_user.user_permissions.add(view_user)
        group = Group.objects.create(name="閲覧")
        group.permissions.add(view_user)
        other_user.groups.add(group)
        inactive = UserFactory.create(is_active=False, is_superuser=True)
        UserFactory.create()  # 権限なし

        users = set(User.objects.with_permission("user.view_user"))
        assert users == {app_user, other_user, platform_admin}
        assert inactive not in users


class TestAuthProviders:
    @pytest.mark.django_db
    def test_list(self, api_client):
        res = api_client.get("/api/app/v1/auth/providers/")
        assert res.status_code == 200
        assert res.json() == [
            {"id": "microsoft", "name": "Microsoft 365", "login_url": "/api/accounts/microsoft/login/"},
            {"id": "google", "name": "Google Workspace", "login_url": "/api/accounts/google/login/"},
        ]


def _sociallogin(provider: str, email: str, **extra_data) -> SocialLogin:
    return SocialLogin(
        user=User(email=email),
        account=SocialAccount(provider=provider, uid="x", extra_data=extra_data),
    )


class TestAllowedAccount:
    @pytest.mark.parametrize(
        ("login", "allowed"),
        [
            (_sociallogin("microsoft", "taro@example.co.jp"), True),
            (_sociallogin("microsoft", "TARO@EXAMPLE.CO.JP"), True),
            (_sociallogin("microsoft", "taro@other.co.jp"), False),
            (_sociallogin("microsoft", ""), False),
            (_sociallogin("google", "taro@example.co.jp", hd="example.co.jp", email_verified=True), True),
            # 個人のGoogleアカウント（hdがない）は受け付けない
            (_sociallogin("google", "taro@example.co.jp", email_verified=True), False),
            (_sociallogin("google", "taro@example.co.jp", hd="example.co.jp", email_verified=False), False),
            # 有効にしていない認証基盤
            (_sociallogin("github", "taro@example.co.jp"), False),
        ],
    )
    def test_is_allowed_account(self, login, allowed):
        assert is_allowed_account(login) is allowed

    def test_google_without_hosted_domain_setting_is_rejected(self, settings):
        settings.YORIKI_SSO_GOOGLE_HOSTED_DOMAIN = ""
        login = _sociallogin("google", "taro@example.co.jp", hd="example.co.jp", email_verified=True)
        assert is_allowed_account(login) is False

    def test_pre_social_login_redirects_to_login_page(self):
        request = RequestFactory().get("/")
        with pytest.raises(ImmediateHttpResponse) as e:
            SocialAccountAdapter().pre_social_login(request, _sociallogin("microsoft", "taro@other.co.jp"))
        assert e.value.response.status_code == 302
        assert e.value.response["Location"] == "/login?error=not_allowed"
