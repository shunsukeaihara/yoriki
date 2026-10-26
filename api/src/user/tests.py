import pytest
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount, SocialLogin
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.test import RequestFactory
from rest_framework.test import APIClient

from .adapters import SocialAccountAdapter, is_allowed_account
from .factories import DepartmentFactory, DepartmentMembershipFactory, SSOProviderFactory, UserFactory
from .models import (
    Department,
    DepartmentMembership,
    DepartmentRole,
    SSOKind,
    SSOProvider,
    User,
    covers_all_departments,
    is_sso_enabled,
)
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

    @pytest.mark.django_db
    def test_sync_creates_missing_permissions_first(self, monkeypatch):
        # post_migrateの順番で、参照するアプリのpermissionがまだ作られていない場合
        Permission.objects.filter(content_type__app_label="user", codename="view_user").delete()
        monkeypatch.setitem(ROLES, "試験用の役割", ["user.view_user"])
        sync_role_groups()
        group = Group.objects.get(name="試験用の役割")
        assert list(group.permissions.values_list("codename", flat=True)) == ["view_user"]

    @pytest.mark.django_db
    def test_skill_publisher_can_edit_and_publish_company_skills(self, app_user):
        # 役割「Plugin公開者」は「Skill公開者」に改めた（design.md 0.19）
        assert not Group.objects.filter(name="Plugin公開者").exists()
        app_user.groups.add(Group.objects.get(name="Skill公開者"))
        app_user = User.objects.get(pk=app_user.pk)
        assert app_user.has_perm("skill.can_edit_company_skills")
        assert app_user.has_perm("skill.can_publish_company_skills")


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


@pytest.fixture(name="sso_providers")
def fixture_sso_providers(db) -> list[SSOProvider]:
    return [
        SSOProviderFactory.create(kind=SSOKind.MICROSOFT),
        SSOProviderFactory.create(kind=SSOKind.GOOGLE),
    ]


class TestAuthProviders:
    @pytest.mark.django_db
    def test_list(self, api_client, sso_providers):
        SSOProviderFactory.create(kind=SSOKind.GOOGLE, deleted=True)
        res = api_client.get("/api/app/v1/auth/providers/")
        assert res.status_code == 200
        assert res.json() == {
            "providers": [
                {"id": "microsoft", "name": "Microsoft 365", "login_url": "/api/accounts/microsoft/login/"},
                {"id": "google", "name": "Google Workspace", "login_url": "/api/accounts/google/login/"},
            ],
            # SSOが有効なら、パスワードの欄は出さない
            "password_login": False,
        }

    @pytest.mark.django_db
    def test_inactive_provider_is_hidden(self, api_client, sso_providers):
        sso_providers[1].is_active = False
        sso_providers[1].save()
        providers = api_client.get("/api/app/v1/auth/providers/").json()["providers"]
        assert [p["id"] for p in providers] == ["microsoft"]
        assert is_sso_enabled()

    @pytest.mark.django_db
    def test_no_provider(self, api_client):
        assert api_client.get("/api/app/v1/auth/providers/").json() == {"providers": [], "password_login": True}
        assert not is_sso_enabled()


def _sociallogin(provider: str, email: str, **extra_data) -> SocialLogin:
    return SocialLogin(
        user=User(email=email),
        account=SocialAccount(provider=provider, uid="x", extra_data=extra_data),
    )


class TestAllowedAccount:
    @pytest.mark.django_db
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
            (_sociallogin("google", "taro@example.co.jp", hd="other.co.jp", email_verified=True), False),
            (_sociallogin("google", "taro@example.co.jp", hd="example.co.jp", email_verified=False), False),
            # 設定していない認証基盤
            (_sociallogin("github", "taro@example.co.jp"), False),
        ],
    )
    def test_is_allowed_account(self, sso_providers, login, allowed):
        assert is_allowed_account(login) is allowed

    @pytest.mark.django_db
    def test_inactive_or_deleted_provider_is_rejected(self, sso_providers):
        microsoft, google = sso_providers
        microsoft.is_active = False
        microsoft.save()
        google.deleted = True
        google.save()
        assert is_allowed_account(_sociallogin("microsoft", "taro@example.co.jp")) is False
        login = _sociallogin("google", "taro@example.co.jp", hd="example.co.jp", email_verified=True)
        assert is_allowed_account(login) is False

    @pytest.mark.django_db
    def test_pre_social_login_redirects_to_login_page(self, sso_providers):
        request = RequestFactory().get("/")
        with pytest.raises(ImmediateHttpResponse) as e:
            SocialAccountAdapter().pre_social_login(request, _sociallogin("microsoft", "taro@other.co.jp"))
        assert e.value.response.status_code == 302
        assert e.value.response["Location"] == "/login?error=not_allowed"


