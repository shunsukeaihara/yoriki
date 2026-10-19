import pytest
from django.contrib.auth.models import Permission
from django.test import override_settings

from common.choices import DataClassification
from llm.factories import LLMConnectionFactory, ModelProfileFactory
from llm.models import LLMConnection, ModelProfile, ModelProfileVersion, ProviderKind
from llm.providers.base import ProviderError

CONNECTIONS = "/api/v1/llm_connections/"
PROFILES = "/api/v1/model_profiles/"


def _current(profile) -> ModelProfileVersion:
    assert profile.current_version is not None
    return profile.current_version


@pytest.fixture(name="admin_client")
def fixture_admin_client(api_client, platform_admin):
    api_client.force_login(platform_admin)
    return api_client


class TestPermission:
    @pytest.mark.django_db
    @pytest.mark.parametrize("url", [CONNECTIONS, PROFILES])
    def test_only_platform_admin(self, api_client, app_user, url):
        assert api_client.get(url).status_code == 401
        api_client.force_login(app_user)
        assert api_client.get(url).status_code == 403

    @pytest.mark.django_db
    def test_permission_from_role_is_not_enough(self, api_client, app_user):
        # 標準のpermissionを付けても、基盤管理者でなければ使えない（画面の表示に使うだけ）
        app_user.user_permissions.add(Permission.objects.get(codename="change_llmconnection"))
        api_client.force_login(app_user)
        assert api_client.get(CONNECTIONS).status_code == 403


class TestLLMConnection:
    @pytest.mark.django_db
    def test_create_encrypts_api_key_and_never_returns_it(self, admin_client, platform_admin):
        res = admin_client.post(
            CONNECTIONS, {"name": " OpenAI ", "kind": ProviderKind.OPENAI, "api_key": "sk-abcdefgh12345678"}
        )
        assert res.status_code == 201, res.json()
        data = res.json()
        assert data["name"] == "OpenAI"
        assert data["has_api_key"] is True
        assert data["api_key_hint"] == "5678"
        assert "api_key" not in data
        assert "sk-abcdefgh12345678" not in res.content.decode()

        connection = LLMConnection.objects.get(uid=data["id"])
        assert connection.created_by == platform_admin
        assert "sk-abcdefgh" not in connection.api_key_encrypted
        assert connection.get_api_key() == "sk-abcdefgh12345678"

    @pytest.mark.django_db
    def test_official_api_requires_key_and_rejects_base_url(self, admin_client):
        res = admin_client.post(
            CONNECTIONS, {"name": "a", "kind": ProviderKind.ANTHROPIC, "base_url": "https://evil.example.com"}
        )
        assert res.status_code == 400
        assert set(res.json()) == {"api_key", "base_url"}

    @pytest.mark.django_db
    def test_compatible_requires_base_url_but_not_key(self, admin_client):
        res = admin_client.post(CONNECTIONS, {"name": "a", "kind": ProviderKind.OPENAI_COMPATIBLE})
        assert res.status_code == 400
        assert set(res.json()) == {"base_url"}
        res = admin_client.post(
            CONNECTIONS,
            {"name": "a", "kind": ProviderKind.OPENAI_COMPATIBLE, "base_url": "http://ollama.internal:11434/v1"},
        )
        assert res.status_code == 201, res.json()
        assert res.json()["has_api_key"] is False

    @pytest.mark.django_db
    def test_patch_without_api_key_keeps_it(self, admin_client):
        connection = LLMConnectionFactory.create()
        res = admin_client.patch(f"{CONNECTIONS}{connection.uid}/", {"timeout_seconds": 30})
        assert res.status_code == 200, res.json()
        connection.refresh_from_db()
        assert connection.timeout_seconds == 30
        assert connection.get_api_key() == "sk-test-0123456789abcd"

    @pytest.mark.django_db
    def test_changing_target_requires_api_key_again(self, admin_client):
        # 登録済みのAPI keyを、別のサーバーへ送らせない
        connection = LLMConnectionFactory.create(kind=ProviderKind.OPENAI_COMPATIBLE, base_url="http://llm.internal/v1")
        url = f"{CONNECTIONS}{connection.uid}/"
        res = admin_client.patch(url, {"base_url": "https://attacker.example.com/v1"})
        assert res.status_code == 400
        assert "api_key" in res.json()

        res = admin_client.patch(url, {"base_url": "https://llm2.internal/v1", "api_key": "new-key-0123456789"})
        assert res.status_code == 200, res.json()
        connection.refresh_from_db()
        assert connection.get_api_key() == "new-key-0123456789"

    @pytest.mark.django_db
    def test_clear_api_key_only_for_compatible(self, admin_client):
        official = LLMConnectionFactory.create()
        assert admin_client.patch(f"{CONNECTIONS}{official.uid}/", {"api_key": ""}).status_code == 400
        compatible = LLMConnectionFactory.create(kind=ProviderKind.OPENAI_COMPATIBLE, base_url="http://x.internal/v1")
        assert admin_client.patch(f"{CONNECTIONS}{compatible.uid}/", {"api_key": ""}).status_code == 200
        compatible.refresh_from_db()
        assert not compatible.has_api_key

    @pytest.mark.django_db
    def test_name_is_unique_among_not_deleted(self, admin_client):
        LLMConnectionFactory.create(name="共通", deleted=True)
        LLMConnectionFactory.create(name="重複")
        data = {"kind": ProviderKind.OPENAI, "api_key": "sk-0123456789abcd"}
        assert admin_client.post(CONNECTIONS, {"name": "共通", **data}).status_code == 201
        assert "name" in admin_client.post(CONNECTIONS, {"name": "重複", **data}).json()

    @pytest.mark.django_db
    @override_settings(YORIKI_SECRET_ENCRYPTION_KEYS=[])
    def test_api_key_requires_encryption_key(self, admin_client):
        res = admin_client.post(CONNECTIONS, {"name": "a", "kind": ProviderKind.OPENAI, "api_key": "sk-0123456789"})
        assert res.status_code == 400
        assert "YORIKI_SECRET_ENCRYPTION_KEYS" in res.json()["api_key"][0]

    @pytest.mark.django_db
    def test_delete_in_use_is_conflict(self, admin_client):
        profile = ModelProfileFactory.create()
        connection = _current(profile).connection
        assert admin_client.delete(f"{CONNECTIONS}{connection.uid}/").status_code == 409

        profile.deleted = True
        profile.save()
        assert admin_client.delete(f"{CONNECTIONS}{connection.uid}/").status_code == 204
        connection.refresh_from_db()
        assert connection.deleted

    @pytest.mark.django_db
    def test_check(self, admin_client, monkeypatch):
        connection = LLMConnectionFactory.create()
        url = f"{CONNECTIONS}{connection.uid}/check/"

        monkeypatch.setattr("llm.providers.openai.OpenAIAdapter.list_models", lambda self: ["gpt-a", "gpt-b"])
        assert admin_client.post(url).json() == {"ok": True, "detail": "", "models": ["gpt-a", "gpt-b"]}

        def fail(self):
            raise ProviderError("API keyが正しくありません。", status_code=401)

        monkeypatch.setattr("llm.providers.openai.OpenAIAdapter.list_models", fail)
        assert admin_client.post(url).json() == {"ok": False, "detail": "API keyが正しくありません。", "models": []}


