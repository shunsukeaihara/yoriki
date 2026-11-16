"""本体のPluginの宣言（design.md §3.11.1）。

本体（api/）はpackageとして入れないので、entry pointsではなくここで宣言する。宣言の型と確かめ方は自社のPluginと同じ。
別々に止められるよう、1つのAdapterごとに1つのPluginにする（plugin_idとextension_idは同じ）。各アプリのAdapterを
読み込むので、Registryが使うときに読み込む（plugin/registry.py）。
"""

from django.conf import settings

from artifact.storage import LocalStorage, S3Storage
from connector.adapters.mysql import MySQLAdapter
from connector.adapters.postgresql import PostgreSQLAdapter
from connector.adapters.sqlserver import SQLServerAdapter
from delivery.adapters import WebhookAdapter, ZapierAdapter
from delivery.in_app import InAppAdapter
from task.triggers import WebhookTrigger

from .builtin import (
    FILE_FETCH_AUTH,
    IN_APP,
    LOCAL_STORAGE,
    MYSQL,
    PERSONAL,
    POSTGRESQL,
    S3_STORAGE,
    SCHEMAS,
    SQLSERVER,
    START,
    URL,
    WEBHOOK,
    WEBHOOK_TRIGGER,
    ZAPIER,
)
from .spec import Extension, ExtensionType, PluginSpec


def _database_schema(port: int, default_schema: str) -> dict:
    return {
        "type": "object",
        "properties": {
            "port": {"type": "integer", "minimum": 1, "maximum": 65535, "default": port},
            "default_schema": {"type": "string", "default": default_schema},
        },
    }


def _single(plugin_id: str, name: str, extension_type: ExtensionType, adapter: type, **options) -> PluginSpec:
    return PluginSpec(
        id=plugin_id,
        name=name,
        version=settings.YORIKI_VERSION,
        extensions=(Extension(id=plugin_id, type=extension_type, name=name, adapter=adapter, **options),),
    )


def plugins() -> list[PluginSpec]:
    return [
        _single(WEBHOOK_TRIGGER, "Webhook", ExtensionType.TRIGGER, WebhookTrigger, capabilities=frozenset({START})),
        _single(
            WEBHOOK,
            "Webhook",
            ExtensionType.NOTIFICATION,
            WebhookAdapter,
            capabilities=frozenset({URL, FILE_FETCH_AUTH}),
            secrets=("signing_secret",),
        ),
        _single(
            ZAPIER,
            "Zapier",
            ExtensionType.NOTIFICATION,
            ZapierAdapter,
            capabilities=frozenset({URL}),
            secrets=("signing_secret",),
        ),
        _single(IN_APP, "画面の通知", ExtensionType.NOTIFICATION, InAppAdapter, capabilities=frozenset({PERSONAL})),
        _single(LOCAL_STORAGE, "ローカル", ExtensionType.STORAGE, LocalStorage),
        _single(S3_STORAGE, "S3", ExtensionType.STORAGE, S3Storage),
        _single(
            POSTGRESQL,
            "PostgreSQL",
            ExtensionType.CONNECTOR,
            PostgreSQLAdapter,
            capabilities=frozenset({SCHEMAS}),
            config_schema=_database_schema(5432, "public"),
            secrets=("password",),
        ),
        _single(
            MYSQL,
            "MySQL",
            ExtensionType.CONNECTOR,
            MySQLAdapter,
            config_schema=_database_schema(3306, ""),
            secrets=("password",),
        ),
        _single(
            SQLSERVER,
            "SQL Server",
            ExtensionType.CONNECTOR,
            SQLServerAdapter,
            capabilities=frozenset({SCHEMAS}),
            config_schema=_database_schema(1433, "dbo"),
            secrets=("password",),
        ),
    ]
