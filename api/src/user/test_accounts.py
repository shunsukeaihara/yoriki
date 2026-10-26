"""Yorikiのアカウント（design.md §10.0）。

パスワードでのログイン、試行回数の制限、招待・再設定のリンク、ユーザーの管理
"""

import threading
import time
from datetime import timedelta

import pytest
from django.contrib.auth.models import Group, Permission
from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from .factories import SSOProviderFactory, UserFactory, grant
from .models import Invitation, InvitationPurpose, User, lock_accounts

SESSION = "/api/app/v1/auth/session/"
VERIFY = "/api/app/v1/auth/invitations/verify/"
ACCEPT = "/api/app/v1/auth/invitations/accept/"
USERS = "/api/v1/users/"
PASSWORD = "correct-horse-battery-staple"


def _login(client: APIClient, email: str, password: str = PASSWORD, ip: str = "203.0.113.1"):
    return client.post(SESSION, {"email": email, "password": password}, format="json", REMOTE_ADDR=ip)


@pytest.fixture(name="password_user")
def fixture_password_user(db) -> User:
    user = UserFactory.create(email="taro@example.co.jp")
    user.set_password(PASSWORD)
    user.save()
    return user


@pytest.fixture(name="admin_client")
def fixture_admin_client(api_client, platform_admin):
    api_client.force_login(platform_admin)
    return api_client


class TestPasswordLogin:
    @pytest.mark.django_db
    def test_login_without_sso(self, api_client, password_user):
        res = _login(api_client, "TARO@example.co.jp")
        assert res.status_code == 200, res.json()
        assert res.json() == {"is_authenticated": True}
        assert api_client.get("/api/app/v1/my/").json()["id"] == password_user.uid

    @pytest.mark.django_db
    def test_wrong_password_and_unknown_user(self, api_client, password_user):
        for email, password in [("taro@example.co.jp", "wrong"), ("nobody@example.co.jp", PASSWORD)]:
            res = _login(api_client, email, password)
            assert res.status_code == 400
            assert res.json()["non_field_errors"] == ["メールアドレスかパスワードが正しくありません。"]
        assert api_client.get("/api/app/v1/my/").status_code == 401

    @pytest.mark.django_db
    def test_inactive_user(self, api_client, password_user):
        password_user.is_active = False
        password_user.save()
        assert _login(api_client, password_user.email).status_code == 400

    @pytest.mark.django_db
    def test_only_platform_admin_while_sso_is_active(self, api_client, password_user):
        SSOProviderFactory.create()
        assert _login(api_client, password_user.email).status_code == 400
        admin = UserFactory.create(is_superuser=True, is_staff=True)
        admin.set_password(PASSWORD)
        admin.save()
        assert _login(api_client, admin.email).status_code == 200

    @pytest.mark.django_db
    def test_inactive_sso_does_not_block(self, api_client, password_user):
        SSOProviderFactory.create(is_active=False)
        assert _login(api_client, password_user.email).status_code == 200

    @pytest.mark.django_db
    def test_requires_csrf(self, password_user):
        client = APIClient(enforce_csrf_checks=True)
        assert _login(client, password_user.email).status_code == 403

    @pytest.mark.django_db
    def test_django_admin_login_follows_the_same_rule(self, api_client, platform_admin):
        platform_admin.set_password(PASSWORD)
        platform_admin.save()
        SSOProviderFactory.create()
        staff = UserFactory.create(is_staff=True)
        staff.set_password(PASSWORD)
        staff.save()
        # Django管理画面のログインも、SSOが有効な間は基盤管理者だけ
        assert _admin_login(api_client, staff.username).status_code == 200
        assert _admin_login(api_client, platform_admin.username).status_code == 302


def _admin_login(client: APIClient, username: str, password: str = PASSWORD, ip: str = "203.0.113.1"):
    # 成功するとリダイレクト（302）、失敗するとログイン画面（200）、止めると429
    return client.post("/api/admin/login/", {"username": username, "password": password}, REMOTE_ADDR=ip)


