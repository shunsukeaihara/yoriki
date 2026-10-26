import pytest

from connector.errors import ConnectorError, ConnectorErrorKind
from connector.sql import MYSQL, POSTGRES, TSQL, Dialect, check_query

EXPOSED = {
    POSTGRES: [("public", "vendors"), ("public", "Items")],
    MYSQL: [("", "vendors")],
    TSQL: [("dbo", "vendors")],
}
DEFAULT_SCHEMA = {POSTGRES: "public", MYSQL: "", TSQL: "dbo"}


def _check(dialect: Dialect, sql: str, params_count: int = 0):
    return check_query(
        sql,
        params_count,
        dialect=dialect,
        database="core",
        default_schema=DEFAULT_SCHEMA[dialect],
        exposed=EXPOSED[dialect],
    )


@pytest.mark.parametrize(
    ("dialect", "sql", "params_count", "expected_sql", "tables"),
    [
        (
            POSTGRES,
            "SELECT * FROM vendors WHERE code = ? AND name LIKE 'a%'",
            1,
            "SELECT * FROM vendors WHERE code = %s AND name LIKE 'a%%'",
            ("public.vendors",),
        ),
        (POSTGRES, "SELECT * FROM core.public.vendors", 0, "SELECT * FROM core.public.vendors", ("public.vendors",)),
        (POSTGRES, 'SELECT * FROM "Items"', 0, 'SELECT * FROM "Items"', ("public.Items",)),
        # CTEはその文の中で定義した名前なので、公開した表でなくてよい
        (
            POSTGRES,
            "WITH s AS (SELECT * FROM vendors) SELECT * FROM s",
            0,
            "WITH s AS (SELECT * FROM vendors) SELECT * FROM s",
            ("public.vendors",),
        ),
        (POSTGRES, "SELECT 1 AS a UNION ALL SELECT 2", 0, "SELECT 1 AS a UNION ALL SELECT 2", ()),
        # コメントは外す（? を数えない）
        (
            POSTGRES,
            "SELECT * FROM vendors -- ?\n WHERE a = ?",
            1,
            "SELECT * FROM vendors WHERE a = %s",
            ("public.vendors",),
        ),
        (
            MYSQL,
            "SELECT * FROM `vendors` WHERE a = ? AND DATE_FORMAT(d, '%Y') = ?",
            2,
            "SELECT * FROM `vendors` WHERE a = %s AND DATE_FORMAT(d, '%%Y') = %s",
            ("core.vendors",),
        ),
        (MYSQL, "SELECT * FROM CORE.Vendors", 0, "SELECT * FROM CORE.Vendors", ("core.vendors",)),
        (
            TSQL,
            "SELECT TOP 5 * FROM vendors WHERE a = ?",
            1,
            "SELECT TOP 5 * FROM vendors WHERE a = ?",
            ("dbo.vendors",),
        ),
        (TSQL, "SELECT * FROM [core].[dbo].[Vendors]", 0, "SELECT * FROM [core].[dbo].[Vendors]", ("dbo.vendors",)),
    ],
)
def test_accepts(dialect, sql, params_count, expected_sql, tables):
    checked = _check(dialect, sql, params_count)
    assert checked.sql == expected_sql
    assert checked.tables == tables


