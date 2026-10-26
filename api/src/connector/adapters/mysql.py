import ssl
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import Any

import pymysql
import pymysql.cursors
from django.utils.translation import gettext as _

from ..errors import ConnectorError, ConnectorErrorKind
from ..models import TLSMode
from ..sql import MYSQL
from .base import DatasourceAdapter

# MAX_EXECUTION_TIME を超えた（ER_QUERY_TIMEOUT）
QUERY_TIMEOUT = 3024


def _ssl_context(mode: TLSMode) -> ssl.SSLContext | None:
    if mode == TLSMode.DISABLE:
        return None
    context = ssl.create_default_context()
    if mode == TLSMode.REQUIRE:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


class MySQLAdapter(DatasourceAdapter):
    """PyMySQL。読取り専用のtransaction（`START TRANSACTION READ ONLY`）と `MAX_EXECUTION_TIME`"""

    dialect = MYSQL

    @contextmanager
    def connect(self) -> Iterator[pymysql.connections.Connection]:
        c = self.connector
        timeout = self.version.timeout_seconds if self.version else 30
        tls = TLSMode(c.tls)
        try:
            conn = pymysql.connect(
                host=c.host,
                port=c.port,
                user=c.username,
                password=c.password.reveal(),
                database=c.database,
                charset="utf8mb4",
                connect_timeout=c.connect_timeout_seconds,
                # サーバーが止めなかったときの保険。MAX_EXECUTION_TIME より少し長くする
                read_timeout=timeout + 10,
                write_timeout=timeout + 10,
                ssl=_ssl_context(tls),
                ssl_disabled=tls == TLSMode.DISABLE,
                autocommit=False,
                program_name="yoriki-connector",
            )
        except pymysql.MySQLError as exc:
            raise ConnectorError(ConnectorErrorKind.CONNECTION, _message(exc)) from exc
        try:
            with conn.cursor() as cur:
                cur.execute("SET SESSION MAX_EXECUTION_TIME = %s", (timeout * 1000,))
                cur.execute("SET SESSION innodb_lock_wait_timeout = %s", (timeout,))
                cur.execute("START TRANSACTION READ ONLY")
            yield conn
        finally:
            # 結果を読み切っていない（上限で打ち切った）接続でも、残りを読まずに閉じる。commitしないので取り消される。
            # PyMySQLは、読みかけの結果を捨てるとき（cursorのclose、結果の__del__）に残りを読み切ろうとするので、
            # その印を外しておく
            result = getattr(conn, "_result", None)
            if result is not None:
                result.unbuffered_active = False
            if conn.open:
                with suppress(pymysql.MySQLError):
                    conn.close()

    def open_cursor(self, conn: pymysql.connections.Connection) -> Any:
        # 結果をバッファしないcursorで少しずつ取り出す
        return conn.cursor(pymysql.cursors.SSCursor)

    def tables_sql(self) -> str:
        return (
            "SELECT table_schema, table_name, table_type FROM information_schema.tables"
            " WHERE table_schema = DATABASE() ORDER BY table_schema, table_name"
        )

    def columns_sql(self) -> str:
        return (
            "SELECT table_schema, table_name, column_name, column_type FROM information_schema.columns"
            " WHERE table_schema = DATABASE() ORDER BY table_schema, table_name, ordinal_position"
        )

    def translate_error(self, exc: Exception) -> ConnectorError | None:
        if isinstance(exc, pymysql.MySQLError):
            if exc.args and exc.args[0] == QUERY_TIMEOUT:
                return ConnectorError(ConnectorErrorKind.TIMEOUT, _("実行時間の上限を超えました"))
            return ConnectorError(ConnectorErrorKind.QUERY, _message(exc))
        return None


def _message(exc: pymysql.MySQLError) -> str:
    return str(exc.args[1]) if len(exc.args) > 1 else str(exc)
