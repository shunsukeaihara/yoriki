"""社内DBへの問い合わせの検査（design.md §4.9.2の3・4）。

書込みの禁止の第一の防御はDBのアカウント（SELECT権限だけ）と読取り専用のtransactionで、ここはその次の層。
sqlglotで各DBの方言として構文解析し、1つのSELECT文だけで、公開した表・ビューだけを読むことを確かめる。

実行するSQLは、検査した構文木から作り直したものにする（コメントは外す）。元の文字列をそのまま流すと、
sqlglotとDBの解析の違い（コメントや引用符の扱い等）で、検査したものと別の文が実行されるおそれがあるため。
引数の場所は `?` で書かせ、ドライバの形（`%s` か `?`）に置き換える。
"""

from collections.abc import Iterable
from dataclasses import dataclass

import sqlglot
from django.utils.translation import gettext as _
from nanoid import generate
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import Scope, traverse_scope

from .errors import ConnectorError, ConnectorErrorKind
from .models import ConnectorKind

MAX_SQL_LENGTH = 100_000

# 書込み・定義の変更・権限・transactionの操作。データを変えるCTE（`WITH d AS (DELETE ...)`）もあるので、
# 文の中のどこにも許さない
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, name)
    for name in (
        "Insert",
        "Update",
        "Delete",
        "Merge",
        "Create",
        "Drop",
        "Alter",
        "TruncateTable",
        "Command",
        "Copy",
        "Set",
        "Transaction",
        "Commit",
        "Rollback",
        "Grant",
        "Revoke",
        "Into",
        "Execute",
        "LoadData",
        "Use",
        "Pragma",
    )
    if hasattr(exp, name)
)

# ファイル・外部への入出力、別の文の実行（表の制限をすり抜ける）、ロック、待機を伴う関数。DBのアカウントの権限が
# 第一の防御なので、ここは知られたものを止める補助（design.md §4.9.2）
FORBIDDEN_FUNCTIONS = {
    # PostgreSQL：文字列の問い合わせを実行するもの、ファイル・大きなオブジェクト、設定の変更
    "query_to_xml",
    "query_to_xmlschema",
    "query_to_xml_and_xmlschema",
    "cursor_to_xml",
    "cursor_to_xmlschema",
    "table_to_xml",
    "table_to_xmlschema",
    "table_to_xml_and_xmlschema",
    "schema_to_xml",
    "schema_to_xmlschema",
    "schema_to_xml_and_xmlschema",
    "database_to_xml",
    "database_to_xmlschema",
    "database_to_xml_and_xmlschema",
    "ts_stat",
    "ts_rewrite",
    "set_config",
    "nextval",
    "setval",
    "txid_current",
    # MySQL
    "load_file",
    "sleep",
    "benchmark",
    "get_lock",
    "release_lock",
    "release_all_locks",
    "is_free_lock",
    "is_used_lock",
    "master_pos_wait",
    "source_pos_wait",
    # SQL Server
    "openrowset",
    "opendatasource",
    "openquery",
    "openxml",
}
FORBIDDEN_FUNCTION_PREFIXES = ("pg_", "lo_", "dblink", "xp_", "sp_")


@dataclass(frozen=True)
class CheckedQuery:
    sql: str  # 実行するSQL（ドライバの引数の形）
    tables: tuple[str, ...]  # 読む表・ビュー（schema.name）


@dataclass(frozen=True)
class Dialect:
    """方言ごとの違い"""

    name: str  # sqlglotの方言
    paramstyle: str  # "format"（%s）か "qmark"（?）
    case_sensitive: bool  # 引用符で囲んだ名前の大文字・小文字を区別する（PostgreSQL）
    schema_is_database: bool  # スキーマがDB名と同じ（MySQL）


