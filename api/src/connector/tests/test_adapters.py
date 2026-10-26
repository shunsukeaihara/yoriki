"""Adapterを実際のDBで確かめる。

PostgreSQLは、テストのDBと同じサーバーに、別のスキーマと読取り用のロールを作って使う（いつも動かす）。
MySQLとSQL Serverは、管理者の接続先を環境変数で渡したときだけ動かす（CIでは backend.yml の connector-db で動かす）：

    YORIKI_CONNECTOR_TEST_MYSQL=mysql://root:<password>@127.0.0.1:3307
    YORIKI_CONNECTOR_TEST_SQLSERVER=mssql://sa:<password>@127.0.0.1:1433

CIでは YORIKI_CONNECTOR_TEST_REQUIRED=1 を付け、接続先がなければ飛ばさずに失敗させる。

どの種別でも、公開した表だけをSELECTでき、公開していない表は検査で断り、検査をすり抜けた書込みは
DBのアカウントか読取り専用のtransactionで止まることを確かめる（design.md §4.9.2の多層の防御）。
"""

import io
import os
from collections.abc import Callable, Iterator
from contextlib import closing
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

import psycopg
import pymysql
import pytest
from django.db import connection
from nanoid import generate

from connector.adapters import get_adapter
from connector.adapters.base import QueryResult, _fetch
from connector.errors import ConnectorError, ConnectorErrorKind
from connector.factories import ConnectorFactory
from connector.models import VERSION_FIELDS, Connector, ConnectorKind, TLSMode

READER_PASSWORD = "Reader-pass-1234"  # SQL Serverのパスワードの規則（大文字・小文字・数字）を満たす
VENDOR_ROWS = 50


@dataclass
class Source:
    """テスト用に作った接続先"""

    kind: ConnectorKind
    host: str
    port: int
    database: str
    username: str
    schema: str  # 表を置いたスキーマ（MySQLは空）
    # 管理者の権限で文を実行する（読取り専用で止まったことの確かめに使う）
    admin_query: Callable[[str], list[tuple[Any, ...]]]

    def qualified(self, name: str) -> str:
        return f"{self.schema or self.database}.{name}"


def _suffix() -> str:
    return generate("abcdefghijklmnopqrstuvwxyz0123456789", 10)


# --- PostgreSQL ---


