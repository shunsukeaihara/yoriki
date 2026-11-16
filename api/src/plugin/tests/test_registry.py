"""Plugin Registry（design.md §3.11.1〜§3.11.4）"""

from dataclasses import dataclass
from typing import Any

import pytest

from connector.adapters import get_adapter
from connector.adapters.base import DatasourceAdapter
from connector.adapters.postgresql import PostgreSQLAdapter
from connector.errors import ConnectorError, ConnectorErrorKind
from connector.factories import ConnectorFactory, DatabaseConnectionFactory
from connector.models import Connector
from delivery.adapters import DeliveryRequest, NotificationAdapter, Outcome, WebhookAdapter
from delivery.factories import DestinationFactory
from delivery.models import AttemptOutcome, Destination
from plugin import builtin, registry
from plugin.errors import PluginTimeoutError, PluginUnavailable
from plugin.interface import check_shape
from plugin.models import Plugin, PluginExtension, PluginFailure
from plugin.observe import observed
from plugin.spec import Extension, ExtensionType, PluginSpec

pytestmark = pytest.mark.django_db


@dataclass
class FakeEntryPoint:
    name: str
    value: Any

    def load(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class AcmeNotification(NotificationAdapter):
    def deliver(self, request: DeliveryRequest) -> Outcome:
        return Outcome(AttemptOutcome.ACCEPTED)


ACME = PluginSpec(
    id="acme.chat",
    name="ACME Chat",
    version="0.3.0",
    extensions=(
        Extension(id="acme.chat.notification", type=ExtensionType.NOTIFICATION, name="ACME", adapter=AcmeNotification),
    ),
)


@pytest.fixture(name="entry_points")
def fixture_entry_points(monkeypatch):
    """自社のPluginのentry pointsを差し替える。Registryの読み込みのキャッシュは前後で消す"""
    found: list[FakeEntryPoint] = []
    monkeypatch.setattr(registry, "entry_points", lambda group: list(found))
    registry.loaded.cache_clear()
    yield found
    # DBの行はテストのtransactionごと戻る。読み込みは、差し替えを戻した後に読み直させる
    registry.loaded.cache_clear()


def _set_enabled(plugin_id: str, enabled: bool) -> None:
    Plugin.objects.filter(plugin_id=plugin_id).update(enabled=enabled)


# --- 形の確かめ ---


class TestShape:
    def test_builtin_adapters_fit_their_interfaces(self):
        assert all(item.valid for item in registry.loaded().values())
        assert set(registry.extensions()) >= {builtin.POSTGRESQL, builtin.WEBHOOK, builtin.WEBHOOK_TRIGGER}

    def test_an_adapter_must_inherit_the_base(self):
        class Loose:
            def deliver(self, request):
                pass

        assert check_shape(Loose, NotificationAdapter) == [
            "adapter does not inherit delivery.adapters.NotificationAdapter"
        ]

    def test_abstract_methods_must_be_implemented(self):
        class Half(NotificationAdapter):
            pass

        assert check_shape(Half, NotificationAdapter) == ["abstract methods are not implemented: deliver"]

    def test_the_arguments_must_accept_how_yoriki_calls(self):
        class OldStyle(WebhookAdapter):
            def deliver(self, *, url, secret, payload, attempt):  # type: ignore[override]
                return Outcome(AttemptOutcome.ACCEPTED)

        problems = check_shape(OldStyle, NotificationAdapter)
        assert len(problems) == 1 and problems[0].startswith("deliver: cannot be called as")

    def test_extra_optional_arguments_are_fine(self):
        class Extended(WebhookAdapter):
            def deliver(self, request, verbose=False):
                return Outcome(AttemptOutcome.ACCEPTED)

        assert check_shape(Extended, NotificationAdapter) == []

    def test_constructor_keyword_arguments_are_configuration(self):
        class Configured(PostgreSQLAdapter):
            def __init__(self, connection, version=None, *, pool_size: int):
                super().__init__(connection, version)

        assert check_shape(Configured, DatasourceAdapter) == []

        class Positional(PostgreSQLAdapter):
            def __init__(self, connection, version, pool_size):
                super().__init__(connection, version)

        assert check_shape(Positional, DatasourceAdapter)


# --- 宣言と登録 ---


class TestRegistration:
    def test_builtin_plugins_are_enabled_from_the_start(self):
        plugin = Plugin.objects.get(plugin_id=builtin.POSTGRESQL)
        assert (plugin.builtin, plugin.enabled, plugin.deployed, plugin.problem) == (True, True, True, "")
        assert plugin.version != ""
        extension = PluginExtension.objects.get(extension_id=builtin.POSTGRESQL)
        assert (extension.extension_type, extension.name) == (ExtensionType.CONNECTOR, "PostgreSQL")
        assert extension.config_schema["properties"]["port"]["default"] == 5432

    def test_a_company_plugin_is_found_but_stays_disabled(self, entry_points):
        entry_points.append(FakeEntryPoint("acme.chat", ACME))
        registry.loaded.cache_clear()
        registry.sync_plugins()
        plugin = Plugin.objects.get(plugin_id="acme.chat")
        assert (plugin.builtin, plugin.enabled, plugin.version) == (False, False, "0.3.0")
        assert "acme.chat.notification" not in registry.available_ids()

        _set_enabled("acme.chat", True)
        assert "acme.chat.notification" in registry.available_ids(ExtensionType.NOTIFICATION)
        extension, spec = registry.get("acme.chat.notification")
        assert (extension.adapter, spec.version) == (AcmeNotification, "0.3.0")

    @pytest.mark.parametrize(
        ("name", "value", "problem"),
        [
            ("acme.other", ACME, "differs from the entry point name"),
            (
                "yoriki.fake",
                PluginSpec(id="yoriki.fake", name="x", version="1", extensions=ACME.extensions),
                "namespace",
            ),
            ("acme.broken", ImportError("no module named acme_chat"), "cannot be loaded"),
            ("acme.notspec", object(), "does not point to a PluginSpec"),
        ],
    )
    def test_invalid_declarations_are_registered_with_the_reason(self, entry_points, name, value, problem):
        entry_points.append(FakeEntryPoint(name, value))
        registry.loaded.cache_clear()
        registry.sync_plugins()
        plugin = Plugin.objects.get(plugin_id=name)
        assert problem in plugin.problem
        assert not plugin.usable

    def test_a_plugin_removed_from_the_deployment_is_kept_but_unusable(self, entry_points):
        entry_points.append(FakeEntryPoint("acme.chat", ACME))
        registry.loaded.cache_clear()
        registry.sync_plugins()
        _set_enabled("acme.chat", True)

        entry_points.clear()
        registry.loaded.cache_clear()
        registry.sync_plugins()
        plugin = Plugin.objects.get(plugin_id="acme.chat")
        assert (plugin.enabled, plugin.deployed) == (True, False)
        assert "acme.chat.notification" not in registry.available_ids()
        with pytest.raises(PluginUnavailable):
            registry.get("acme.chat.notification")

    def test_deprecated_plugins_are_not_offered_for_new_settings(self, entry_points):
        deprecated = PluginSpec(
            id="acme.chat",
            name="ACME",
            version="0.4.0",
            extensions=ACME.extensions,
            deprecated="v2へ",
            replaced_by="acme.chat2",
        )
        entry_points.append(FakeEntryPoint("acme.chat", deprecated))
        registry.loaded.cache_clear()
        registry.sync_plugins()
        _set_enabled("acme.chat", True)
        assert "acme.chat.notification" in registry.available_ids()
        assert "acme.chat.notification" not in registry.available_ids(for_new=True)
        assert Plugin.objects.get(plugin_id="acme.chat").replaced_by == "acme.chat2"


# --- 停止の効き方 ---


class TestDisabling:
    def test_a_disabled_connector_plugin_is_like_a_stopped_connection(self):
        connector = ConnectorFactory.create()
        assert connector.in_service()
        _set_enabled(builtin.POSTGRESQL, False)
        connector = Connector.objects.get(pk=connector.pk)
        assert not connector.in_service()
        assert not Connector.objects.in_service().filter(pk=connector.pk).exists()
        with pytest.raises(ConnectorError) as exc_info:
            get_adapter(connector.connection)
        assert exc_info.value.kind == ConnectorErrorKind.UNAVAILABLE

        _set_enabled(builtin.POSTGRESQL, True)
        assert Connector.objects.get(pk=connector.pk).in_service()

    def test_a_disabled_notification_plugin_is_like_a_stopped_destination(self):
        destination = DestinationFactory.create()
        _set_enabled(builtin.WEBHOOK, False)
        destination = Destination.objects.get(pk=destination.pk)
        assert not destination.in_service()
        assert not Destination.objects.in_service().filter(pk=destination.pk).exists()
        assert destination.problem_for(None, sensitive=False) == "出口の種類（Plugin）が停止中です"

    def test_other_connections_keep_working(self):
        mysql = DatabaseConnectionFactory.create(extension=builtin.MYSQL, port=3306)
        _set_enabled(builtin.POSTGRESQL, False)
        assert get_adapter(mysql).connection == mysql


# --- 実行時の観察 ---


class TestObservation:
    def test_unexpected_errors_and_timeouts_are_counted_by_version(self):
        with pytest.raises(KeyError), observed(builtin.WEBHOOK):
            raise KeyError("payload")
        with pytest.raises(PluginTimeoutError), observed(builtin.WEBHOOK):
            raise PluginTimeoutError("slow")
        row = PluginFailure.objects.get(extension__extension_id=builtin.WEBHOOK)
        assert (row.unexpected, row.timeouts, row.plugin_version) == (
            1,
            1,
            registry.versions([builtin.WEBHOOK])[builtin.WEBHOOK],
        )
        assert row.last_error == "PluginTimeoutError: slow"

    def test_contract_errors_are_not_counted(self):
        with pytest.raises(ConnectorError), observed(builtin.POSTGRESQL):
            raise ConnectorError(ConnectorErrorKind.QUERY, "syntax")
        with pytest.raises(ValueError), observed(builtin.POSTGRESQL, expected=(ValueError,)):
            raise ValueError("expected")
        assert not PluginFailure.objects.exists()

    def test_connector_timeouts_are_counted(self):
        with pytest.raises(ConnectorError), observed(builtin.POSTGRESQL):
            raise ConnectorError(ConnectorErrorKind.TIMEOUT, "slow")
        assert PluginFailure.objects.get().timeouts == 1


def test_versions_are_recorded_for_the_manifest():
    assert registry.versions([builtin.WEBHOOK, "acme.unknown"]) == {
        builtin.WEBHOOK: registry.versions([builtin.WEBHOOK])[builtin.WEBHOOK]
    }
