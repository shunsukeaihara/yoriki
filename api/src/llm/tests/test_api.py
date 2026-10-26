import pytest
from django.contrib.auth.models import Permission
from django.test import override_settings

from llm.factories import LLMConnectionFactory, ModelProfileFactory
from llm.models import LLMConnection, ModelProfile, ModelProfileVersion, ProviderKind
from llm.providers.base import ProviderError

CONNECTIONS = "/api/v1/llm_connections/"
PROFILES = "/api/v1/model_profiles/"


@pytest.fixture(name="admin_client")
def fixture_admin_client(api_client, platform_admin):
    api_client.force_login(platform_admin)
    return api_client


def _current(profile: ModelProfile) -> ModelProfileVersion:
    profile.refresh_from_db()
    assert profile.current_version is not None
    return profile.current_version


class TestPermission:
    @pytest.mark.django_db
    @pytest.mark.parametrize("url", [CONNECTIONS, PROFILES])
    def test_only_platform_admin(self, api_client, app_user, url):
        assert api_client.get(url).status_code == 401
        api_client.force_login(app_user)
        assert api_client.get(url).status_code == 403

    @pytest.mark.django_db
    def test_standard_permission_decides(self, api_client, app_user):
        # 基盤管理者でなくても、標準のpermissionを持てばその操作ができる（authorization.md §10）
        app_user.user_permissions.add(Permission.objects.get(codename="view_llmconnection"))
        api_client.force_login(app_user)
        assert api_client.get(CONNECTIONS).status_code == 200
        assert api_client.get(PROFILES).status_code == 403
        res = api_client.post(CONNECTIONS, {"name": "x", "kind": ProviderKind.OPENAI, "api_key": "sk-abcdefgh12345678"})
        assert res.status_code == 403


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
        assert "sk-abcdefgh" not in connection.api_key.ciphertext
        assert connection.api_key.reveal() == "sk-abcdefgh12345678"

    @pytest.mark.django_db
    def test_cloud_requires_key_and_rejects_base_url(self, admin_client):
        res = admin_client.post(
            CONNECTIONS, {"name": "a", "kind": ProviderKind.ANTHROPIC, "base_url": "https://evil.example.com"}
        )
        assert res.status_code == 400
        assert set(res.json()) == {"api_key", "base_url"}

    @pytest.mark.django_db
    def test_local_requires_base_url_but_not_key(self, admin_client):
        res = admin_client.post(CONNECTIONS, {"name": "a", "kind": ProviderKind.LOCAL})
        assert res.status_code == 400
        assert set(res.json()) == {"base_url"}
        res = admin_client.post(
            CONNECTIONS, {"name": "a", "kind": ProviderKind.LOCAL, "base_url": "http://ollama.internal:11434/v1"}
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
        assert connection.api_key.reveal() == "sk-test-0123456789abcd"

    @pytest.mark.django_db
    def test_api_key_can_be_replaced(self, admin_client):
        connection = LLMConnectionFactory.create()
        res = admin_client.patch(f"{CONNECTIONS}{connection.uid}/", {"api_key": "sk-new-0123456789abcd"})
        assert res.status_code == 200, res.json()
        assert res.json()["api_key_hint"] == "abcd"
        connection.refresh_from_db()
        assert connection.api_key.reveal() == "sk-new-0123456789abcd"

    @pytest.mark.django_db
    def test_clear_api_key_only_for_local(self, admin_client):
        cloud = LLMConnectionFactory.create()
        assert admin_client.patch(f"{CONNECTIONS}{cloud.uid}/", {"api_key": ""}).status_code == 400
        local = LLMConnectionFactory.create(kind=ProviderKind.LOCAL, base_url="http://x.internal/v1")
        assert admin_client.patch(f"{CONNECTIONS}{local.uid}/", {"api_key": ""}).status_code == 200
        local.refresh_from_db()
        assert not local.api_key

    @pytest.mark.django_db
    def test_kind_and_base_url_are_fixed_after_creation(self, admin_client):
        # 接続先ごとに接続を作る。更新では種別とbase URLを受け付けない（送っても変わらない）
        local = LLMConnectionFactory.create(kind=ProviderKind.LOCAL, base_url="http://llm.internal/v1")
        res = admin_client.put(
            f"{CONNECTIONS}{local.uid}/",
            {"name": "改名", "kind": ProviderKind.OPENAI, "base_url": "https://attacker.example.com/v1"},
        )
        assert res.status_code == 200, res.json()
        local.refresh_from_db()
        assert (local.name, local.kind, local.base_url) == ("改名", ProviderKind.LOCAL, "http://llm.internal/v1")

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
    def test_search_by_name(self, admin_client):
        target = LLMConnectionFactory.create(name="社内のvLLM")
        LLMConnectionFactory.create(name="OpenAI")
        results = admin_client.get(CONNECTIONS, {"search": "vllm"}).json()["results"]
        assert [r["id"] for r in results] == [target.uid]

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

    @pytest.mark.django_db
    def test_django_admin_is_read_only(self, api_client, platform_admin):
        # 管理画面のフォームには API key が出ないので、作成・変更はYorikiの管理画面だけで行う
        connection = LLMConnectionFactory.create(kind=ProviderKind.LOCAL, base_url="http://llm.internal/v1")
        api_client.force_login(platform_admin)
        assert api_client.get("/api/admin/llm/llmconnection/add/").status_code == 403
        url = f"/api/admin/llm/llmconnection/{connection.pk}/change/"
        page = api_client.get(url).content.decode()
        assert "sk-test" not in page
        assert 'name="api_key"' not in page
        assert 'name="base_url"' not in page
        assert 'name="kind"' not in page
        assert api_client.post(url, {"name": "変更", "base_url": "http://other/v1"}).status_code == 403
        connection.refresh_from_db()
        assert connection.base_url == "http://llm.internal/v1"


def _profile(connection, **overrides):
    return {
        "name": "標準",
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
    def test_create_makes_first_version(self, admin_client, platform_admin):
        connection = LLMConnectionFactory.create()
        res = admin_client.post(PROFILES, _profile(connection), format="json")
        assert res.status_code == 201, res.json()
        data = res.json()
        assert data["version"] == 1
        assert data["model_name"] == "gpt-test"
        assert data["connection"] == connection.uid
        assert data["connection_info"]["name"] == connection.name
        assert data["input_price"] == "2.500000"
        profile = ModelProfile.objects.get(uid=data["id"])
        assert profile.created_by == platform_admin
        assert profile.versions.count() == 1

    @pytest.mark.django_db
    def test_inactive_connection_cannot_be_chosen(self, admin_client):
        connection = LLMConnectionFactory.create(is_active=False)
        res = admin_client.post(PROFILES, _profile(connection), format="json")
        assert res.status_code == 400
        assert "connection" in res.json()

    @pytest.mark.django_db
    def test_output_tokens_must_not_exceed_input(self, admin_client):
        connection = LLMConnectionFactory.create()
        res = admin_client.post(PROFILES, _profile(connection, max_output_tokens=200000), format="json")
        assert res.status_code == 400
        assert "max_output_tokens" in res.json()

    @pytest.mark.django_db
    def test_changing_content_creates_new_version(self, admin_client):
        profile = ModelProfileFactory.create()
        first = _current(profile)
        res = admin_client.patch(
            f"{PROFILES}{profile.uid}/",
            {"version": 1, "model_name": "gpt-new", "change_reason": "新しいモデル"},
            format="json",
        )
        assert res.status_code == 200, res.json()
        assert (res.json()["version"], res.json()["model_name"]) == (2, "gpt-new")
        # 送らなかった項目は前の版の値を引き継ぐ
        assert res.json()["max_input_tokens"] == first.max_input_tokens
        # 前の版は変わらない（進行中のRunが使い続ける）
        first.refresh_from_db()
        assert first.model_name == "gpt-test"

        versions = admin_client.get(f"{PROFILES}{profile.uid}/versions/").json()["results"]
        assert [(v["number"], v["model_name"]) for v in versions] == [(2, "gpt-new"), (1, "gpt-test")]
        assert versions[0]["change_reason"] == "新しいモデル"
        assert admin_client.get(f"{PROFILES}{profile.uid}/versions/1/").json()["model_name"] == "gpt-test"

    @pytest.mark.django_db
    def test_same_content_does_not_create_version(self, admin_client):
        profile = ModelProfileFactory.create()
        res = admin_client.patch(f"{PROFILES}{profile.uid}/", {"version": 1, "name": "改名"}, format="json")
        assert res.status_code == 200, res.json()
        assert (res.json()["version"], res.json()["name"]) == (1, "改名")
        assert profile.versions.count() == 1

    @pytest.mark.django_db
    def test_update_requires_version_and_detects_conflict(self, admin_client):
        profile = ModelProfileFactory.create()
        url = f"{PROFILES}{profile.uid}/"
        res = admin_client.patch(url, {"model_name": "x"}, format="json")
        assert res.status_code == 400
        assert "version" in res.json()
        # 編集を始めた後に、別の版ができていた
        assert admin_client.patch(url, {"version": 0, "model_name": "x"}, format="json").status_code == 409

    @pytest.mark.django_db
    def test_deactivate(self, admin_client):
        profile = ModelProfileFactory.create()
        res = admin_client.patch(f"{PROFILES}{profile.uid}/", {"version": 1, "is_active": False}, format="json")
        assert res.status_code == 200, res.json()
        profile.refresh_from_db()
        assert profile.is_active is False

    @pytest.mark.django_db
    def test_django_admin_cannot_add_profiles_or_versions(self, api_client, platform_admin):
        # 版は revise() でだけ作る。管理画面から、版のない設定や、番号・作成者を自由に入れた版を作らせない
        profile = ModelProfileFactory.create()
        api_client.force_login(platform_admin)
        assert api_client.get("/api/admin/llm/modelprofile/add/").status_code == 403
        assert api_client.get("/api/admin/llm/modelprofileversion/add/").status_code == 403
        assert api_client.get(f"/api/admin/llm/modelprofile/{profile.pk}/change/").status_code == 200
        assert api_client.post(f"/api/admin/llm/modelprofile/{profile.pk}/change/", {"name": "x"}).status_code == 403

    @pytest.mark.django_db
    def test_filter_by_connection(self, admin_client):
        target = ModelProfileFactory.create()
        ModelProfileFactory.create()
        uid = _current(target).connection.uid
        results = admin_client.get(PROFILES, {"connection": uid}).json()["results"]
        assert [r["id"] for r in results] == [target.uid]

    @pytest.mark.django_db
    def test_search_by_name(self, admin_client):
        target = ModelProfileFactory.create(name="GPT（社外）")
        ModelProfileFactory.create(name="Claude")
        results = admin_client.get(PROFILES, {"search": "gpt"}).json()["results"]
        assert [r["id"] for r in results] == [target.uid]
