import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from django.utils.translation import gettext as _

from ..errors import ConnectorError, ConnectorErrorKind
from ..models import TLSMode
from ..sql import TSQL
from .base import DatasourceAdapter

# ODBCのドライバマネージャーが、ドライバを見つけられない・読み込めないときのSQLSTATE
DRIVER_ERRORS = {"IM002", "IM003", "01000"}

ENCRYPT = {
    TLSMode.DISABLE: "Encrypt=no",
    TLSMode.REQUIRE: "Encrypt=yes;TrustServerCertificate=yes",
    TLSMode.VERIFY: "Encrypt=yes;TrustServerCertificate=no",
}


def _value(value: str) -> str:
    """接続文字列の値を {} で囲む（; や = を含んでもよいように。} は重ねる）"""
    return "{" + value.replace("}", "}}") + "}"


def _pyodbc():
    try:
        import pyodbc  # noqa: PLC0415  ODBCのライブラリ（unixODBC）がない環境でも、他の種別とアプリが動くように
    except ImportError as exc:
        raise ConnectorError(
            ConnectorErrorKind.UNAVAILABLE, _("SQL Serverに接続するためのODBCのライブラリがありません")
        ) from exc
    return pyodbc


class SQLServerAdapter(DatasourceAdapter):
    """pyodbc＋Microsoft ODBC Driver 18。`ApplicationIntent=ReadOnly`、問い合わせのtimeoutと `SET LOCK_TIMEOUT`。

    SQL Serverには読取り専用のtransactionがないので、読取り専用は、`db_datareader` だけのアカウント（第一の防御）と
    SQLの検査で強制する（design.md §4.9.2）。ドライバはMicrosoftのライセンスなので、本体イメージのビルド時に入れる
    """

    dialect = TSQL

    @property
    def driver(self) -> str:
        return os.getenv("YORIKI_CONNECTOR_ODBC_DRIVER", "ODBC Driver 18 for SQL Server")

    @contextmanager
    def connect(self) -> Iterator[Any]:
        pyodbc = _pyodbc()
        c = self.connector
        timeout = self.version.timeout_seconds if self.version else 30
        conn_str = ";".join(
            [
                f"DRIVER={_value(self.driver)}",
                f"SERVER={_value(f'{c.host},{c.port}')}",
                f"DATABASE={_value(c.database)}",
                f"UID={_value(c.username)}",
                f"PWD={_value(c.password.reveal())}",
                ENCRYPT[TLSMode(c.tls)],
                "ApplicationIntent=ReadOnly",
                "APP=yoriki-connector",
            ]
        )
        try:
            conn = pyodbc.connect(conn_str, autocommit=False, timeout=c.connect_timeout_seconds)
        except pyodbc.Error as exc:
            # ドライバが入っていない・読み込めない（IM002、01000）は、接続先ではなくこのサーバーの問題
            kind = ConnectorErrorKind.UNAVAILABLE if exc.args[0] in DRIVER_ERRORS else ConnectorErrorKind.CONNECTION
            raise ConnectorError(kind, _message(exc)) from exc
        try:
            conn.timeout = timeout
            cur = conn.cursor()
            cur.execute(f"SET LOCK_TIMEOUT {int(timeout) * 1000}")
            if self.version and self.default_schema:
                # スキーマを書かない表の名前は、ログインしたユーザーの既定のスキーマで解決される。検査と同じにするため、
                # 違えば使わない（接続ごとに変える方法がない）
                cur.execute("SELECT SCHEMA_NAME()")
                actual = cur.fetchone()[0]
                if (actual or "").casefold() != self.default_schema.casefold():
                    raise ConnectorError(
                        ConnectorErrorKind.CONNECTION,
                        _(
                            "ユーザーの既定のスキーマ（%(actual)s）が、Connectorの既定のスキーマ（%(expected)s）と違います"
                        )
                        % {"actual": actual, "expected": self.default_schema},
                    )
            yield conn
        finally:
            # commitせずに閉じるので、transactionは取り消される
            conn.close()

    def open_cursor(self, conn: Any) -> Any:
        return conn.cursor()

    def tables_sql(self) -> str:
        return (
            "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE FROM INFORMATION_SCHEMA.TABLES"
            " WHERE TABLE_CATALOG = DB_NAME() ORDER BY TABLE_SCHEMA, TABLE_NAME"
        )

    def columns_sql(self) -> str:
        return (
            "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS"
            " WHERE TABLE_CATALOG = DB_NAME() ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION"
        )

    def translate_error(self, exc: Exception) -> ConnectorError | None:
        pyodbc = _pyodbc()
        if isinstance(exc, pyodbc.OperationalError) and exc.args and exc.args[0] == "HYT00":
            return ConnectorError(ConnectorErrorKind.TIMEOUT, _("実行時間の上限を超えました"))
        if isinstance(exc, pyodbc.Error):
            return ConnectorError(ConnectorErrorKind.QUERY, _message(exc))
        return None


def _message(exc: Exception) -> str:
    return str(exc.args[1]) if len(exc.args) > 1 else str(exc)