class TestLockout:
    """失敗はアカウントごとと送信元のIPアドレスごとに別々に数える（AXES_FAILURE_LIMIT = 10）"""

    @pytest.mark.django_db
    def test_account_is_locked_across_ips(self, api_client, password_user):
        for i in range(9):
            assert _login(api_client, password_user.email, "wrong", ip=f"198.51.100.{i}").status_code == 400
        # 10回目の失敗で止める。応答はDRFのエラーと同じ形のJSON
        res = _login(api_client, password_user.email, "wrong", ip="198.51.100.9")
        assert res.status_code == 429
        assert res.json()["detail"].startswith("ログインの失敗が続いたため")
        # 別のIPアドレスから、正しいパスワードでも止める（多数のIPアドレスからの推測を防ぐ）
        res = _login(api_client, password_user.email, ip="192.0.2.99")
        assert res.status_code == 429
        assert "しばらくログインできません" in res.json()["detail"]
        # 大文字・小文字を変えても同じアカウントとして数える
        assert _login(api_client, password_user.email.upper(), ip="192.0.2.100").status_code == 429

    @pytest.mark.django_db
    def test_ip_is_locked_across_accounts(self, api_client, password_user):
        for i in range(10):
            _login(api_client, f"user{i}@example.co.jp", "wrong", ip="198.51.100.7")
        assert _login(api_client, password_user.email, ip="198.51.100.7").status_code == 429
        assert _login(api_client, password_user.email, ip="198.51.100.8").status_code == 200

    @pytest.mark.django_db
    def test_lock_expires(self, api_client, password_user):
        from axes.models import AccessAttempt  # noqa: PLC0415

        for i in range(10):
            _login(api_client, password_user.email, "wrong", ip=f"198.51.100.{i}")
        assert _login(api_client, password_user.email).status_code == 429
        AccessAttempt.objects.update(attempt_time=timezone.now() - timedelta(minutes=16))
        assert _login(api_client, password_user.email).status_code == 200

    @pytest.mark.django_db
    def test_django_admin_login_is_limited(self, api_client, platform_admin):
        platform_admin.set_password(PASSWORD)
        platform_admin.save()
        for i in range(10):
            _admin_login(api_client, platform_admin.username, "wrong", ip=f"10.0.0.{i}")
        assert _admin_login(api_client, platform_admin.username, ip="10.0.1.1").status_code == 429


def _create_user(client: APIClient, **body):
    return client.post(USERS, {"email": "hanako@example.co.jp", "display_name": "花子", **body}, format="json")


def _token(url: str) -> str:
    # tokenはURLのフラグメントに置く（アクセスログに残さない）
    base, _, token = url.partition("#")
    assert base == "http://testserver/invitation"
    return token


