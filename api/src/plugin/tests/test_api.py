"""Pluginの管理画面のAPIと、停止したPluginの入口・出口・作成への効き方（design.md §3.11.1・§3.11.3）"""

import json
from unittest import mock

import pytest

from connector.factories import ConnectorFactory
from delivery.factories import DestinationFactory
from delivery.models import Delivery, DeliveryStatus
from delivery.tasks import send_delivery
from plugin import builtin, registry
from plugin.errors import PluginConfigError, PluginTemporaryError
from plugin.models import Plugin
from plugin.spec import PluginSpec
from run.models import RunLaunch, RunStatus
from task.dispatch import start_task_run
from task.factories import TaskDefinitionFactory
from task.models import TaskScope, TriggerBinding
from user.factories import UserFactory, grant
from utils.permissions import AppPermission

from .test_registry import ACME, FakeEntryPoint

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("default_runtime")]

PLUGINS = "/api/v1/plugins/"
SELECTABLE = "/api/app/v1/plugin_extensions/"


@pytest.fixture(name="entry_points")
def fixture_entry_points(monkeypatch):
    found: list[FakeEntryPoint] = []
    monkeypatch.setattr(registry, "entry_points", lambda group: list(found))
    registry.loaded.cache_clear()
    yield found
    registry.loaded.cache_clear()


def _add(entry_points, spec: PluginSpec) -> None:
    entry_points.append(FakeEntryPoint(spec.id, spec))
    registry.loaded.cache_clear()
    registry.sync_plugins()


def _set_enabled(plugin_id: str, enabled: bool) -> None:
    Plugin.objects.filter(plugin_id=plugin_id).update(enabled=enabled)


class TestManage:
    def test_only_platform_admins_see_plugins(self, api_client, app_user, admin_user):
        api_client.force_login(app_user)
        assert api_client.get(PLUGINS).status_code == 403
        api_client.force_login(admin_user)
        res = api_client.get(PLUGINS, {"page_size": 100})
        assert res.status_code == 200
        items = {p["id"]: p for p in res.json()["results"]}
        webhook = items[builtin.WEBHOOK]
        assert (webhook["builtin"], webhook["enabled"], webhook["usable"]) == (True, True, True)
        assert webhook["extensions"][0]["type"] == "notification"
        assert webhook["extensions"][0]["capabilities"] == ["file_fetch_auth", "url"]

    def test_admins_can_disable_and_enable(self, api_client, admin_user):
        api_client.force_login(admin_user)
        res = api_client.patch(f"{PLUGINS}{builtin.ZAPIER}/", {"enabled": False}, format="json")
        assert res.status_code == 200, res.json()
        assert res.json()["usable"] is False
        assert builtin.ZAPIER not in registry.available_ids()
        res = api_client.patch(f"{PLUGINS}{builtin.ZAPIER}/", {"enabled": True}, format="json")
        assert res.status_code == 200, res.json()
        assert builtin.ZAPIER in registry.available_ids()

    def test_other_fields_cannot_be_changed(self, api_client, admin_user):
        api_client.force_login(admin_user)
        res = api_client.patch(f"{PLUGINS}{builtin.ZAPIER}/", {"version": "9", "problem": "x"}, format="json")
        assert res.status_code == 200
        plugin = Plugin.objects.get(plugin_id=builtin.ZAPIER)
        assert (plugin.version, plugin.problem) == (registry.versions([builtin.ZAPIER])[builtin.ZAPIER], "")

    def test_storage_plugins_cannot_be_stopped_from_the_screen(self, api_client, admin_user):
        api_client.force_login(admin_user)
        res = api_client.patch(f"{PLUGINS}{builtin.LOCAL_STORAGE}/", {"enabled": False}, format="json")
        assert res.status_code == 400
        assert Plugin.objects.get(plugin_id=builtin.LOCAL_STORAGE).enabled

    def test_enabling_runs_the_self_check(self, api_client, admin_user, entry_points):
        check = mock.Mock(side_effect=RuntimeError("ACME_TOKEN がありません"))
        _add(entry_points, PluginSpec(**{**ACME.__dict__, "self_check": check}))
        api_client.force_login(admin_user)
        res = api_client.patch(f"{PLUGINS}acme.chat/", {"enabled": True}, format="json")
        assert res.status_code == 400
        assert "ACME_TOKEN" in str(res.json())
        assert not Plugin.objects.get(plugin_id="acme.chat").enabled

        check.side_effect = None
        res = api_client.patch(f"{PLUGINS}acme.chat/", {"enabled": True}, format="json")
        assert res.status_code == 200, res.json()
        assert check.call_count == 2

    def test_deprecated_or_invalid_plugins_cannot_be_enabled(self, api_client, admin_user, entry_points):
        _add(entry_points, PluginSpec(**{**ACME.__dict__, "deprecated": "acme.chat2 を使ってください"}))
        _add(entry_points, PluginSpec(**{**ACME.__dict__, "id": "acme.bad", "extensions": ()}))
        api_client.force_login(admin_user)
        assert api_client.patch(f"{PLUGINS}acme.chat/", {"enabled": True}, format="json").status_code == 400
        assert api_client.patch(f"{PLUGINS}acme.bad/", {"enabled": True}, format="json").status_code == 400

    def test_a_deprecated_plugin_that_is_enabled_keeps_working(self, api_client, admin_user, entry_points):
        _add(entry_points, ACME)
        _set_enabled("acme.chat", True)
        entry_points.clear()  # 新しい版を配備した
        _add(entry_points, PluginSpec(**{**ACME.__dict__, "deprecated": "acme.chat2 を使ってください"}))
        assert "acme.chat.notification" in registry.available_ids()
        api_client.force_login(admin_user)
        res = api_client.get(f"{PLUGINS}acme.chat/")
        assert (res.json()["enabled"], res.json()["deprecated"]) == (True, "acme.chat2 を使ってください")