POSTGRES = Dialect("postgres", "format", case_sensitive=True, schema_is_database=False)
MYSQL = Dialect("mysql", "format", case_sensitive=False, schema_is_database=True)
TSQL = Dialect("tsql", "qmark", case_sensitive=False, schema_is_database=False)
DIALECTS = {ConnectorKind.POSTGRESQL: POSTGRES, ConnectorKind.MYSQL: MYSQL, ConnectorKind.SQLSERVER: TSQL}


def _reject(message: str) -> ConnectorError:
    return ConnectorError(ConnectorErrorKind.REJECTED, message)


def _identifier(node: exp.Expression | None, dialect: Dialect) -> str:
    """名前の比べ方をそろえる。PostgreSQLは引用符のない名前を小文字にする。他は大文字・小文字を区別しない"""
    if node is None:
        return ""
    if not isinstance(node, exp.Identifier):
        raise _reject(_("この書き方の表の名前は使えません"))
    if dialect.case_sensitive:
        return node.this if node.quoted else node.this.lower()
    return node.this.casefold()


def table_key(schema: str, name: str, dialect: Dialect) -> tuple[str, str]:
    """公開する表・ビューの名前（DBの中の実際の名前）を、比べる形にする"""
    if dialect.case_sensitive:
        return (schema, name)
    return (schema.casefold(), name.casefold())


def check_query(
    sql: str,
    params_count: int,
    *,
    dialect: Dialect,
    database: str,
    default_schema: str,
    exposed: Iterable[tuple[str, str]],
) -> CheckedQuery:
    """SELECT文を検査し、実行するSQLを返す。受け付けなければ ConnectorError（REJECTED）。

    `exposed` は公開する表・ビューの（スキーマ、名前）。MySQLではスキーマはDB名なので、どの値でもよい
    """
    if len(sql) > MAX_SQL_LENGTH:
        raise _reject(_("SQLが長すぎます"))
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect.name) if s is not None]
    except SqlglotError as exc:
        raise _reject(_("SQLを解析できません：%(error)s") % {"error": str(exc).splitlines()[0]}) from exc
    if len(statements) != 1:
        raise _reject(_("SELECT文を1つだけ書いてください"))
    root = statements[0]
    if not isinstance(root, exp.Select | exp.SetOperation):
        raise _reject(_("SELECT文だけを実行できます"))

    for node in root.walk():
        _check_node(node)

    tables = _check_tables(root, dialect=dialect, database=database, default_schema=default_schema, exposed=exposed)

    placeholders = list(root.find_all(exp.Placeholder))
    if len(placeholders) != params_count:
        raise _reject(
            _("引数の数（%(params)d）が、SQLの ? の数（%(placeholders)d）と合いません")
            % {"params": params_count, "placeholders": len(placeholders)}
        )
    return CheckedQuery(sql=_render(root, placeholders, sql, dialect), tables=tables)


def _check_node(node: exp.Expr) -> None:
    if isinstance(node, FORBIDDEN_NODES):
        raise _reject(_("SELECT文だけを実行できます（書込み・SELECT INTO・定義の変更は使えません）"))
    if isinstance(node, exp.Select) and node.args.get("locks"):
        raise _reject(_("ロックを取る読取り（FOR UPDATE等）は使えません"))
    if isinstance(node, exp.Table) and node.args.get("hints"):
        raise _reject(_("表のヒント（WITH (NOLOCK)等）は使えません"))
    if isinstance(node, exp.Parameter):
        raise _reject(_("引数の場所は ? で書いてください"))
    if isinstance(node, exp.Placeholder) and node.this:
        raise _reject(_("引数の場所は ? で書いてください（名前付きの引数は使えません）"))
    if isinstance(node, exp.Func):
        name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
        if name in FORBIDDEN_FUNCTIONS or name.startswith(FORBIDDEN_FUNCTION_PREFIXES):
            raise _reject(_("この関数は使えません：%(name)s") % {"name": name})