class TestConnectExistingUser:
    """SSOで初めてログインしたとき、同じメールアドレスのユーザーに紐付ける（design.md §10.0）"""

    def _login(self, email: str) -> SocialLogin:
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        login = _sociallogin("microsoft", email)
        SocialAccountAdapter().pre_social_login(request, login)
        return login

    @pytest.mark.django_db
    def test_connects_by_email_ignoring_case(self, sso_providers, app_user):
        app_user.email = "Taro@Example.co.jp"
        app_user.save()
        login = self._login("taro@example.co.jp")
        assert login.user == app_user
        assert SocialAccount.objects.get(provider="microsoft", uid="x").user == app_user

    @pytest.mark.django_db
    def test_new_user_when_no_match(self, sso_providers, app_user):
        login = self._login("hanako@example.co.jp")
        assert login.user.pk is None
        assert not SocialAccount.objects.exists()

    @pytest.mark.django_db
    def test_inactive_user_is_not_connected(self, sso_providers, app_user):
        app_user.is_active = False
        app_user.save()
        with pytest.raises(ImmediateHttpResponse):
            self._login(app_user.email)
        assert not SocialAccount.objects.exists()


class TestListApps:
    @pytest.mark.django_db
    def test_builds_apps_from_active_providers(self, sso_providers):
        google = sso_providers[1]
        apps = {app.provider: app for app in SocialAccountAdapter().list_apps(RequestFactory().get("/"))}
        assert set(apps) == {"microsoft", "google"}
        assert apps["microsoft"].secret == "test-secret-0123456789"
        assert apps["microsoft"].settings == {"tenant": "test-tenant"}
        assert apps["google"].settings == {"auth_params": {"hd": "example.co.jp"}}
        # 保存しない（allauthの SocialApp のテーブルに client secret を平文で残さない）
        assert apps["microsoft"].pk is None

        google.is_active = False
        google.save()
        apps_list = SocialAccountAdapter().list_apps(RequestFactory().get("/"), provider="google")
        assert apps_list == []

    @pytest.mark.django_db
    def test_login_redirects_to_tenant(self, api_client, sso_providers):
        res = api_client.post("/api/accounts/microsoft/login/")
        assert res.status_code == 302
        assert res["Location"].startswith("https://login.microsoftonline.com/test-tenant/oauth2/v2.0/authorize")
        assert "client_id=test-client" in res["Location"]

    @pytest.mark.django_db
    def test_login_of_unconfigured_provider_returns_to_login_page(self, api_client, sso_providers):
        sso_providers[0].is_active = False
        sso_providers[0].save()
        res = api_client.post("/api/accounts/microsoft/login/")
        assert res.status_code == 302
        assert res["Location"] == "/login?error=not_allowed"


SSO_PROVIDERS = "/api/v1/sso_providers/"


@pytest.fixture(name="admin_client")
def fixture_admin_client(api_client, platform_admin):
    api_client.force_login(platform_admin)
    return api_client