class TestSelectable:
    def test_lists_adapters_that_can_be_used_for_new_settings(self, api_client, app_user):
        api_client.force_login(app_user)
        res = api_client.get(SELECTABLE, {"type": "connector"})
        assert res.status_code == 200
        names = {e["id"]: e for e in res.json()}
        assert set(names) == {builtin.POSTGRESQL, builtin.MYSQL, builtin.SQLSERVER}
        assert names[builtin.MYSQL]["config_schema"]["properties"]["default_schema"]["default"] == ""
        assert names[builtin.POSTGRESQL]["capabilities"] == ["schemas"]

        _set_enabled(builtin.MYSQL, False)
        assert builtin.MYSQL not in {e["id"] for e in api_client.get(SELECTABLE, {"type": "connector"}).json()}
        assert api_client.get(SELECTABLE).status_code == 400

    def test_new_settings_refuse_disabled_plugins(self, api_client, admin_user):
        _set_enabled(builtin.ZAPIER, False)
        api_client.force_login(admin_user)
        res = api_client.post(
            "/api/v1/destinations/",
            {
                "extension": builtin.ZAPIER,
                "scope": 3,
                "name": "Zap",
                "url": "https://hooks.zapier.com/hooks/catch/1/abc/",
            },
            format="json",
        )
        assert res.status_code == 400
        assert "extension" in res.json()
        res = api_client.post(
            "/api/v1/database_connections/",
            {
                "name": "勘定系",
                "extension": "acme.unknown",
                "host": "db.example.co.jp",
                "database": "core",
                "username": "reader",
                "password": "secret",
            },
            format="json",
        )
        assert res.status_code == 400
        assert "extension" in res.json()

    def test_the_default_port_comes_from_the_adapter(self, api_client, admin_user):
        api_client.force_login(admin_user)
        res = api_client.post(
            "/api/v1/database_connections/",
            {
                "name": "勘定系",
                "extension": builtin.SQLSERVER,
                "host": "db.example.co.jp",
                "database": "core",
                "username": "reader",
                "password": "secret",
            },
            format="json",
        )
        assert res.status_code == 201, res.json()
        assert (res.json()["port"], res.json()["extension_name"]) == (1433, "SQL Server")


