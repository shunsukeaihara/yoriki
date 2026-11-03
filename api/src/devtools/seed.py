"""確認用のデータ。何度流しても同じ状態になるように、名前で探して作るか揃える（docs/development.md §6.1）。

本番の設定では作らない（管理コマンドは DEBUG のときだけ動く）。パスワード・API key等は確認用の固定の値
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from django.contrib.auth.models import Permission
from django.db import connection, models
from psycopg import sql

from connector.models import VERSION_FIELDS as CONNECTOR_FIELDS
from connector.models import Connector, ConnectorKind, ConnectorScope, DatabaseConnection, TLSMode
from llm.models import VERSION_FIELDS as MODEL_FIELDS
from llm.models import LLMConnection, ModelProfile, ModelProfileVersion, ProviderKind
from runtime.models import VERSION_FIELDS as RUNTIME_FIELDS
from runtime.models import RuntimeProfile, RuntimeProfileVersion
from runtime.registry import inspect_image
from user.models import Department, DepartmentMembership, User
from utils.permissions import AppPermission, to_perm_str

DEFAULT_PASSWORD = "yoriki-check"
# seed_dev が作った表・ロールの印（コメント）。印のあるものだけを作り直す・書き換える
MARK = "yoriki seed_dev"
DEFAULT_LLM_URL = "http://127.0.0.1:8765/v1"
# つながらない接続先（discardのポート）。再試行の後のモデルの切り替えを確かめる
UNREACHABLE_LLM_URL = "http://127.0.0.1:9/v1"

DEPARTMENT = "確認用の部門"
CRUD = ("view", "add", "change", "delete")
# （username、表示名、permission）。Groupは手元で消されていることがあるので使わず、permissionを直接付ける
USERS: tuple[tuple[str, str, tuple[AppPermission | str, ...]], ...] = (
    ("check-member", "確認用の社員", ()),
    (
        "check-lead",
        "確認用の部門の担当",
        (
            AppPermission.EDIT_DEPARTMENT_SKILLS,
            AppPermission.PUBLISH_DEPARTMENT_SKILLS,
            AppPermission.MANAGE_DEPARTMENT_TASKS,
        ),
    ),
    (
        "check-admin",
        "確認用の全社の担当",
        (
            AppPermission.MANAGE_ALL_DEPARTMENTS,
            AppPermission.EDIT_DEPARTMENT_SKILLS,
            AppPermission.PUBLISH_DEPARTMENT_SKILLS,
            AppPermission.EDIT_COMPANY_SKILLS,
            AppPermission.PUBLISH_COMPANY_SKILLS,
            AppPermission.MANAGE_DEPARTMENT_TASKS,
            AppPermission.MANAGE_COMPANY_TASKS,
            *(f"connector.{action}_connector" for action in CRUD),
            *(f"connector.{action}_databaseconnection" for action in CRUD),
            *(f"task.{action}_triggerbinding" for action in CRUD),
            *(f"delivery.{action}_destination" for action in CRUD),
        ),
    ),
)
ACTOR = "check-admin"  # 確認用のデータの作成者

FAKE_CONNECTION = "確認用の偽LLM"
UNREACHABLE_CONNECTION = "確認用のつながらないLLM"
MODEL_COMMON = {
    "max_input_tokens": 128_000,
    "max_output_tokens": 8_000,
    "supports_tools": True,
    "supports_images": True,
}
# （名前、接続、版の項目）
MODELS: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("確認用の偽モデル", FAKE_CONNECTION, {"model_name": "fake"}),
    (
        "確認用の偽モデル（推論）",
        FAKE_CONNECTION,
        {
            "model_name": "fake-reasoning",
            "supports_reasoning": True,
            "effort_values": ["low", "medium", "high"],
            "default_effort": "medium",
        },
    ),
    ("確認用のつながらないモデル", UNREACHABLE_CONNECTION, {"model_name": "fake"}),
)

RUNTIME_PROFILE = "確認用の文書処理"

DB_CONNECTION = "確認用DB接続"
CONNECTOR = "確認用DB"
SCHEMA = "connector_check"
DB_ROLE = "connector_check_ro"
DB_ROLE_PASSWORD = "check-ro-pass"
VENDORS = (("V001", "A商事", "T1000000000001"), ("V002", "B物産", "T1000000000002"), ("V003", "C工業", ""))
INVOICES = (
    (1, "V001", "2026-10", 110000),
    (2, "V002", "2026-10", 55000),
    (3, "V001", "2026-11", 132000),
)
TABLES = [
    {
        "schema": SCHEMA,
        "name": "vendors",
        "description": "取引先の台帳",
        "columns": {"code": "取引先コード", "name": "取引先名", "invoice_number": "インボイスの登録番号"},
    },
    {
        "schema": SCHEMA,
        "name": "invoices",
        "description": "請求の記録",
        "columns": {"vendor_code": "取引先コード", "month": "請求の月（YYYY-MM）", "amount": "税込の金額（円）"},
    },
]


class SeedError(Exception):
    """既存のデータが確認用のものと確かめられず、揃えると壊すおそれがある"""


@dataclass
class Seeder:
    password: str = DEFAULT_PASSWORD
    llm_url: str = DEFAULT_LLM_URL
    sandbox_image: str = ""
    database: bool = True
    db_role: str = DB_ROLE
    recreate_tables: bool = False  # 印のない同じ名前の確認用の表も作り直す
    lines: list[str] = field(default_factory=list)

    def run(self) -> list[str]:
        """確認用のデータを作るか揃え、何をしたかの行を返す。transactionは呼び出し元が持つ"""
        department = self._department()
        users = {username: self._user(username, label, perms, department) for username, label, perms in USERS}
        actor = users[ACTOR]
        connections = {
            FAKE_CONNECTION: self._llm_connection(FAKE_CONNECTION, self.llm_url, actor, timeout=60),
            UNREACHABLE_CONNECTION: self._llm_connection(UNREACHABLE_CONNECTION, UNREACHABLE_LLM_URL, actor, timeout=5),
        }
        for name, connection_name, fields in MODELS:
            self._model_profile(name, connections[connection_name], fields, actor)
        if self.sandbox_image:
            self._runtime_profile(actor)
        else:
            self._log("Runtime Profile", "飛ばしました（--sandbox-image で指定すると登録します）")
        if self.database:
            self._check_schema()
            self._connector(actor)
        return self.lines

    def _log(self, kind: str, message: str) -> None:
        self.lines.append(f"{kind}：{message}")

    def _department(self) -> Department:
        department, created = Department.objects.get_or_create(name=DEPARTMENT, deleted=False)
        self._log("部門", f"{DEPARTMENT}（{'作成' if created else 'あり'}）")
        return department

    def _user(self, username: str, label: str, perms: Iterable[AppPermission | str], department: Department) -> User:
        email = f"{username}@example.co.jp"
        user, created = User.objects.get_or_create(username=username, defaults={"email": email, "display_name": label})
        if not created and (user.email != email or user.is_superuser or user.is_system):
            # 確認用のメールアドレスでない同じ名前のユーザーは、確認用のアカウントとして引き取らない
            raise SeedError(
                f"ユーザー {username} は確認用のアカウントではありません（{user.email}）。名前を変えるか消してください"
            )
        user.display_name = label
        user.is_active = True
        user.is_staff = False
        if not user.check_password(self.password):
            # 同じパスワードで設定し直すと、作ったセッション（dev_session、ブラウザ）が無効になる
            user.set_password(self.password)
        user.save()
        user.user_permissions.set([_permission(p) for p in perms])
        user.groups.clear()
        # 所属も権限の軸なので、確認用の部門だけにする（authorization.md §4）
        DepartmentMembership.objects.filter(user=user).exclude(department=department).delete()
        DepartmentMembership.objects.get_or_create(department=department, user=user)
        self._log("ユーザー", f"{username}（{label}。{'作成' if created else '揃えました'}）")
        return user

    def _llm_connection(self, name: str, url: str, actor: User, *, timeout: int) -> LLMConnection:
        conn = LLMConnection.objects.filter(name=name, deleted=False).first()
        if conn is not None and conn.created_by_id != actor.pk and (conn.kind != ProviderKind.LOCAL or conn.api_key):
            # API keyを持つ接続は、確認用の作成者のものでなければ引き取らない（keyを確認用のモデル設定で使わないため）
            raise SeedError(f"LLMの接続「{name}」は確認用のものではありません。名前を変えるか消してください")
        if conn is not None and (conn.kind != ProviderKind.LOCAL or conn.base_url != url):
            # 種別と接続先は作った後に変えない（API keyが別のサーバーへ送られ、過去の版の接続先の意味も変わるため。
            # llm.models.LLMConnection）。元の接続は名前を変えて残し、新しい接続を作る
            conn.name = f"{name}（以前の接続先 {conn.base_url or conn.get_kind_display()}）"[:100]
            conn.save(update_fields=["name", "updated"])
            self._log("LLMの接続", f"{name}（接続先が違うので、元の接続を「{conn.name}」にしました）")
            conn = None
        if conn is None:
            conn = LLMConnection.objects.create(
                name=name, kind=ProviderKind.LOCAL, base_url=url, api_key="", timeout_seconds=timeout, created_by=actor
            )
            self._log("LLMの接続", f"{name}（{url}。作成）")
            return conn
        conn.timeout_seconds = timeout
        conn.is_active = True
        conn.save(update_fields=["timeout_seconds", "is_active", "updated"])
        self._log("LLMの接続", f"{name}（{url}。揃えました）")
        return conn

    def _model_profile(self, name: str, conn: LLMConnection, fields: dict[str, Any], actor: User) -> None:
        content = _version_fields(ModelProfileVersion, MODEL_FIELDS, MODEL_COMMON | fields | {"connection": conn})
        profile = (
            ModelProfile.objects.filter(name=name, deleted=False).select_related("current_version__connection").first()
        )
        if profile is not None and not self._is_check_profile(profile, actor):
            raise SeedError(f"モデル設定「{name}」は確認用のものではありません。名前を変えるか消してください")
        if profile is None:
            profile = ModelProfile.create_with_version(
                name=name, actor=actor, change_reason="確認用（seed_dev）", **content
            )
            state = "作成"
        else:
            before = profile.current_version_id
            number = profile.current_version.number if profile.current_version else 0
            profile.revise(actor=actor, expected_version=number, change_reason="確認用（seed_dev）", **content)
            state = "そのまま" if profile.current_version_id == before else "新しい版"
        if not profile.is_active:
            profile.is_active = True
            profile.save(update_fields=["is_active", "updated"])
        if not ModelProfile.objects.filter(deleted=False, is_default=True).exists():
            profile.make_default()
            state += "。既定にしました"
        self._log("モデル設定", f"{name}（{state}）")

    @staticmethod
    def _is_check_profile(profile: ModelProfile, actor: User) -> bool:
        """確認用の作成者が作ったか、今の版が確認用の接続（名前を変えて残したものを含む）を使う"""
        if profile.created_by_id == actor.pk:
            return True
        conn = profile.current_version.connection if profile.current_version else None
        names = (FAKE_CONNECTION, UNREACHABLE_CONNECTION)
        return conn is not None and any(conn.name == n or conn.name.startswith(f"{n}（以前の接続先") for n in names)

    def _runtime_profile(self, actor: User) -> None:
        profile = RuntimeProfile.objects.filter(name=RUNTIME_PROFILE, deleted=False).first()
        if profile is not None and profile.created_by_id != actor.pk:
            raise SeedError(
                f"Runtime Profile「{RUNTIME_PROFILE}」は確認用の作成者のものではありません。"
                "名前を変えるか消してください"
            )
        inspected = inspect_image(self.sandbox_image)
        image = {
            "image_repository": inspected.reference.repository,
            "image_digest": inspected.reference.digest,
            "contract": inspected.contract,
            "platforms": inspected.platforms,
            "platform_digests": inspected.platform_digests,
            "python_version": inspected.python_version,
        }
        content = _version_fields(RuntimeProfileVersion, RUNTIME_FIELDS, image)
        if profile is None:
            profile = RuntimeProfile.create_with_version(
                name=RUNTIME_PROFILE, actor=actor, change_reason="確認用（seed_dev）", **content
            )
            state = "作成"
        else:
            # 資源の上限は、管理画面で変えた値を残す
            current = profile.current_version
            if current is not None:
                content |= {k: getattr(current, k) for k in RUNTIME_FIELDS if k not in image}
            before = profile.current_version_id
            number = current.number if current else 0
            profile.revise(actor=actor, expected_version=number, change_reason="確認用（seed_dev）", **content)
            state = "そのまま" if profile.current_version_id == before else "新しい版"
        if not profile.is_active:
            profile.is_active = True
            profile.save(update_fields=["is_active", "updated"])
        if not RuntimeProfile.objects.filter(deleted=False, is_default=True).exists():
            profile.make_default()
            state += "。既定にしました"
        self._log("Runtime Profile", f"{RUNTIME_PROFILE}（{inspected.reference.digest[:19]}…。{state}）")

    def _check_schema(self) -> None:
        """Yorikiと同じPostgreSQLに、確認用のスキーマと、読むだけのロールを作る。

        表は毎回作り直す。作り直す・パスワードを設定し直すのは、seed_dev が作った印（コメント）のある表・ロールだけ。
        印のない同じ名前の表（以前に手で作ったもの）は `recreate_tables` のときだけ消す。印のない同じ名前のロールが
        あれば止める（ロールはクラスタで共有なので、ほかの用途のロールのパスワードを変えないため）
        """
        schema, role = sql.Identifier(SCHEMA), sql.Identifier(self.db_role)
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(schema))
            for table in ("vendors", "invoices"):
                cursor.execute("SELECT obj_description(to_regclass(%s), 'pg_class')", [f"{SCHEMA}.{table}"])
                row = cursor.fetchone()
                cursor.execute("SELECT to_regclass(%s) IS NOT NULL", [f"{SCHEMA}.{table}"])
                exists = cursor.fetchone()[0]
                if exists and (row is None or row[0] != MARK) and not self.recreate_tables:
                    raise SeedError(
                        f"{SCHEMA}.{table} は seed_dev が作った表ではありません。消してよければ --recreate-tables を"
                        "付けてください"
                    )
            cursor.execute(sql.SQL("DROP TABLE IF EXISTS {}.vendors, {}.invoices").format(schema, schema))
            cursor.execute(
                sql.SQL(
                    "CREATE TABLE {}.vendors (code text PRIMARY KEY, name text NOT NULL, invoice_number text NOT NULL)"
                ).format(schema)
            )
            cursor.execute(
                sql.SQL(
                    "CREATE TABLE {}.invoices (id integer PRIMARY KEY, vendor_code text NOT NULL, "
                    "month text NOT NULL, amount integer NOT NULL)"
                ).format(schema)
            )
            for table in ("vendors", "invoices"):
                cursor.execute(
                    sql.SQL("COMMENT ON TABLE {}.{} IS {}").format(schema, sql.Identifier(table), sql.Literal(MARK))
                )
            cursor.executemany(f"INSERT INTO {SCHEMA}.vendors VALUES (%s, %s, %s)", VENDORS)
            cursor.executemany(f"INSERT INTO {SCHEMA}.invoices VALUES (%s, %s, %s, %s)", INVOICES)
            self._role(cursor, role)
            cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schema, role))
            # スキーマのほかの表は読ませない（確認用の2つの表だけ）
            cursor.execute(sql.SQL("GRANT SELECT ON {}.vendors, {}.invoices TO {}").format(schema, schema, role))
        self._log(
            "確認用のスキーマ",
            f"{SCHEMA}（取引先 {len(VENDORS):,}件、請求 {len(INVOICES):,}件。ロール {self.db_role}）",
        )

    def _role(self, cursor, role: sql.Identifier) -> None:
        cursor.execute("SELECT shobj_description(oid, 'pg_authid') FROM pg_roles WHERE rolname = %s", [self.db_role])
        row = cursor.fetchone()
        if row is None:
            cursor.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(role, sql.Literal(DB_ROLE_PASSWORD)))
        elif row[0] != MARK:
            # 以前に別の用途で作ったロールかもしれないので、印のないロールは引き取らない
            raise SeedError(
                f"ロール {self.db_role} は seed_dev が作ったロールではありません。別の用途のロールなら名前を変え、"
                "以前の確認で手で作ったものなら消してください（DROP ROLE）"
            )
        else:
            if extra := self._extra_privileges(cursor):
                # 後から付いた権限があれば、固定のパスワードでその権限を使えるようにしない
                raise SeedError(
                    f"ロール {self.db_role} に、確認用の2つの表のほかの権限・持ち物・属性があります（{extra}）。"
                    "外すか、ロールを消してください"
                )
            cursor.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(role, sql.Literal(DB_ROLE_PASSWORD)))
        cursor.execute(sql.SQL("COMMENT ON ROLE {} IS {}").format(role, sql.Literal(MARK)))

    def _extra_privileges(self, cursor) -> str:
        """ロールの、確認用の範囲（このDBの確認用のスキーマのUSAGEと2つの表のSELECT）の外の権限・持ち物・属性。

        表を作り直した後に呼ぶ（前の表に付いていた権限は、表と一緒に消えている）。ほかのDBの権限と持ち物も
        pg_shdepend で分かる
        """
        cursor.execute(
            "SELECT r.oid, r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls, "
            "EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member = r.oid OR m.roleid = r.oid) "
            "FROM pg_roles r WHERE r.rolname = %s",
            [self.db_role],
        )
        oid, privileged, member = cursor.fetchone()
        if privileged:
            return "superuser等の属性"
        if member:
            return "ロールの所属"
        cursor.execute(
            "SELECT count(*) FROM pg_shdepend d WHERE d.refclassid = 'pg_authid'::regclass AND d.refobjid = %s "
            "AND NOT (d.deptype = 'a' AND d.dbid = (SELECT oid FROM pg_database WHERE datname = current_database()) "
            "AND ((d.classid = 'pg_namespace'::regclass AND d.objid = to_regnamespace(%s)::oid) "
            "OR (d.classid = 'pg_class'::regclass AND d.objid IN (to_regclass(%s)::oid, to_regclass(%s)::oid))))",
            [oid, SCHEMA, f"{SCHEMA}.vendors", f"{SCHEMA}.invoices"],
        )
        count = cursor.fetchone()[0]
        if count:
            return f"ほかの権限・持ち物 {count:,}件（ほかのDBのものを含む）"
        # 確認用のスキーマにはUSAGEだけ（表は作り直した直後なので、権限は残っていない）
        cursor.execute(
            "SELECT string_agg(DISTINCT a.privilege_type, ', ') FROM pg_namespace n, aclexplode(n.nspacl) a "
            "WHERE n.nspname = %s AND a.grantee = %s AND a.privilege_type <> 'USAGE'",
            [SCHEMA, oid],
        )
        extra = cursor.fetchone()[0]
        return f"スキーマ {SCHEMA} の {extra}" if extra else ""

    def _connector(self, actor: User) -> None:
        settings_dict = connection.settings_dict
        values = {
            "kind": ConnectorKind.POSTGRESQL,
            "host": settings_dict["HOST"] or "localhost",
            "port": int(settings_dict["PORT"] or 5432),
            "database": settings_dict["NAME"],
            "username": self.db_role,
            "password": DB_ROLE_PASSWORD,
            "tls": TLSMode.DISABLE,
            "is_active": True,
        }
        connector = Connector.objects.filter(name=CONNECTOR, deleted=False).select_related("connection").first()
        if connector is not None and (connector.scope != ConnectorScope.COMPANY or connector.department_id):
            raise SeedError(
                f"同じ名前のConnector「{CONNECTOR}」が全社の範囲ではありません。名前を変えるか消してください"
            )
        conn = (
            connector.connection
            if connector
            else DatabaseConnection.objects.filter(name=DB_CONNECTION, deleted=False).first()
        )
        if conn is not None:
            # 確認用のロールでYorikiのDBへつなぐ接続先だけを揃える（ほかの用途の接続先を書き換えないため）
            # ユーザー名は、seed_dev の印のあるロール（--db-role で前に指定したもの）なら替えてよい
            same_db = (conn.kind, conn.host, conn.port, conn.database) == (
                values["kind"],
                values["host"],
                values["port"],
                values["database"],
            )
            if not same_db or (conn.username != self.db_role and not _is_marked_role(conn.username)):
                raise SeedError(
                    f"接続先「{conn.name}」は、確認用のロール {self.db_role} でこのDBへつなぐものではありません。"
                    "名前を変えるか消してください"
                )
            # ほかのConnectorも使う接続先は書き換えない（そちらの接続も変わるため）
            others = Connector.objects.filter(connection=conn, deleted=False).exclude(name=CONNECTOR)
            if others.exists():
                raise SeedError(
                    f"接続先「{conn.name}」は、ほかのConnector（{', '.join(others.values_list('name', flat=True))}）"
                    "も使っています。確認用DBの接続先を別にしてください"
                )
        if conn is None:
            conn = DatabaseConnection.objects.create(name=DB_CONNECTION, created_by=actor, **values)
        else:
            for key, value in values.items():
                setattr(conn, key, value)
            conn.save()
        content = {
            "description": "確認用の取引先と請求の記録（seed_dev が作るスキーマ）",
            "default_schema": SCHEMA,
            "tables": TABLES,
            "max_rows": 1000,
            "max_result_bytes": 10 * 1024 * 1024,
            "timeout_seconds": 30,
            "is_sensitive": False,
        }
        assert set(content) == set(CONNECTOR_FIELDS)
        if connector is None:
            connector = Connector.create_with_version(
                actor=actor,
                change_reason="確認用（seed_dev）",
                content=content,
                name=CONNECTOR,
                connection=conn,
                scope=ConnectorScope.COMPANY,
            )
            state = "作成"
        else:
            before = connector.current_version_id
            number = connector.current_version.number if connector.current_version else 0
            connector.revise(actor=actor, expected_version=number, change_reason="確認用（seed_dev）", **content)
            state = "そのまま" if connector.current_version_id == before else "新しい版"
            if not connector.is_active:
                connector.is_active = True
                connector.save(update_fields=["is_active", "updated"])
        self._log("Connector", f"{CONNECTOR}（全社。{state}）")


def _is_marked_role(name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT shobj_description(oid, 'pg_authid') FROM pg_roles WHERE rolname = %s", [name])
        row = cursor.fetchone()
    return row is not None and row[0] == MARK


def _permission(perm: AppPermission | str) -> Permission:
    app_label, codename = to_perm_str(perm).split(".", 1)
    return Permission.objects.get(content_type__app_label=app_label, codename=codename)


def _version_fields(model: type[models.Model], names: Iterable[str], values: dict[str, Any]) -> dict[str, Any]:
    """版の項目をすべてそろえる（指定のないものはModelの既定値）。revise() は全項目で前の版と比べる"""
    result = {}
    for name in names:
        if name in values:
            result[name] = values[name]
            continue
        model_field = model._meta.get_field(name)
        assert isinstance(model_field, models.Field)
        result[name] = model_field.get_default()
    return result