class TestInvitation:
    @pytest.mark.django_db
    def test_create_user_and_accept_invitation(self, admin_client, platform_admin):
        Group.objects.get_or_create(name="テスト用のGroup")
        res = _create_user(admin_client, email=" Hanako@Example.co.jp ", groups=["テスト用のGroup"])
        assert res.status_code == 201, res.json()
        data = res.json()
        assert data["email"] == "hanako@example.co.jp"
        assert data["groups"] == ["テスト用のGroup"]
        assert data["has_password"] is False
        assert data["invitation"]["purpose"] == InvitationPurpose.INVITE
        token = _token(data["invitation"]["url"])
        invitation = Invitation.objects.get()
        assert token not in invitation.token_hash
        assert invitation.created_by == platform_admin

        anonymous = APIClient()
        res = anonymous.post(VERIFY, {"token": token}, format="json")
        assert res.status_code == 200
        assert res.json()["email"] == "hanako@example.co.jp"

        res = anonymous.post(ACCEPT, {"token": token, "password": PASSWORD}, format="json")
        assert res.status_code == 200, res.json()
        assert anonymous.get("/api/app/v1/my/").json()["email"] == "hanako@example.co.jp"
        # 1回だけ使える
        assert APIClient().post(ACCEPT, {"token": token, "password": PASSWORD}, format="json").status_code == 400
        assert _login(APIClient(), "hanako@example.co.jp").status_code == 200

    @pytest.mark.django_db
    def test_password_is_validated(self, admin_client):
        token = _token(_create_user(admin_client).json()["invitation"]["url"])
        res = APIClient().post(ACCEPT, {"token": token, "password": "short"}, format="json")
        assert res.status_code == 400
        assert "password" in res.json()
        # 失敗してもリンクは使える
        assert APIClient().post(VERIFY, {"token": token}, format="json").status_code == 200

    @pytest.mark.django_db
    @pytest.mark.parametrize("url", [VERIFY, ACCEPT])
    def test_unknown_and_expired_tokens(self, admin_client, url):
        token = _token(_create_user(admin_client).json()["invitation"]["url"])
        assert APIClient().post(url, {"token": "x" + token, "password": PASSWORD}, format="json").status_code == 400
        Invitation.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        res = APIClient().post(url, {"token": token, "password": PASSWORD}, format="json")
        assert res.status_code == 400
        assert "期限が切れたか、使用済み" in res.json()["token"][0]

    @pytest.mark.django_db
    def test_issue_reset_link_revokes_previous(self, admin_client, password_user):
        first = admin_client.post(f"{USERS}{password_user.uid}/invitation/")
        assert first.status_code == 201, first.json()
        assert first.json()["purpose"] == InvitationPurpose.RESET
        second = admin_client.post(f"{USERS}{password_user.uid}/invitation/")
        assert APIClient().post(VERIFY, {"token": _token(first.json()["url"])}, format="json").status_code == 400
        token = _token(second.json()["url"])
        assert (
            APIClient().post(ACCEPT, {"token": token, "password": "new-" + PASSWORD}, format="json").status_code == 200
        )
        assert _login(APIClient(), password_user.email, "new-" + PASSWORD).status_code == 200

    @pytest.mark.django_db
    def test_accept_requires_csrf(self, admin_client):
        token = _token(_create_user(admin_client).json()["invitation"]["url"])
        client = APIClient(enforce_csrf_checks=True)
        assert client.post(ACCEPT, {"token": token, "password": PASSWORD}, format="json").status_code == 403


class TestInvitationIssue:
    @pytest.mark.django_db
    def test_one_open_link_per_user_in_db(self, password_user, platform_admin):
        Invitation.issue(user=password_user, actor=platform_admin)
        with pytest.raises(IntegrityError), transaction.atomic():
            Invitation.objects.create(
                user=password_user,
                purpose=InvitationPurpose.RESET,
                token_hash="x" * 64,
                expires_at=timezone.now(),
                created_by=platform_admin,
            )

    @pytest.mark.django_db
    def test_expired_link_is_replaced(self, password_user, platform_admin):
        first, _ = Invitation.issue(user=password_user, actor=platform_admin)
        Invitation.objects.update(expires_at=timezone.now() - timedelta(days=1))
        Invitation.issue(user=password_user, actor=platform_admin)
        first.refresh_from_db()
        assert first.revoked_at is not None
        assert Invitation.objects.open().count() == 1

    @pytest.mark.django_db(transaction=True)
    def test_concurrent_issue_leaves_one_usable_link(self, password_user, platform_admin):
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def issue():
            try:
                barrier.wait()
                Invitation.issue(user=password_user, actor=platform_admin)
            except BaseException as exc:
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=issue) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        assert Invitation.objects.count() == 2
        assert Invitation.objects.usable().count() == 1


