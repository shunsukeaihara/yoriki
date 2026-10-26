"""ConnectorのAdapterの共通の型（design.md §4.9.4）。

社内DBの3種類（PostgreSQL・MySQL・SQL Server）は同じ契約で実装し、種別ごとの違いは、ドライバ、読取り専用の設定、
実行時間の上限、SQLの方言、表の一覧の取り方だけに閉じ込める（design.md §4.9.2）。
接続はControl Planeと同じ信頼範囲のプロセス（apiかqcluster）の中だけで開き、操作ごとに開いて閉じる。
"""

import csv
import io
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.utils.translation import gettext as _

from ..errors import ConnectorError, ConnectorErrorKind
from ..models import Connector, ConnectorVersion
from ..sql import CheckedQuery, Dialect, check_query

# 管理画面で表を選ぶための一覧の上限
MAX_LISTED_TABLES = 5000
FETCH_SIZE = 500
# INFORMATION_SCHEMA を名前で照会するときの、1回の名前の数
NAMES_PER_QUERY = 1000
# 1行の大きさが分からない最初の取り出し
FIRST_FETCH_SIZE = 10

Param = str | int | float | bool | None


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    type: str
    description: str = ""


@dataclass(frozen=True)
class TableInfo:
    schema: str
    name: str
    kind: str  # "table" か "view"
    columns: tuple[ColumnInfo, ...] = ()
    description: str = ""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    # 行数・サイズの上限で打ち切った（打ち切った後の行は読んでいない）
    truncated: bool = False
    # CSVにしたときのバイト数（打ち切った場合は、打ち切るまで）
    csv_bytes: int = 0
    elapsed_ms: int = 0
    tables: tuple[str, ...] = field(default_factory=tuple)
    sql: str = ""  # 実際に実行したSQL（検査した構文木から作り直したもの）

    def to_csv(self) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(self.columns)
        writer.writerows([to_text(v) for v in row] for row in self.rows)
        return buffer.getvalue()


