from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from django.utils.translation import gettext as _
from psycopg import errors

from ..errors import ConnectorError, ConnectorErrorKind
from ..models import TLSMode
from ..sql import POSTGRES
from .base import DatasourceAdapter

SSL_MODES = {TLSMode.DISABLE: "disable", TLSMode.REQUIRE: "require", TLSMode.VERIFY: "verify-full"}


class PostgreSQLAdapter(DatasourceAdapter):
    """psycopg 3。読取り専用のtransaction（`default_transaction_read_only`、`BEGIN READ ONLY`）と `statement_timeout`"""

    dialect = POSTGRES
    catalog_scope = "TABLE_SCHEMA NOT IN ('pg_catalog', 'information_schema') AND TABLE_SCHEMA NOT LIKE 'pg\\_%%'"

    @contextmanager
    def connect(self) -> Iterator[psycopg.Connection]:
        c = self.connector
        timeout_ms = (self.version.timeout_seconds if self.version else 30) * 1000
        options: dict[str, Any] = {}
        if c.tls == TLSMode.VERIFY:
            options["sslrootcert"] = "system"  # OSの証明書を使う（libpq 16以降）
        try:
            conn = psycopg.connect(
                host=c.host,
                port=c.port,
                dbname=c.database,
                user=c.username,
                password=c.password.reveal(),
                connect_timeout=c.connect_timeout_seconds,
                sslmode=SSL_MODES[TLSMode(c.tls)],
                application_name="yoriki-connector",
                # 数字と決まった語だけなので、そのまま埋め込める
                options=(
                    "-c default_transaction_read_only=on"
                    f" -c statement_timeout={timeout_ms} -c lock_timeout={timeout_ms}"
                ),
                **options,
            )
        except psycopg.Error as exc:
            raise ConnectorError(ConnectorErrorKind.CONNECTION, _message(exc)) from exc
        try:
            conn.read_only = True
            yield conn
        finally:
            # commitせずに閉じるので、transactionは取り消される
            conn.close()

    def open_cursor(self, conn: psycopg.Connection) -> Any:
        # サーバー側のcursorで少しずつ取り出す（上限で打ち切った残りの行を読まない）
        return conn.cursor(name="yoriki_query")

    def translate_error(self, exc: Exception) -> ConnectorError | None:
        if isinstance(exc, errors.QueryCanceled):
            return ConnectorError(ConnectorErrorKind.TIMEOUT, _("実行時間の上限を超えました"))
        if isinstance(exc, psycopg.Error):
            return ConnectorError(ConnectorErrorKind.QUERY, _message(exc))
        return None


def _message(exc: psycopg.Error) -> str:
    return exc.diag.message_primary if exc.diag and exc.diag.message_primary else str(exc).strip()