def _version(connection, **overrides):
    return {
        "connection": connection.uid,
        "model_name": " gpt-test ",
        "max_input_tokens": 128000,
        "max_output_tokens": 16000,
        "input_price": "2.50",
        "output_price": "10.00",
        "supports_tools": True,
        **overrides,
    }


class TestModelProfile:
    @pytest.mark.django_db
    def test_create_with_first_version(self, admin_client, platform_admin):
        connection = LLMConnectionFactory.create()
        res = admin_client.post(PROFILES, {"name": "標準", "version": _version(connection)}, format="json")
        assert res.status_code == 201, res.json()
        data = res.json()
        assert data["current_version"]["number"] == 1
        assert data["current_version"]["model_name"] == "gpt-test"
        assert data["current_version"]["connection_info"]["name"] == connection.name
        assert data["current_version"]["data_classification_display"] == "社外送信可"
        profile = ModelProfile.objects.get(uid=data["id"])
        assert profile.created_by == platform_admin
        assert profile.versions.count() == 1

    @pytest.mark.django_db
    def test_inactive_connection_cannot_be_chosen(self, admin_client):
        connection = LLMConnectionFactory.create(is_active=False)
        res = admin_client.post(PROFILES, {"name": "a", "version": _version(connection)}, format="json")
        assert res.status_code == 400
        assert "connection" in res.json()["version"]

    @pytest.mark.django_db
    def test_internal_only_requires_compatible_connection(self, admin_client):
        official = LLMConnectionFactory.create()
        version = _version(official, data_classification=DataClassification.INTERNAL_ONLY)
        res = admin_client.post(PROFILES, {"name": "a", "version": version}, format="json")
        assert "data_classification" in res.json()["version"]

        local = LLMConnectionFactory.create(kind=ProviderKind.OPENAI_COMPATIBLE, base_url="http://llm.internal/v1")
        version = _version(local, data_classification=DataClassification.INTERNAL_ONLY)
        assert admin_client.post(PROFILES, {"name": "a", "version": version}, format="json").status_code == 201

    @pytest.mark.django_db
    def test_revise_creates_new_version(self, admin_client):
        profile = ModelProfileFactory.create()
        first = _current(profile)
        res = admin_client.post(
            f"{PROFILES}{profile.uid}/revise/",
            {
                "expected_version": 1,
                **_version(first.connection, model_name="gpt-new"),
                "change_reason": "新しいモデル",
            },
            format="json",
        )
        assert res.status_code == 201, res.json()
        assert res.json()["number"] == 2
        profile.refresh_from_db()
        assert _current(profile).model_name == "gpt-new"
        # 前の版は変わらない（進行中のRunが使い続ける）
        first.refresh_from_db()
        assert first.model_name == "gpt-test"

        versions = admin_client.get(f"{PROFILES}{profile.uid}/versions/").json()["results"]
        assert [v["number"] for v in versions] == [2, 1]
        assert admin_client.get(f"{PROFILES}{profile.uid}/versions/1/").json()["model_name"] == "gpt-test"

    @pytest.mark.django_db
    def test_revise_conflict(self, admin_client):
        profile = ModelProfileFactory.create()
        data = {"expected_version": 0, **_version(_current(profile).connection)}
        assert admin_client.post(f"{PROFILES}{profile.uid}/revise/", data, format="json").status_code == 409

    @pytest.mark.django_db
    def test_patch_cannot_change_version(self, admin_client):
        profile = ModelProfileFactory.create()
        res = admin_client.patch(
            f"{PROFILES}{profile.uid}/", {"name": "改名", "is_active": False, "current_version": None}, format="json"
        )
        assert res.status_code == 200, res.json()
        profile.refresh_from_db()
        assert (profile.name, profile.is_active, _current(profile).number) == ("改名", False, 1)

    @pytest.mark.django_db
    def test_filter_by_connection(self, admin_client):
        target = ModelProfileFactory.create()
        ModelProfileFactory.create()
        uid = _current(target).connection.uid
        results = admin_client.get(PROFILES, {"connection": uid}).json()["results"]
        assert [r["id"] for r in results] == [target.uid]