@pytest.fixture(name="manager")
def fixture_manager(db):
    return grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)


@pytest.fixture(name="task")
def fixture_task(db):
    return TaskDefinitionFactory.create(
        scope=TaskScope.COMPANY,
        revision__destination=DestinationFactory.create(scope=TaskScope.COMPANY),
        revision__notify_events=["run.completed", "run.failed"],
        revision__prompt_template="確認してください。",
    )


class TestRuns:
    def test_the_manifest_records_the_plugin_versions(self, task, manager):
        connector = ConnectorFactory.create()
        run = start_task_run(task, inputs={}, actor=manager, launch=RunLaunch.MANUAL)
        plugins = run.manifest["fixed"]["plugins"]
        assert plugins[builtin.WEBHOOK] == registry.versions([builtin.WEBHOOK])[builtin.WEBHOOK]
        assert set(plugins) <= {builtin.WEBHOOK, connector.connection.extension}

    def test_a_disabled_trigger_plugin_refuses_webhooks(self, api_client, task, manager):
        api_client.force_login(manager)
        res = api_client.post(f"/api/app/v1/tasks/{task.uid}/triggers/", {"name": "SaaS"}, format="json")
        assert res.status_code == 201, res.json()
        key = res.json()["key"]
        api_client.logout()
        _set_enabled(builtin.WEBHOOK_TRIGGER, False)
        res = api_client.post(
            f"/api/hooks/v1/tasks/{task.uid}/",
            json.dumps({"event_id": "e1"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {key}",
        )
        assert res.status_code == 403
        assert res.json()["code"] == "trigger_suspended"
        assert not TriggerBinding.objects.get(task=task).in_service()

        # 新しい入口も作れない
        api_client.force_login(manager)
        res = api_client.post(f"/api/app/v1/tasks/{task.uid}/triggers/", {"name": "別"}, format="json")
        assert res.status_code == 400

    @pytest.mark.parametrize(
        ("error", "status"),
        [
            (PluginTemporaryError("混んでいます"), DeliveryStatus.RETRY_WAIT),
            (PluginConfigError("宛先の設定が違います"), DeliveryStatus.FAILED),
        ],
    )
    def test_contract_errors_of_notifications_become_outcomes(self, task, manager, error, status):
        run = start_task_run(task, inputs={}, actor=manager, launch=RunLaunch.MANUAL)
        run.set_status(RunStatus.RUNNING)
        with mock.patch("delivery.outbox.async_task"):
            run.set_status(RunStatus.COMPLETED)
        delivery = Delivery.objects.get(run=run)
        with (
            mock.patch("delivery.adapters.WebhookAdapter.deliver", side_effect=error),
            mock.patch("delivery.tasks.send_later"),
        ):
            send_delivery(delivery.pk)
        delivery.refresh_from_db()
        assert (delivery.status, delivery.last_error) == (status, str(error))

    def test_deliveries_of_a_disabled_plugin_are_not_sent(self, task, manager):
        run = start_task_run(task, inputs={}, actor=manager, launch=RunLaunch.MANUAL)
        run.set_status(RunStatus.RUNNING)
        with mock.patch("delivery.outbox.async_task"):
            run.set_status(RunStatus.COMPLETED)
        delivery = Delivery.objects.get(run=run)
        _set_enabled(builtin.WEBHOOK, False)
        with mock.patch("delivery.adapters.WebhookAdapter.deliver") as deliver:
            send_delivery(delivery.pk)
        deliver.assert_not_called()
        delivery.refresh_from_db()
        assert delivery.status == DeliveryStatus.CANCELLED
        assert delivery.last_error == "出口の種類（Plugin）が停止中です"