class TestInvitationWithSSO:
    """SSOが有効な間は、基盤管理者以外にリンクを発行・使用させない（SSOだけでログインする規則を迂回させない）"""

    @pytest.mark.django_db
    def test_no_link_when_creating_user(self, admin_client):
        SSOProviderFactory.create()
        res = _create_user(admin_client)
        assert res.status_code == 201
        # SSOで初めてログインしたときに、このアカウントに紐付く
        assert res.json()["invitation"] is None
        assert not Invitation.objects.exists()

    @pytest.mark.django_db
    def test_cannot_issue_for_non_admin(self, admin_client, password_user, platform_admin):
        SSOProviderFactory.create()
        res = admin_client.post(f"{USERS}{password_user.uid}/invitation/")
        assert res.status_code == 400
        assert not Invitation.objects.exists()
        # 基盤管理者には発行できる（設定の誤りや認証基盤の停止に備える）
        assert admin_client.post(f"{USERS}{platform_admin.uid}/invitation/").status_code == 201

    @pytest.mark.django_db
    def test_links_issued_before_sso_cannot_be_used(self, admin_client, platform_admin):
        token = _token(_create_user(admin_client).json()["invitation"]["url"])
        # 無効にした後で再び有効にした場合も、Invitation.revoke_for_sso で無効にしているのを迂回して確かめる
        SSOProviderFactory.create()
        for url in (VERIFY, ACCEPT):
            res = APIClient().post(url, {"token": token, "password": PASSWORD}, format="json")
            assert res.status_code == 400
            assert "SSOでログインしてください" in res.json()["token"][0]
        assert APIClient().get("/api/app/v1/my/").status_code == 401

    @pytest.mark.django_db
    def test_enabling_sso_revokes_non_admin_links(self, admin_client, platform_admin):
        _create_user(admin_client)
        admin_link = admin_client.post(f"{USERS}{platform_admin.uid}/invitation/").json()
        res = admin_client.post(
            "/api/v1/sso_providers/",
            {
                "kind": "microsoft",
                "client_id": "c",
                "client_secret": "s",
                "tenant_id": "t",
                "allowed_email_domains": ["example.co.jp"],
            },
            format="json",
        )
        assert res.status_code == 201
        usable = Invitation.objects.usable()
        assert [i.user for i in usable] == [platform_admin]
        token = _token(admin_link["url"])
        assert APIClient().post(ACCEPT, {"token": token, "password": PASSWORD}, format="json").status_code == 200

    @pytest.mark.django_db
    def test_reactivating_sso_revokes_links(self, admin_client):
        sso = SSOProviderFactory.create(is_active=False)
        _create_user(admin_client)
        assert Invitation.objects.usable().count() == 1
        res = admin_client.patch(f"/api/v1/sso_providers/{sso.uid}/", {"is_active": True}, format="json")
        assert res.status_code == 200
        assert Invitation.objects.usable().count() == 0


class TestManageUser:
    @pytest.mark.django_db
    def test_only_platform_admin(self, api_client, app_user):
        assert api_client.get(USERS).status_code == 401
        api_client.force_login(app_user)
        assert api_client.get(USERS).status_code == 403
        assert api_client.get("/api/v1/groups/").status_code == 403

    @pytest.mark.django_db
    def test_email_is_unique_ignoring_case(self, admin_client, password_user):
        res = _create_user(admin_client, email="TARO@example.co.jp")
        assert res.status_code == 400
        assert "email" in res.json()

    @pytest.mark.django_db
    def test_update_groups_and_keep_email(self, admin_client, password_user):
        Group.objects.get_or_create(name="全社のSkill公開者")
        res = admin_client.patch(
            f"{USERS}{password_user.uid}/",
            {"groups": ["全社のSkill公開者"], "email": "other@example.co.jp", "display_name": "太郎"},
            format="json",
        )
        assert res.status_code == 200, res.json()
        password_user.refresh_from_db()
        assert password_user.email == "taro@example.co.jp"
        assert password_user.display_name == "太郎"
        assert list(password_user.groups.values_list("name", flat=True)) == ["全社のSkill公開者"]

    @pytest.mark.django_db
    def test_deactivate_ends_session(self, admin_client, password_user):
        client = APIClient()
        assert _login(client, password_user.email).status_code == 200
        res = admin_client.patch(f"{USERS}{password_user.uid}/", {"is_active": False}, format="json")
        assert res.status_code == 200
        assert client.get("/api/app/v1/my/").status_code == 401

    @pytest.mark.django_db
    def test_cannot_deactivate_self(self, admin_client, platform_admin):
        res = admin_client.patch(f"{USERS}{platform_admin.uid}/", {"is_active": False}, format="json")
        assert res.status_code == 400
        assert "is_active" in res.json()

    @pytest.mark.django_db
    def test_search_and_groups(self, admin_client, password_user):
        Group.objects.get_or_create(name="全社のSkill公開者")
        results = admin_client.get(USERS, {"search": "taro"}).json()["results"]
        assert [r["id"] for r in results] == [password_user.uid]
        names = [g["name"] for g in admin_client.get("/api/v1/groups/").json()]
        assert names == sorted(names)
        assert "全社のSkill公開者" in names