@pytest.mark.parametrize(
    ("dialect", "sql", "params_count", "message"),
    [
        (POSTGRES, "SELECT 1; SELECT 2", 0, "1つだけ"),
        (POSTGRES, "SELECT 1; DELETE FROM vendors", 0, "1つだけ"),
        (POSTGRES, "DELETE FROM vendors", 0, "SELECT文だけ"),
        (POSTGRES, "UPDATE vendors SET a = 1", 0, "SELECT文だけ"),
        (POSTGRES, "COPY vendors TO '/tmp/x'", 0, "SELECT文だけ"),
        (POSTGRES, "EXPLAIN ANALYZE DELETE FROM vendors", 0, "SELECT文だけ"),
        # データを変えるCTE
        (POSTGRES, "WITH d AS (DELETE FROM vendors RETURNING *) SELECT * FROM d", 0, "SELECT文だけ"),
        (POSTGRES, "SELECT * INTO copied FROM vendors", 0, "SELECT文だけ"),
        (POSTGRES, "SELECT * FROM vendors FOR UPDATE", 0, "ロック"),
        (POSTGRES, "SELECT * FROM secret", 0, "公開していない"),
        (POSTGRES, "SELECT * FROM items", 0, "公開していない"),  # 引用符のない名前は小文字（"Items"とは別）
        (POSTGRES, "SELECT * FROM other.vendors", 0, "公開していない"),
        (POSTGRES, "SELECT * FROM vendors WHERE code IN (SELECT code FROM secret)", 0, "公開していない"),
        (POSTGRES, "SELECT * FROM vendors UNION ALL SELECT * FROM secret", 0, "公開していない"),
        (POSTGRES, "SELECT * FROM vendors, LATERAL (SELECT * FROM secret) s", 0, "公開していない"),
        # 別のスコープのCTEと同じ名前の実際の表
        (
            POSTGRES,
            "SELECT (WITH secret AS (SELECT 1) SELECT * FROM secret), * FROM (SELECT * FROM secret) y",
            0,
            "公開していない",
        ),
        (POSTGRES, "SELECT * FROM otherdb.public.vendors", 0, "接続先のDB以外"),
        (POSTGRES, "SELECT * FROM pg_catalog.pg_authid", 0, "公開していない"),
        (POSTGRES, "SELECT pg_read_file('/etc/passwd')", 0, "関数"),
        (POSTGRES, "SELECT * FROM generate_series(1, 3)", 0, "関数"),
        # 文字列のSQLを実行する関数（表の制限をすり抜ける）
        (POSTGRES, "SELECT query_to_xml('SELECT * FROM secret', true, true, '')", 0, "関数"),
        (POSTGRES, "SELECT nextval('seq')", 0, "関数"),
        (POSTGRES, "SELECT * FROM vendors WHERE a = $1", 1, "?"),
        (POSTGRES, "SELECT * FROM vendors WHERE a = :name", 1, "?"),
        (POSTGRES, "SELECT * FROM vendors WHERE a = ?", 0, "引数の数"),
        (POSTGRES, "SELECT * FROM", 0, "解析"),
        (MYSQL, "SELECT * FROM mysql.user", 0, "接続先のDB以外"),
        (MYSQL, "SELECT LOAD_FILE('/etc/passwd')", 0, "関数"),
        (MYSQL, "SELECT SLEEP(10)", 0, "関数"),
        (MYSQL, "SELECT * FROM vendors LOCK IN SHARE MODE", 0, "ロック"),
        (TSQL, "SELECT * FROM srv.core.dbo.vendors", 0, "別のサーバー"),
        (TSQL, "SELECT * FROM sys.objects", 0, "公開していない"),
        (TSQL, "SELECT * FROM vendors WITH (UPDLOCK)", 0, "ヒント"),
        (TSQL, "SELECT * FROM OPENROWSET('SQLNCLI', 'x', 'SELECT 1')", 0, "関数"),
        (TSQL, "EXEC xp_cmdshell 'dir'", 0, "SELECT文だけ"),
        (TSQL, "SELECT @x", 0, "?"),
    ],
)
def test_rejects(dialect, sql, params_count, message):
    with pytest.raises(ConnectorError) as exc_info:
        _check(dialect, sql, params_count)
    assert exc_info.value.kind == ConnectorErrorKind.REJECTED
    assert message in exc_info.value.message


def test_rejects_too_long_sql():
    with pytest.raises(ConnectorError, match="長すぎ"):
        _check(POSTGRES, "SELECT 1 AS a" + " " * 100_000)