def _check_tables(
    root: exp.Expression, *, dialect: Dialect, database: str, default_schema: str, exposed: Iterable[tuple[str, str]]
) -> tuple[str, ...]:
    """文の中のすべての表が、同じ文のCTEか、公開した表・ビューであることを確かめる"""
    allowed = {table_key(database if dialect.schema_is_database else s, n, dialect) for s, n in exposed}
    database_key = database if dialect.case_sensitive else database.casefold()
    default_key = table_key(default_schema, "", dialect)[0]
    used: list[str] = []
    for table in _real_tables(root):
        schema, name = _resolve(table, dialect=dialect, database_key=database_key, default_key=default_key)
        if (schema, name) not in allowed:
            raise _reject(_("公開していない表・ビューです：%(name)s") % {"name": table.sql(dialect.name)})
        if not table.args.get("db"):
            # スキーマを書かない名前は、DBが別のスキーマで解決することがある（SQL Serverの dbo、PostgreSQLの
            # pg_catalog への読み替え）。検査したスキーマを付けて、実行時の解決を固定する
            table.set("db", exp.to_identifier(database if dialect.schema_is_database else default_schema, quoted=True))
        used.append(f"{schema}.{name}")
    return tuple(dict.fromkeys(used))


def _real_tables(root: exp.Expression) -> list[exp.Table]:
    """CTEの参照を除いた、実際の表の参照"""
    try:
        scopes = traverse_scope(root)
    except SqlglotError as exc:
        raise _reject(_("SQLを解析できません：%(error)s") % {"error": str(exc).splitlines()[0]}) from exc

    # CTEの参照かどうかは、その表が書かれたスコープで決める（別のスコープのCTEと同じ名前の実際の表を見分ける）
    cte_refs: set[int] = set()
    real: list[exp.Table] = []
    for scope in scopes:
        for table in scope.tables:
            source = scope.sources.get(table.alias_or_name)
            if isinstance(source, Scope) and not table.args.get("db") and table.name in scope.cte_sources:
                cte_refs.add(id(table))
            else:
                real.append(table)
    # スコープに現れない表（関数の引数の中等）は、どちらと決められないので受け付けない
    known = cte_refs | {id(t) for t in real}
    if any(id(table) not in known for table in root.find_all(exp.Table)):
        raise _reject(_("この書き方の表は使えません"))
    return real


def _resolve(table: exp.Table, *, dialect: Dialect, database_key: str, default_key: str) -> tuple[str, str]:
    """表の参照を、比べる形の（スキーマ、名前）にする"""
    if isinstance(table.this, exp.Dot):
        raise _reject(_("別のサーバーの表は使えません"))
    if not isinstance(table.this, exp.Identifier):
        raise _reject(_("表を返す関数は使えません"))
    catalog = _identifier(table.args.get("catalog"), dialect)
    schema = _identifier(table.args.get("db"), dialect)
    name = _identifier(table.this, dialect)
    if dialect.schema_is_database:
        # MySQLのスキーマはDB名。別のDBの表は読まない
        if catalog or (schema and schema != database_key):
            raise _reject(_("接続先のDB以外の表は使えません"))
        return database_key, name
    if catalog and catalog != database_key:
        raise _reject(_("接続先のDB以外の表は使えません"))
    return schema or default_key, name


def _render(root: exp.Expression, placeholders: list[exp.Placeholder], original: str, dialect: Dialect) -> str:
    """検査した構文木から、ドライバに渡すSQLを作る"""
    if dialect.paramstyle == "qmark":
        return root.sql(dialect=dialect.name, comments=False)
    # %s の形では、文字列の中の % も %% にする。? の場所は、元のSQLにない印に置き換えてから戻す
    marker = f"yoriki_param_{generate('abcdefghijklmnopqrstuvwxyz', 16)}"
    assert marker not in original
    for placeholder in placeholders:
        placeholder.replace(exp.var(marker))
    rendered = root.sql(dialect=dialect.name, comments=False).replace("%", "%%")
    return rendered.replace(marker, "%s")
