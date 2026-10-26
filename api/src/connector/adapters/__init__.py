"""ConnectorのAdapter。`get_adapter()` で取得し、共通の型（base.py）で呼ぶ"""

from ..models import Connector, ConnectorKind, ConnectorVersion, DatabaseConnection
from .base import DatasourceAdapter
from .mysql import MySQLAdapter
from .postgresql import PostgreSQLAdapter
from .sqlserver import SQLServerAdapter

ADAPTERS: dict[int, type[DatasourceAdapter]] = {
    ConnectorKind.POSTGRESQL: PostgreSQLAdapter,
    ConnectorKind.MYSQL: MySQLAdapter,
    ConnectorKind.SQLSERVER: SQLServerAdapter,
}


def get_adapter(target: Connector | DatabaseConnection, version: ConnectorVersion | None = None) -> DatasourceAdapter:
    """Connectorなら、その接続と版（省略すると現在の版）で、公開した範囲の describe・query を行う。
    DB接続なら、接続の確認と表の一覧だけを行う（範囲がないので describe・query はできない）
    """
    if isinstance(target, Connector):
        return ADAPTERS[target.connection.kind](target.connection, version or target.current_version)
    return ADAPTERS[target.kind](target, version)