class TestSSOProviderAPI:
    @pytest.mark.django_db
    def test_only_platform_admin(self, api_client, app_user):
        assert api_client.get(SSO_PROVIDERS).status_code == 401
        api_client.force_login(app_user)
        assert api_client.get(SSO_PROVIDERS).status_code == 403

    @pytest.mark.django_db
    def test_create_encrypts_secret_and_never_returns_it(self, admin_client, platform_admin):
        res = admin_client.post(
            SSO_PROVIDERS,
            {
                "kind": "microsoft",
                "client_id": " client-1 ",
                "client_secret": "secret-abcdefgh1234",
                "tenant_id": "tenant-1",
                "allowed_email_domains": ["Example.co.jp", "@sub.example.co.jp", "example.co.jp"],
            },
            format="json",
        )
        assert res.status_code == 201, res.json()
        data = res.json()
        assert data["client_id"] == "client-1"
        assert data["has_client_secret"] is True
        assert data["client_secret_hint"] == "1234"
        assert "client_secret" not in data
        assert "secret-abcdefgh1234" not in res.content.decode()
        assert data["allowed_email_domains"] == ["example.co.jp", "sub.example.co.jp"]
        assert data["callback_url"] == "http://testserver/api/accounts/microsoft/login/callback/"

        sso = SSOProvider.objects.get(uid=data["id"])
        assert sso.created_by == platform_admin
        assert "secret-abcdefgh" not in sso.client_secret.ciphertext
        assert sso.client_secret.reveal() == "secret-abcdefgh1234"

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        ("body", "field"),
        [
            # Microsoftはテナントを固定する
            ({"kind": "microsoft", "client_secret": "s"}, "tenant_id"),
            (
                {"kind": "microsoft", "client_secret": "s", "tenant_id": "t", "hosted_domain": "example.co.jp"},
                "hosted_domain",
            ),
            # Googleはhdを確かめる
            ({"kind": "google", "client_secret": "s"}, "hosted_domain"),
            ({"kind": "google", "client_secret": "s", "hosted_domain": "example.co.jp", "tenant_id": "t"}, "tenant_id"),
            ({"kind": "google", "client_secret": "s", "hosted_domain": "https://example.co.jp"}, "hosted_domain"),
            ({"kind": "microsoft", "tenant_id": "t"}, "client_secret"),
        ],
    )
    def test_validation(self, admin_client, body, field):
        body = {"client_id": "c", "allowed_email_domains": ["example.co.jp"], **body}
        res = admin_client.post(SSO_PROVIDERS, body, format="json")
        assert res.status_code == 400
        assert field in res.json()

    @pytest.mark.django_db
    @pytest.mark.parametrize("domains", [[], [""], ["taro@example.co.jp"], ["example"]])
    def test_allowed_email_domains_are_required_and_checked(self, admin_client, domains):
        body = {"kind": "microsoft", "client_id": "c", "client_secret": "s", "tenant_id": "t"}
        res = admin_client.post(SSO_PROVIDERS, {**body, "allowed_email_domains": domains}, format="json")
        assert res.status_code == 400
        assert "allowed_email_domains" in res.json()

    @pytest.mark.django_db
    def test_one_per_kind_among_not_deleted(self, admin_client):
        existing = SSOProviderFactory.create(kind=SSOKind.MICROSOFT)
        body = {
            "kind": "microsoft",
            "client_id": "c",
            "client_secret": "s",
            "tenant_id": "t",
            "allowed_email_domains": ["example.co.jp"],
        }
        assert admin_client.post(SSO_PROVIDERS, body, format="json").status_code == 400
        assert admin_client.delete(f"{SSO_PROVIDERS}{existing.uid}/").status_code == 204
        assert admin_client.post(SSO_PROVIDERS, body, format="json").status_code == 201

    @pytest.mark.django_db
    def test_patch_keeps_secret_and_kind(self, admin_client):
        sso = SSOProviderFactory.create(kind=SSOKind.MICROSOFT)
        res = admin_client.patch(
            f"{SSO_PROVIDERS}{sso.uid}/", {"kind": "google", "tenant_id": "tenant-2"}, format="json"
        )
        assert res.status_code == 200, res.json()
        sso.refresh_from_db()
        assert sso.kind == SSOKind.MICROSOFT
        assert sso.tenant_id == "tenant-2"
        assert sso.client_secret.reveal() == "test-secret-0123456789"

        res = admin_client.patch(f"{SSO_PROVIDERS}{sso.uid}/", {"client_secret": "new-secret"}, format="json")
        assert res.status_code == 200
        sso.refresh_from_db()
        assert sso.client_secret.reveal() == "new-secret"

    @pytest.mark.django_db
    def test_patch_cannot_clear_tenant(self, admin_client):
        sso = SSOProviderFactory.create(kind=SSOKind.MICROSOFT)
        res = admin_client.patch(f"{SSO_PROVIDERS}{sso.uid}/", {"tenant_id": ""}, format="json")
        assert res.status_code == 400
        assert "tenant_id" in res.json()

    @pytest.mark.django_db
    def test_secret_requires_encryption_key(self, admin_client, settings):
        settings.YORIKI_SECRET_ENCRYPTION_KEYS = []
        body = {
            "kind": "microsoft",
            "client_id": "c",
            "client_secret": "s",
            "tenant_id": "t",
            "allowed_email_domains": ["example.co.jp"],
        }
        res = admin_client.post(SSO_PROVIDERS, body, format="json")
        assert res.status_code == 400
        assert "client_secret" in res.json()

    @pytest.mark.django_db
    def test_django_admin_is_read_only(self, api_client, platform_admin):
        # 管理画面のフォームには client secret が出ないので、作成・変更はYorikiの管理画面だけで行う
        sso = SSOProviderFactory.create()
        api_client.force_login(platform_admin)
        assert api_client.get("/api/admin/user/ssoprovider/add/").status_code == 403
        res = api_client.get(f"/api/admin/user/ssoprovider/{sso.pk}/change/")
        assert res.status_code == 200
        html = res.content.decode()
        assert "test-secret" not in html
        assert 'name="kind"' not in html
        assert 'name="_save"' not in html
        res = api_client.post(f"/api/admin/user/ssoprovider/{sso.pk}/change/", {"kind": "google", "is_active": ""})
        assert res.status_code == 403
        sso.refresh_from_db()
        assert sso.kind == SSOKind.MICROSOFT
        assert sso.is_active


