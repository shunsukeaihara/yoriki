import threading

import pytest
from django.db import connection

from conversation.dispatch import post_message
from conversation.factories import ConversationFactory
from run.models import Run
from runtime.errors import RegistryError
from runtime.factories import DIGEST, RuntimeProfileFactory, version_fields
from runtime.models import RuntimeProfile
from runtime.registry import ImageReference, InspectedImage

PROFILES = "/api/v1/runtime_profiles/"
IMAGE = f"yoriki/yoriki-sandbox-docs@{DIGEST}"
NEW_DIGEST = "sha256:" + "d" * 64


@pytest.fixture(name="admin_client")
def fixture_admin_client(api_client, platform_admin):
    api_client.force_login(platform_admin)
    return api_client


@pytest.fixture(name="inspected", autouse=True)
def fixture_inspected(monkeypatch):
    """レジストリの代わりに、渡されたイメージを契約番号1の2つのplatformとして返す"""
    calls: list[str] = []

    def fake(value: str) -> InspectedImage:
        calls.append(value)
        digest = value.rsplit("@", 1)[1]
        return InspectedImage(
            reference=ImageReference("docker.io", "yoriki/yoriki-sandbox-docs", digest),
            contract=1,
            platforms=["linux/amd64", "linux/arm64"],
            platform_digests={"linux/amd64": "sha256:" + "b" * 64, "linux/arm64": "sha256:" + "c" * 64},
            python_version="3.13.16",
            profile_label="docs",
        )

    monkeypatch.setattr("runtime.serializers.inspect_image", fake)
    return calls


@pytest.mark.django_db
class TestRuntimeProfile:
    def test_only_platform_admin(self, api_client, app_user):
        assert api_client.get(PROFILES).status_code == 401
        api_client.force_login(app_user)
        assert api_client.get(PROFILES).status_code == 403

    def test_create_inspects_the_image_and_makes_the_first_version(self, admin_client, inspected):
        res = admin_client.post(PROFILES, {"name": "文書処理", "image": IMAGE}, format="json")
        assert res.status_code == 201, res.data
        assert inspected == [IMAGE]
        assert res.data["image"] == f"docker.io/yoriki/yoriki-sandbox-docs@{DIGEST}"
        assert res.data["contract"] == 1
        assert res.data["platforms"] == ["linux/amd64", "linux/arm64"]
        assert res.data["version"] == 1
        assert res.data["memory_mb"] == 2048

    def test_registry_errors_are_shown_on_the_image_field(self, admin_client, monkeypatch):
        def fail(_value):
            raise RegistryError("イメージが見つかりません。名前とdigestを確かめてください")

        monkeypatch.setattr("runtime.serializers.inspect_image", fail)
        res = admin_client.post(PROFILES, {"name": "文書処理", "image": IMAGE}, format="json")
        assert res.status_code == 400
        assert "見つかりません" in res.data["image"][0]

    def test_tmpfs_must_fit_in_memory(self, admin_client):
        res = admin_client.post(PROFILES, {"name": "x", "image": IMAGE, "memory_mb": 1024}, format="json")
        assert res.status_code == 400
        assert "memory_mb" in res.data

    def test_changing_resources_creates_a_version_without_inspecting_again(self, admin_client, inspected):
        profile = RuntimeProfileFactory.create()
        url = f"{PROFILES}{profile.uid}/"
        res = admin_client.patch(url, {"version": 1, "memory_mb": 4096, "cpus": "4"}, format="json")
        assert res.status_code == 200, res.data
        assert (res.data["version"], res.data["memory_mb"], res.data["cpus"]) == (2, 4096, "4.00")
        assert inspected == []
        # 同じ中身なら版を作らない
        res = admin_client.patch(url, {"version": 2, "memory_mb": 4096}, format="json")
        assert res.data["version"] == 2
        # 古い版を元に編集したら409
        assert admin_client.patch(url, {"version": 1, "memory_mb": 3000}, format="json").status_code == 409
        versions = admin_client.get(f"{url}versions/").data["results"]
        assert [v["number"] for v in versions] == [2, 1]
        assert versions[1]["cpus"] == "2.00"

    def test_changing_the_image_inspects_it(self, admin_client, inspected):
        profile = RuntimeProfileFactory.create()
        res = admin_client.patch(
            f"{PROFILES}{profile.uid}/",
            {"version": 1, "image": f"yoriki/yoriki-sandbox-docs@{NEW_DIGEST}"},
            format="json",
        )
        assert res.status_code == 200, res.data
        assert res.data["image"].endswith(NEW_DIGEST)
        assert len(inspected) == 1

    def test_make_default(self, admin_client):
        first, second = RuntimeProfileFactory.create(), RuntimeProfileFactory.create()
        assert admin_client.post(f"{PROFILES}{first.uid}/make_default/").data["is_default"] is True
        assert admin_client.post(f"{PROFILES}{second.uid}/make_default/").status_code == 200
        first.refresh_from_db()
        assert first.is_default is False
        assert RuntimeProfile.default_version() == RuntimeProfile.objects.get(pk=second.pk).current_version

    @pytest.mark.django_db(transaction=True)
    def test_concurrent_make_default_does_not_break_the_constraint(self):
        profiles = [RuntimeProfileFactory.create(), RuntimeProfileFactory.create()]
        errors: list[Exception] = []
        barrier = threading.Barrier(2)

        def make(profile):
            try:
                barrier.wait()
                RuntimeProfile.objects.get(pk=profile.pk).make_default()
            except Exception as exc:  # どの例外も失敗として集める
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=make, args=(p,)) for p in profiles]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert RuntimeProfile.objects.filter(is_default=True).count() == 1

    def test_inactive_cannot_be_default_and_is_not_used(self, admin_client):
        profile = RuntimeProfileFactory.create(is_active=False)
        assert admin_client.post(f"{PROFILES}{profile.uid}/make_default/").status_code == 409
        RuntimeProfile.objects.filter(pk=profile.pk).update(is_default=True)
        assert RuntimeProfile.default_version() is None


@pytest.mark.django_db
class TestRunPinsTheVersion:
    def test_a_new_run_pins_the_default_version(self, app_user):
        profile = RuntimeProfileFactory.create(is_default=True)
        conversation = ConversationFactory.create(owner=app_user)
        message = post_message(conversation, body="集計して", client_message_id="", mode="auto", actor=app_user)
        run = message.run
        assert run is not None
        pinned = profile.current_version
        assert pinned is not None
        assert run.runtime_version == pinned
        assert run.manifest["fixed"]["runtime_version"] == pinned.pk
        # 新しい版を作っても、始まっているRunは替わらない
        profile.revise(actor=app_user, expected_version=1, **version_fields(memory_mb=4096))
        run.refresh_from_db()
        assert run.runtime_version is not None and run.runtime_version.number == 1

    def test_without_a_default_the_run_does_not_start(self, api_client, app_user):
        # エージェントの作業はほとんどがSandboxの中で動くので、既定がなければRunを始めず、理由を返す
        conversation = ConversationFactory.create(owner=app_user)
        api_client.force_login(app_user)
        res = api_client.post(
            f"/api/app/v1/conversations/{conversation.uid}/messages/", {"body": "要約して"}, format="json"
        )
        assert res.status_code == 400
        assert "Runtime Profile" in str(res.data["non_field_errors"][0])
        assert not Run.objects.filter(conversation=conversation).exists()
