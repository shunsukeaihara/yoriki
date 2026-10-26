"""ConnectorのAdapter。`get_adapter()` で取得し、共通の型（base.py）で呼ぶ"""

from ..models import Connector, ConnectorKind, ConnectorVersion
from .base import DatasourceAdapter
from .mysql import MySQLAdapter
from .postgresql import PostgreSQLAdapter
from .sqlserver import SQLServerAdapter

ADAPTERS: dict[int, type[DatasourceAdapter]] = {
    ConnectorKind.POSTGRESQL: PostgreSQLAdapter,
    ConnectorKind.MYSQL: MySQLAdapter,
    ConnectorKind.SQLSERVER: SQLServerAdapter,
}


def get_adapter(connector: Connector, version: ConnectorVersion | None = None) -> DatasourceAdapter:
    return ADAPTERS[connector.kind](connector, version)