USER_ADMIN_PERMS = ("user.view_user", "user.add_user", "user.change_user")


@pytest.fixture(name="user_admin")
def fixture_user_admin(db) -> User:
    """基盤管理者ではないユーザー管理者"""
    return grant(UserFactory.create(), *USER_ADMIN_PERMS)


class TestDelegatedUserAdmin:
    """管理を委任したときの防止策（authorization.md §10）"""

    @pytest.mark.django_db
    def test_can_change_is_returned_per_account(self, api_client, user_admin, password_user, platform_admin):
        stronger = grant(UserFactory.create(), "skill.can_publish_company_skills")
        api_client.force_login(user_admin)
        rows = {r["id"]: r for r in api_client.get(USERS).json()["results"]}
        assert rows[password_user.uid]["can_change"] is True
        assert rows[password_user.uid]["can_issue_invitation"] is True
        assert rows[stronger.uid]["can_change"] is False
        assert rows[platform_admin.uid]["can_change"] is False
        assert rows[platform_admin.uid]["can_issue_invitation"] is False
        assert "is_platform_admin" not in rows[platform_admin.uid]

    @pytest.mark.django_db
    def test_cannot_change_stronger_or_platform_admin_account(self, api_client, user_admin, platform_admin):
        stronger = grant(UserFactory.create(), "skill.can_publish_company_skills")
        inactive = grant(UserFactory.create(is_active=False), "skill.can_publish_company_skills")
        api_client.force_login(user_admin)
        for user in (stronger, platform_admin, inactive):
            res = api_client.patch(f"{USERS}{user.uid}/", {"is_active": True, "display_name": "x"}, format="json")
            assert res.status_code == 403, user
            assert api_client.post(f"{USERS}{user.uid}/invitation/").status_code == 403
        # 同じpermissionまでなら変えられる
        same = grant(UserFactory.create(), "user.view_user")
        assert api_client.patch(f"{USERS}{same.uid}/", {"display_name": "x"}, format="json").status_code == 200

    @pytest.mark.django_db
    def test_groups_with_permissions_not_owned_cannot_be_assigned(self, api_client, user_admin, password_user):
        strong = Group.objects.create(name="強いGroup")
        strong.permissions.add(*_permissions("connector.add_connector"))
        weak = Group.objects.create(name="弱いGroup")
        weak.permissions.add(*_permissions("user.view_user"))
        api_client.force_login(user_admin)
        url = f"{USERS}{password_user.uid}/"
        res = api_client.patch(url, {"groups": ["強いGroup"]}, format="json")
        assert res.status_code == 400
        assert "groups" in res.json()
        assert api_client.patch(url, {"groups": ["弱いGroup"]}, format="json").status_code == 200
        res = _create_user(api_client, email="new@example.co.jp", groups=["強いGroup"])
        assert res.status_code == 400

    @pytest.mark.django_db
    def test_cannot_change_own_groups(self, api_client, user_admin):
        Group.objects.create(name="空のGroup")
        api_client.force_login(user_admin)
        res = api_client.patch(f"{USERS}{user_admin.uid}/", {"groups": ["空のGroup"]}, format="json")
        assert res.status_code == 400
        assert "groups" in res.json()
        res = api_client.patch(f"{USERS}{user_admin.uid}/", {"display_name": "自分"}, format="json")
        assert res.status_code == 200

    @pytest.mark.django_db
    def test_only_display_name_active_and_groups_are_writable(self, api_client, user_admin, password_user):
        api_client.force_login(user_admin)
        res = api_client.patch(
            f"{USERS}{password_user.uid}/",
            {"is_superuser": True, "user_permissions": [1], "is_staff": True},
            format="json",
        )
        assert res.status_code == 200
        password_user.refresh_from_db()
        assert not password_user.is_superuser
        assert not password_user.is_staff
        assert not password_user.user_permissions.exists()

    @pytest.mark.django_db
    @pytest.mark.parametrize("change", ["issuer_lost_permission", "target_promoted", "issuer_inactive"])
    def test_link_is_checked_again_when_accepted(self, api_client, user_admin, password_user, change):
        api_client.force_login(user_admin)
        token = _token(api_client.post(f"{USERS}{password_user.uid}/invitation/").json()["url"])
        if change == "issuer_lost_permission":
            password_user.user_permissions.add(*_permissions("user.add_user"))
            user_admin.user_permissions.remove(*_permissions("user.add_user"))
        elif change == "target_promoted":
            password_user.user_permissions.add(*_permissions("connector.add_connector"))
        else:
            User.objects.filter(pk=user_admin.pk).update(is_active=False)
        res = APIClient().post(ACCEPT, {"token": token, "password": PASSWORD}, format="json")
        assert res.status_code == 400
        assert "token" in res.json()