@pytest.fixture(scope="module", name="postgres_source")
def fixture_postgres_source(django_db_setup, django_db_blocker) -> Iterator[Source]:
    with django_db_blocker.unblock():
        params = connection.get_connection_params()
    suffix = _suffix()
    schema, role = f"connector_test_{suffix}", f"yoriki_reader_{suffix}"
    admin = {
        "host": params["host"],
        "port": params["port"],
        "dbname": params["dbname"],
        "user": params["user"],
        "password": params["password"],
    }

    def admin_query(sql: str) -> list[tuple[Any, ...]]:
        with psycopg.connect(**admin, autocommit=True) as conn:
            cur = conn.execute(sql)
            return cur.fetchall() if cur.description else []

    try:
        for sql in [
            f"CREATE ROLE {role} LOGIN PASSWORD '{READER_PASSWORD}'",
            f"CREATE SCHEMA {schema}",
            f"CREATE TABLE {schema}.vendors (code text PRIMARY KEY, name text, amount numeric(12, 2), registered date)",
            f"INSERT INTO {schema}.vendors SELECT 'V' || lpad(i::text, 3, '0'), '取引先' || i, i * 1000.5,"
            f" date '2026-01-01' + i FROM generate_series(1, {VENDOR_ROWS}) i",
            f"CREATE TABLE {schema}.items (id integer)",
            f"CREATE TABLE {schema}.secret (salary integer)",
            f"INSERT INTO {schema}.secret VALUES (100)",
            f"CREATE VIEW {schema}.slow AS SELECT 1 AS s FROM pg_sleep(3)",
            f"GRANT USAGE ON SCHEMA {schema} TO {role}",
            f"GRANT SELECT ON {schema}.vendors, {schema}.slow TO {role}",
            # 書込みの権限を持たせても、読取り専用のtransactionで止まることを確かめる
            f"GRANT SELECT, INSERT ON {schema}.items TO {role}",
        ]:
            admin_query(sql)
        yield Source(
            ConnectorKind.POSTGRESQL, params["host"], int(params["port"]), params["dbname"], role, schema, admin_query
        )
    finally:
        admin_query(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        admin_query(f"DROP ROLE IF EXISTS {role}")


# --- MySQL ---


def _skip(reason: str) -> None:
    # CI（YORIKI_CONNECTOR_TEST_REQUIRED）では飛ばさずに失敗させる
    if os.getenv("YORIKI_CONNECTOR_TEST_REQUIRED"):
        pytest.fail(reason)
    pytest.skip(reason)


def _admin_url(name: str):
    value = os.getenv(name)
    if not value:
        _skip(f"{name} がない")
        raise AssertionError  # 届かない（型のため）
    url = urlparse(value)
    return url.hostname, url.port, unquote(url.username or ""), unquote(url.password or "")


@pytest.fixture(scope="module", name="mysql_source")
def fixture_mysql_source() -> Iterator[Source]:
    host, port, user, password = _admin_url("YORIKI_CONNECTOR_TEST_MYSQL")
    suffix = _suffix()
    database, reader = f"connector_test_{suffix}", f"reader_{suffix}"
    admin = {"host": host, "port": port or 3306, "user": user, "password": password, "autocommit": True}

    def admin_query(sql: str) -> list[tuple[Any, ...]]:
        with closing(pymysql.connect(**admin)) as conn, conn.cursor() as cur:
            cur.execute(sql)
            return list(cur.fetchall())

    try:
        for sql in [
            f"CREATE DATABASE {database} CHARACTER SET utf8mb4",
            f"CREATE TABLE {database}.vendors (code varchar(10) PRIMARY KEY, name varchar(100), amount decimal(12, 2),"
            " registered date)",
            f"INSERT INTO {database}.vendors WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n"
            f" WHERE i < {VENDOR_ROWS}) SELECT CONCAT('V', LPAD(i, 3, '0')), CONCAT('取引先', i), i * 1000.5,"
            " DATE_ADD('2026-01-01', INTERVAL i DAY) FROM n",
            f"CREATE TABLE {database}.items (id integer)",
            f"CREATE TABLE {database}.secret (salary integer)",
            f"INSERT INTO {database}.secret VALUES (100)",
            f"CREATE VIEW {database}.slow AS SELECT SLEEP(3) AS s",
            f"CREATE USER '{reader}'@'%' IDENTIFIED BY '{READER_PASSWORD}'",
            f"GRANT SELECT ON {database}.vendors TO '{reader}'@'%'",
            f"GRANT SELECT ON {database}.slow TO '{reader}'@'%'",
            f"GRANT SELECT, INSERT ON {database}.items TO '{reader}'@'%'",
        ]:
            admin_query(sql)
        yield Source(ConnectorKind.MYSQL, str(host), port or 3306, database, reader, "", admin_query)
    finally:
        admin_query(f"DROP DATABASE IF EXISTS {database}")
        admin_query(f"DROP USER IF EXISTS '{reader}'@'%'")


# --- SQL Server ---


@pytest.fixture(scope="module", name="sqlserver_source")
def fixture_sqlserver_source() -> Iterator[Source]:
    host, port, user, password = _admin_url("YORIKI_CONNECTOR_TEST_SQLSERVER")
    try:
        import pyodbc  # noqa: PLC0415  ODBCのライブラリがない環境では飛ばす
    except ImportError:
        _skip("pyodbc（unixODBC）を読み込めない")
        raise
    suffix = _suffix()
    database, reader = f"connector_test_{suffix}", f"reader_{suffix}"
    driver = os.getenv("YORIKI_CONNECTOR_ODBC_DRIVER", "ODBC Driver 18 for SQL Server")
    base = f"DRIVER={{{driver}}};SERVER={host},{port or 1433};UID={user};PWD={{{password}}};TrustServerCertificate=yes"

    def run(sql: str, db: str = "master") -> list[tuple[Any, ...]]:
        with closing(pyodbc.connect(f"{base};DATABASE={db}", autocommit=True)) as conn:
            cur = conn.cursor()
            cur.execute(sql)
            return [tuple(r) for r in cur.fetchall()] if cur.description else []

    try:
        run(f"CREATE DATABASE {database}")
        run(f"CREATE LOGIN {reader} WITH PASSWORD = '{READER_PASSWORD}', CHECK_POLICY = OFF")
        for sql in [
            f"CREATE USER {reader} FOR LOGIN {reader} WITH DEFAULT_SCHEMA = dbo",
            "CREATE TABLE dbo.vendors (code nvarchar(10) PRIMARY KEY, name nvarchar(100), amount decimal(12, 2),"
            " registered date)",
            f"WITH n AS (SELECT 1 AS i UNION ALL SELECT i + 1 FROM n WHERE i < {VENDOR_ROWS}) INSERT INTO dbo.vendors"
            " SELECT CONCAT('V', RIGHT('00' + CAST(i AS varchar(3)), 3)), CONCAT(N'取引先', i), i * 1000.5,"
            " DATEADD(day, i, '2026-01-01') FROM n",
            "CREATE TABLE dbo.items (id integer)",
            "CREATE TABLE dbo.secret (salary integer)",
            "INSERT INTO dbo.secret VALUES (100)",
            # 時間のかかる問い合わせ（ビューでは WAITFOR を使えないので、大きな結合で待たせる）
            "CREATE VIEW dbo.slow AS SELECT COUNT_BIG(*) AS s FROM sys.all_columns a CROSS JOIN sys.all_columns b"
            " CROSS JOIN sys.all_columns c",
            f"GRANT SELECT ON dbo.vendors TO {reader}",
            f"GRANT SELECT ON dbo.slow TO {reader}",
            f"GRANT SELECT, INSERT ON dbo.items TO {reader}",
        ]:
            run(sql, database)
        yield Source(
            ConnectorKind.SQLSERVER, str(host), port or 1433, database, reader, "dbo", lambda sql: run(sql, database)
        )
    finally:
        run(f"IF DB_ID('{database}') IS NOT NULL ALTER DATABASE {database} SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
        run(f"DROP DATABASE IF EXISTS {database}")
        run(f"IF SUSER_ID('{reader}') IS NOT NULL DROP LOGIN {reader}")


@pytest.fixture(name="source", params=["postgres_source", "mysql_source", "sqlserver_source"])
def fixture_source(request) -> Source:
    return request.getfixturevalue(request.param)


def _connector(source: Source, **version: Any) -> Connector:
    tables = [{"schema": source.schema, "name": n} for n in ("vendors", "items", "slow")]
    version.setdefault("tables", tables)
    version.setdefault("default_schema", source.schema)
    return ConnectorFactory.create(
        kind=source.kind,
        host=source.host,
        port=source.port,
        database=source.database,
        username=source.username,
        password=READER_PASSWORD,
        tls=TLSMode.DISABLE if source.kind != ConnectorKind.SQLSERVER else TLSMode.REQUIRE,
        **{f"version__{k}": v for k, v in version.items()},
    )


def _revise(connector: Connector, **changes: Any) -> None:
    current = connector.current_version
    content = {name: changes.get(name, getattr(current, name)) for name in VERSION_FIELDS}
    connector.revise(actor=connector.created_by, expected_version=connector.version_number, **content)


@pytest.mark.django_db
class TestDatasource:
    def test_check(self, source):
        connector = _connector(source)
        get_adapter(connector).check()

        connector.password = "wrong-password"
        with pytest.raises(ConnectorError) as exc_info:
            get_adapter(connector).check()
        assert exc_info.value.kind == ConnectorErrorKind.CONNECTION
        assert "wrong-password" not in exc_info.value.message

    def test_list_tables(self, source):
        tables = {t.name.lower(): t for t in get_adapter(_connector(source)).list_tables()}
        # アカウントが読める表だけ（権限のない表は出ない）
        assert {"vendors", "items", "slow"} <= set(tables)
        assert "secret" not in tables
        assert tables["slow"].kind == "view"
        assert [c.name for c in tables["vendors"].columns] == ["code", "name", "amount", "registered"]

    def test_describe(self, source):
        connector = _connector(
            source,
            tables=[
                {
                    "schema": source.schema,
                    "name": "vendors",
                    "description": "取引先",
                    "columns": {"code": "取引先コード"},
                },
                {"schema": source.schema, "name": "removed"},
            ],
        )
        vendors, removed = get_adapter(connector).describe()
        assert vendors.description == "取引先"
        assert [(c.name, c.description) for c in vendors.columns][:2] == [("code", "取引先コード"), ("name", "")]
        # DBにない表（公開した後に消された等）は列を空にする
        assert removed.name == "removed"
        assert removed.columns == ()

    def test_query(self, source):
        result = get_adapter(_connector(source)).query(
            "SELECT code, name, amount, registered FROM vendors WHERE code LIKE 'V00%' AND name <> ? ORDER BY code",
            ["取引先1"],
        )
        assert result.columns == ["code", "name", "amount", "registered"]
        assert [r[0] for r in result.rows] == [f"V00{i}" for i in range(2, 10)]
        assert result.truncated is False
        csv = result.to_csv().splitlines()
        assert csv[0] == "code,name,amount,registered"
        assert csv[1] == "V002,取引先2,2001.00,2026-01-03"
        assert result.csv_bytes == len(result.to_csv().encode())

    def test_query_writes_csv_and_keeps_head(self, source):
        out = io.StringIO()
        result = get_adapter(_connector(source)).query(
            "SELECT code, name FROM vendors ORDER BY code", keep_rows=3, csv_out=out
        )
        lines = out.getvalue().splitlines()
        assert lines[0] == "code,name"
        assert len(lines) == VENDOR_ROWS + 1
        assert result.row_count == VENDOR_ROWS
        assert [r[0] for r in result.rows] == ["V001", "V002", "V003"]
        assert result.csv_bytes == len(out.getvalue().encode())

    def test_truncates_by_rows_and_bytes(self, source):
        connector = _connector(source, max_rows=10)
        result = get_adapter(connector).query("SELECT code FROM vendors ORDER BY code")
        assert len(result.rows) == 10
        assert result.truncated is True

        _revise(connector, max_rows=1000, max_result_bytes=1024)
        result = get_adapter(connector).query("SELECT code, name, amount FROM vendors ORDER BY code")
        assert result.truncated is True
        assert 0 < len(result.rows) < VENDOR_ROWS
        assert result.csv_bytes <= 1024

    def test_header_beyond_byte_limit_is_rejected(self, source):
        connector = _connector(source, max_result_bytes=5)
        with pytest.raises(ConnectorError) as exc_info:
            get_adapter(connector).query("SELECT code, name FROM vendors")
        assert exc_info.value.kind == ConnectorErrorKind.REJECTED

    def test_timeout(self, source):
        connector = _connector(source, timeout_seconds=1)
        with pytest.raises(ConnectorError) as exc_info:
            get_adapter(connector).query("SELECT * FROM slow")
        assert exc_info.value.kind == ConnectorErrorKind.TIMEOUT

    def test_unexposed_table_is_rejected_before_connecting(self, source):
        with pytest.raises(ConnectorError) as exc_info:
            get_adapter(_connector(source)).query("SELECT * FROM secret")
        assert exc_info.value.kind == ConnectorErrorKind.REJECTED

    def test_account_permission_is_the_first_defense(self, source):
        # 管理者が誤って公開しても、アカウントに権限がなければ読めない
        connector = _connector(source, tables=[{"schema": source.schema, "name": "secret"}])
        with pytest.raises(ConnectorError) as exc_info:
            get_adapter(connector).query("SELECT * FROM secret")
        assert exc_info.value.kind == ConnectorErrorKind.QUERY

    def test_write_is_stopped_by_the_database(self, source):
        # 検査をすり抜けた書込み（直接 connect() で実行する）も残らない。PostgreSQL・MySQLは読取り専用のtransactionで
        # 止まり、SQL Serverはcommitしないので取り消される
        adapter = get_adapter(_connector(source))
        with adapter.connect() as conn:
            cur = conn.cursor()
            try:
                cur.execute(f"INSERT INTO {source.qualified('items')} VALUES (1)")
            except Exception as exc:
                error = adapter.translate_error(exc)
                assert error is not None
                assert error.kind == ConnectorErrorKind.QUERY
        assert source.admin_query(f"SELECT COUNT(*) FROM {source.qualified('items')}") == [(0,)]


class FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]]):
        self.rows = rows
        self.sizes: list[int] = []

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        self.sizes.append(size)
        taken, self.rows = self.rows[:size], self.rows[size:]
        return taken


def _consume(cur: FakeCursor, max_rows: int, max_bytes: int) -> QueryResult:
    result = QueryResult(columns=["v"], rows=[])
    result.csv_bytes = len("v\n")
    for row in _fetch(cur, result, max_rows, max_bytes):
        size = len(f"{row[0]}\n".encode())
        if len(result.rows) >= max_rows or result.csv_bytes + size > max_bytes:
            result.truncated = True
            break
        result.rows.append(tuple(row))
        result.row_count += 1
        result.csv_bytes += size
    return result


def test_fetch_does_not_read_beyond_row_limit():
    cur = FakeCursor([(i,) for i in range(1000)])
    result = _consume(cur, max_rows=1, max_bytes=10**9)
    assert result.truncated is True
    assert cur.sizes == [2]  # 上限より1行だけ多く取り出す


def test_fetch_does_not_read_beyond_byte_limit():
    cur = FakeCursor([("x" * 99,) for _ in range(1000)])  # 1行100バイト
    result = _consume(cur, max_rows=10**6, max_bytes=1000)
    assert result.truncated is True
    assert sum(cur.sizes) <= 12