class TestRemovedSSOEnvCheck:
    def test_warns_when_old_env_remains(self, monkeypatch):
        from .checks import check_removed_sso_env  # noqa: PLC0415

        monkeypatch.delenv("YORIKI_SSO_PROVIDERS", raising=False)
        assert check_removed_sso_env(None) == []
        monkeypatch.setenv("YORIKI_SSO_PROVIDERS", "microsoft")
        warnings = check_removed_sso_env(None)
        assert [w.id for w in warnings] == ["user.W001"]
        assert "YORIKI_SSO_PROVIDERS" in warnings[0].msg


class TestDepartmentAPI:
    url = "/api/v1/departments/"

    @pytest.mark.django_db
    def test_only_platform_admin(self, api_client, app_user):
        DepartmentMembershipFactory.create(user=app_user, role=DepartmentRole.PUBLISHER)
        api_client.force_login(app_user)
        assert api_client.get(self.url).status_code == 403
        assert api_client.post(self.url, {"name": "経理部"}, format="json").status_code == 403

    @pytest.mark.django_db
    def test_create_and_assign(self, api_client, platform_admin, app_user):
        api_client.force_login(platform_admin)
        res = api_client.post(self.url, {"name": " 経理部 "}, format="json")
        assert res.status_code == 201, res.json()
        assert res.json()["name"] == "経理部"
        assert res.json()["member_count"] == 0
        assert api_client.post(self.url, {"name": "経理部"}, format="json").status_code == 400
        members = f"{self.url}{res.json()['id']}/members/"

        res = api_client.post(members, {"user": app_user.uid, "role": DepartmentRole.EDITOR}, format="json")
        assert res.status_code == 201, res.json()
        assert res.json()["user_info"]["email"] == app_user.email
        assert api_client.post(members, {"user": app_user.uid, "role": 1}, format="json").status_code == 400
        assert api_client.get(self.url).json()["results"][0]["member_count"] == 1

        # 所属の社員は変えず、役割だけを変える
        other = UserFactory.create()
        res = api_client.patch(f"{members}{res.json()['id']}/", {"user": other.uid, "role": 3}, format="json")
        assert res.status_code == 200
        membership = DepartmentMembership.objects.get()
        assert membership.user == app_user
        assert membership.role == DepartmentRole.PUBLISHER

    @pytest.mark.django_db
    def test_covers_all_departments(self, api_client, platform_admin, app_user):
        api_client.force_login(platform_admin)
        res = api_client.post(self.url, {"name": "監査室", "covers_all_departments": True}, format="json")
        assert res.status_code == 201, res.json()
        assert res.json()["covers_all_departments"] is True
        sales = DepartmentFactory.create(name="営業部")
        assert covers_all_departments(app_user) is False
        assert list(Department.objects.of(app_user)) == []
        DepartmentMembershipFactory.create(
            user=app_user, department=Department.objects.get(uid=res.json()["id"]), role=DepartmentRole.MEMBER
        )
        # 印のある部門の所属者は、すべての部門に所属しているものとして扱う
        assert covers_all_departments(app_user) is True
        assert sales in set(Department.objects.of(app_user))

    @pytest.mark.django_db
    def test_my_departments(self, api_client, app_user):
        DepartmentMembershipFactory.create(
            user=app_user, department=DepartmentFactory.create(name="営業部"), role=DepartmentRole.EDITOR
        )
        DepartmentMembershipFactory.create(user=app_user, department=DepartmentFactory.create(deleted=True))
        api_client.force_login(app_user)
        departments = api_client.get("/api/app/v1/my/").json()["departments"]
        assert [(d["name"], d["role"]) for d in departments] == [("営業部", DepartmentRole.EDITOR)]