def to_text(value: Any) -> str:
    """結果の値を、CSVと画面に出す文字列にする"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime | date | dt_time):
        return value.isoformat()
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    if isinstance(value, Decimal | UUID | timedelta):
        return str(value)
    return str(value)


class DatasourceAdapter(ABC):
    """読取り専用の社内DB。管理画面での接続確認・表の一覧と、エージェントの describe・query を受け持つ"""

    dialect: Dialect

    def __init__(self, connector: Connector, version: ConnectorVersion | None = None):
        self.connector = connector
        self.version = version or connector.current_version

    # --- 種別ごとに実装する ---

    @abstractmethod
    def connect(self) -> AbstractContextManager[Any]:
        """読取り専用で、実行時間の上限を設定した接続（DB-API 2.0のconnection）を開く。閉じるときにrollbackする"""

    @abstractmethod
    def open_cursor(self, conn: Any) -> Any:
        """結果を少しずつ取り出すcursor（全行をメモリに読まない）"""

    # INFORMATION_SCHEMA を読む範囲（WHERE の条件）、列の型の列、ドライバの引数の印
    catalog_scope: str
    type_column = "DATA_TYPE"
    param_marker = "%s"

    @abstractmethod
    def translate_error(self, exc: Exception) -> ConnectorError | None:
        """ドライバの例外を ConnectorError にする。ドライバの例外でなければNone"""

    # --- 共通 ---

    @property
    def default_schema(self) -> str:
        assert self.version is not None
        return self.version.default_schema

    def _run(self, fn):
        try:
            return fn()
        except ConnectorError:
            raise
        except Exception as exc:
            error = self.translate_error(exc)
            if error is None:
                raise
            raise error from exc

    def check(self) -> None:
        """接続できるか（ホスト、認証、DB名）を確かめる"""

        def run():
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute("SELECT 1")
                cur.fetchall()

        self._run(run)

    def list_tables(self) -> list[TableInfo]:
        """アカウントが読める表・ビューと列の一覧。管理者が公開する表を選ぶために使う"""
        return self._run(lambda: self._catalog(None))

    def describe(self) -> list[TableInfo]:
        """公開している表・ビューの列と、管理者が付けた説明（`datasource_describe`）。

        公開した表を名前で照会する（一覧の件数の上限に左右されない）。DBにない表（公開した後に消された等）は、
        列を空にして返す
        """
        assert self.version is not None
        names = sorted({entry["name"] for entry in self.version.tables})
        found = {self._key(t.schema, t.name): t for t in self._run(lambda: self._catalog(names))} if names else {}
        result = []
        for entry in self.version.tables:
            schema, name = entry.get("schema", ""), entry["name"]
            table = found.get(self._key(schema, name))
            descriptions: dict[str, str] = entry.get("columns") or {}
            columns = tuple(
                ColumnInfo(c.name, c.type, descriptions.get(c.name, "")) for c in (table.columns if table else ())
            )
            kind = table.kind if table else "table"
            result.append(TableInfo(schema, name, kind, columns, entry.get("description", "")))
        return result

    def _catalog(self, names: list[str] | None) -> list[TableInfo]:
        """INFORMATION_SCHEMA から表・ビューと列を読む。names を渡せば、その名前の表だけ。

        列は、読んだ表の名前で照会する（一覧を件数の上限で打ち切ったときに、載せない表の列まで読まない）。
        名前は同じでスキーマの違う表の列も返るので、表と組み合わせて選ぶ
        """
        with self.connect() as conn:
            cur = conn.cursor()
            tables: list[tuple[str, str, str]] = []
            for condition, params in self._by_names(names):
                cur.execute(
                    "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE FROM INFORMATION_SCHEMA.TABLES"
                    f" WHERE {condition} ORDER BY TABLE_SCHEMA, TABLE_NAME",
                    params,
                )
                tables += [(s, n, k) for s, n, k in cur.fetchmany(MAX_LISTED_TABLES - len(tables))]
                if len(tables) >= MAX_LISTED_TABLES:
                    break
            listed = {(s, n) for s, n, _k in tables}
            columns: dict[tuple[str, str], list[ColumnInfo]] = {}
            for condition, params in self._by_names(sorted({n for _s, n, _k in tables})) if tables else ():
                cur.execute(
                    f"SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, {self.type_column} FROM INFORMATION_SCHEMA.COLUMNS"
                    f" WHERE {condition} ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION",
                    params,
                )
                for schema, table, column, type_ in cur.fetchall():
                    if (schema, table) in listed:
                        columns.setdefault((schema, table), []).append(ColumnInfo(column, type_))
        return [
            TableInfo(
                schema=s,
                name=n,
                kind="view" if "VIEW" in k.upper() else "table",
                columns=tuple(columns.get((s, n), ())),
            )
            for s, n, k in tables
        ]

    def _by_names(self, names: list[str] | None) -> Iterator[tuple[str, tuple[str, ...]]]:
        """INFORMATION_SCHEMA を読む条件と引数。名前は区切って渡す（SQL Serverの引数は2100個まで）"""
        if names is None:
            yield self.catalog_scope, ()
            return
        for start in range(0, len(names), NAMES_PER_QUERY):
            chunk = tuple(names[start : start + NAMES_PER_QUERY])
            yield f"{self.catalog_scope} AND TABLE_NAME IN ({', '.join([self.param_marker] * len(chunk))})", chunk

    def _key(self, schema: str, name: str) -> tuple[str, str]:
        # MySQLのスキーマはDB名（公開する表はスキーマを持たない）
        return (self.connector.database if self.dialect.schema_is_database else schema, name)

    def check_query(self, sql: str, params: Sequence[Param]) -> CheckedQuery:
        assert self.version is not None
        return check_query(
            sql,
            len(params),
            dialect=self.dialect,
            database=self.connector.database,
            default_schema=self.default_schema,
            exposed=[(t.get("schema", ""), t["name"]) for t in self.version.tables],
        )

    def query(self, sql: str, params: Sequence[Param] = ()) -> QueryResult:
        """公開している表・ビューへのSELECT文を実行する（`datasource_query`）。行数・サイズの上限で打ち切る"""
        assert self.version is not None
        checked = self.check_query(sql, params)
        max_rows = self.version.max_rows
        max_bytes = self.version.max_result_bytes

        def run() -> QueryResult:
            started = time.monotonic()
            with self.connect() as conn:
                cur = self.open_cursor(conn)
                cur.execute(checked.sql, tuple(params))
                if cur.description is None:
                    raise ConnectorError(ConnectorErrorKind.REJECTED, _("結果を返さない文は実行できません"))
                columns = [d[0] for d in cur.description]
                result = QueryResult(columns=columns, rows=[], tables=checked.tables, sql=checked.sql)
                result.csv_bytes = _csv_size(columns)
                if result.csv_bytes > max_bytes:
                    raise ConnectorError(
                        ConnectorErrorKind.REJECTED, _("列名だけで結果のサイズの上限を超えます。列を絞ってください")
                    )
                for row in _fetch(cur, result, max_rows, max_bytes):
                    size = _csv_size([to_text(v) for v in row])
                    if len(result.rows) >= max_rows or result.csv_bytes + size > max_bytes:
                        result.truncated = True
                        break
                    result.rows.append(tuple(row))
                    result.csv_bytes += size
            result.elapsed_ms = int((time.monotonic() - started) * 1000)
            return result

        return self._run(run)


def _fetch(cur, result: QueryResult, max_rows: int, max_bytes: int) -> Iterator[Sequence[Any]]:
    """上限を超える分を取り出さないよう、残りの行数と、これまでの1行の大きさから見た残りのバイト数で件数を絞る。
    打ち切ったと分かるように、上限より1行だけ多く取り出す"""
    while True:
        count = len(result.rows)
        size = min(FETCH_SIZE, max_rows - count + 1)
        if count:
            per_row = max(1, (result.csv_bytes - _csv_size(result.columns)) // count)
            size = min(size, max(1, (max_bytes - result.csv_bytes) // per_row + 1))
        else:
            size = min(size, FIRST_FETCH_SIZE)
        rows = cur.fetchmany(size)
        if not rows:
            return
        yield from rows


def _csv_size(values: Sequence[Any]) -> int:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerow(values)
    return len(buffer.getvalue().encode())