def _permissions(*codes: str) -> list[Permission]:
    return [Permission.objects.get(content_type__app_label=c.split(".")[0], codename=c.split(".")[1]) for c in codes]


class TestAcceptRace:
    @pytest.mark.django_db(transaction=True)
    @pytest.mark.parametrize("change", ["issuer_lost_permission", "target_promoted"])
    def test_accept_waits_for_a_concurrent_permission_change(self, password_user, change):
        """権限の変更（ユーザーの管理APIと同じく行をロックする）の途中に受け入れが来ても、変更の確定を待って
        最新の権限で確かめ直す（authorization.md §10）"""
        user_admin = grant(UserFactory.create(), *USER_ADMIN_PERMS)
        password_user = grant(password_user, "user.view_user")
        _invitation, token = Invitation.issue(user=password_user, actor=user_admin)
        locked = threading.Event()
        errors: list[BaseException] = []

        def change_permissions():
            try:
                with transaction.atomic():
                    target = user_admin if change == "issuer_lost_permission" else password_user
                    lock_accounts(target)
                    locked.set()
                    time.sleep(2)  # 受け入れの要求がロックを待つ間に、権限を変える
                    if change == "issuer_lost_permission":
                        user_admin.user_permissions.remove(*_permissions("user.view_user"))
                    else:
                        password_user.user_permissions.add(*_permissions("connector.add_connector"))
            except BaseException as exc:
                errors.append(exc)
            finally:
                connection.close()

        thread = threading.Thread(target=change_permissions)
        thread.start()
        assert locked.wait(timeout=5)
        res = APIClient().post(ACCEPT, {"token": token, "password": PASSWORD}, format="json")
        thread.join()
        assert errors == []
        assert res.status_code == 400
        assert "token" in res.json()


class TestUpdateRace:
    @pytest.mark.django_db(transaction=True)
    def test_update_keeps_a_password_set_while_waiting_for_the_lock(self, password_user):
        """ユーザーの更新は、ロックの後に読み直した行を保存する（待つ間に受け入れが設定したパスワードを書き戻さない）"""
        user_admin = grant(UserFactory.create(), *USER_ADMIN_PERMS)
        locked = threading.Event()
        errors: list[BaseException] = []

        def accept():
            try:
                with transaction.atomic():
                    lock_accounts(password_user)
                    locked.set()
                    time.sleep(2)  # 更新の要求がロックを待つ間に、パスワードを変える
                    user = User.objects.get(pk=password_user.pk)
                    user.set_password("another-horse-battery-staple")
                    user.save(update_fields=["password"])
            except BaseException as exc:
                errors.append(exc)
            finally:
                connection.close()

        client = APIClient()
        client.force_login(user_admin)
        thread = threading.Thread(target=accept)
        thread.start()
        assert locked.wait(timeout=5)
        res = client.patch(f"{USERS}{password_user.uid}/", {"display_name": "太郎"}, format="json")
        thread.join()
        assert errors == []
        assert res.status_code == 200, res.json()
        password_user.refresh_from_db()
        assert password_user.display_name == "太郎"
        assert password_user.check_password("another-horse-battery-staple")
