import pytest
from auditlog.models import LogEntry
from django.contrib.auth.models import Group, Permission
from django.test import override_settings
from rest_framework.exceptions import ValidationError

from connector.adapters import get_adapter
from connector.adapters.base import QueryResult
from connector.errors import ConnectorError, ConnectorErrorKind
from connector.factories import ConnectorFactory, ConnectorPermissionFactory, DatabaseConnectionFactory
from connector.models import VERSION_FIELDS, Connector, ConnectorKind, ConnectorVersion, DatabaseConnection, Operation
from connector.serializers import ConnectorUpdateSerializer, DatabaseConnectionUpdateSerializer
from user.factories import UserFactory
from user.models import Department, DepartmentMembership, DepartmentRole
from utils.exceptions import Conflict

CONNECTIONS = "/api/v1/database_connections/"
CONNECTORS = "/api/v1/connectors/"


@pytest.fixture(name="admin_client")
def fixture_admin_client(api_client, platform_admin):
    api_client.force_login(platform_admin)
    return api_client


@pytest.fixture(name="department")
def fixture_department(db) -> Department:
    return Department.objects.create(name="経理部")


def _connection_data(**overrides):
    data = {
        "name": "基幹DB",
        "kind": ConnectorKind.POSTGRESQL,
        "host": "db.example.co.jp",
        "database": "core",
        "username": "yoriki_reader",
        "password": "secret-pass-1234",
    }
    data.update(overrides)
    return data


def _data(on: DatabaseConnection | None = None, **overrides):
    data = {
        "name": "取引先マスタ",
        "connection": (on or DatabaseConnectionFactory.create()).uid,
        "tables": [{"name": "vendors", "description": "取引先", "columns": {"code": "取引先コード"}}],
        "permissions": [],
    }
    data.update(overrides)
    return data


class TestPermission:
    @pytest.mark.django_db
    @pytest.mark.parametrize(
        ("url", "codename"), [(CONNECTIONS, "change_databaseconnection"), (CONNECTORS, "change_connector")]
    )
    def test_only_platform_admin(self, api_client, app_user, url, codename):
        assert api_client.get(url).status_code == 401
        api_client.force_login(app_user)
        assert api_client.get(url).status_code == 403
        # 標準のpermissionは画面のメニューの出し分けに使うだけで、APIは基盤管理者に限る
        app_user.user_permissions.add(Permission.objects.get(codename=codename))
        assert api_client.get(url).status_code == 403


class TestDatabaseConnection:
    @pytest.mark.django_db
    def test_create(self, admin_client, platform_admin):
        res = admin_client.post(CONNECTIONS, _connection_data(), format="json")
        assert res.status_code == 201, res.data
        body = res.json()
        # パスワードは応答に出さない
        assert "password" not in body
        assert body["has_password"] is True
        assert body["password_hint"] == "1234"
        assert body["port"] == 5432  # 種別の既定のポート
        assert body["connector_count"] == 0
        connection = DatabaseConnection.objects.get(uid=body["id"])
        assert connection.password.reveal() == "secret-pass-1234"
        assert connection.created_by == platform_admin

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        ("overrides", "field"),
        [
            ({"password": ""}, "password"),
            ({"host": "db.example.co.jp;Server=evil"}, "host"),
            ({"port": 0}, "port"),
        ],
    )
    def test_validation(self, admin_client, overrides, field):
        res = admin_client.post(CONNECTIONS, _connection_data(**overrides), format="json")
        assert res.status_code == 400
        assert field in res.json()

    @pytest.mark.django_db
    def test_duplicate_name(self, admin_client):
        DatabaseConnectionFactory.create(name="基幹DB")
        res = admin_client.post(CONNECTIONS, _connection_data(), format="json")
        assert res.status_code == 400
        assert "name" in res.json()

    @pytest.mark.django_db
    @override_settings(YORIKI_SECRET_ENCRYPTION_KEYS=[])
    def test_requires_encryption_key(self, admin_client):
        res = admin_client.post(CONNECTIONS, _connection_data(), format="json")
        assert res.status_code == 400
        assert "password" in res.json()

    @pytest.mark.django_db
    def test_password(self, admin_client):
        connection = DatabaseConnectionFactory.create(password="old-pass-0000")
        url = f"{CONNECTIONS}{connection.uid}/"
        # 送らない・空文字は変更しない
        assert admin_client.patch(url, {"username": "reader2"}, format="json").status_code == 200
        assert admin_client.patch(url, {"password": ""}, format="json").status_code == 200
        connection.refresh_from_db()
        assert connection.password.reveal() == "old-pass-0000"
        assert connection.username == "reader2"
        assert admin_client.patch(url, {"password": "new-pass-9999"}, format="json").status_code == 200
        connection.refresh_from_db()
        assert connection.password.reveal() == "new-pass-9999"

    @pytest.mark.django_db
    @pytest.mark.parametrize("change", [{"host": "db2.example.co.jp"}, {"port": 15432}, {"database": "core2"}])
    def test_moving_requires_password(self, admin_client, change):
        connection = DatabaseConnectionFactory.create(password="old-pass-0000")
        url = f"{CONNECTIONS}{connection.uid}/"
        # 登録済みのパスワードを別のサーバーへ送らない
        res = admin_client.patch(url, change, format="json")
        assert res.status_code == 400
        assert "password" in res.json()
        res = admin_client.patch(url, {**change, "password": ""}, format="json")
        assert res.status_code == 400
        res = admin_client.patch(url, {**change, "password": "new-pass-9999"}, format="json")
        assert res.status_code == 200, res.data
        connection.refresh_from_db()
        assert connection.password.reveal() == "new-pass-9999"
        assert all(getattr(connection, name) == value for name, value in change.items())

    @pytest.mark.django_db
    def test_same_address_does_not_require_password(self, admin_client):
        connection = DatabaseConnectionFactory.create(host="db.example.co.jp", port=5432, database="core")
        res = admin_client.patch(
            f"{CONNECTIONS}{connection.uid}/",
            {"host": "db.example.co.jp", "port": 5432, "database": "core", "username": "reader2"},
            format="json",
        )
        assert res.status_code == 200, res.data

    @pytest.mark.django_db
    def test_kind_cannot_change(self, admin_client):
        connection = DatabaseConnectionFactory.create()
        res = admin_client.patch(f"{CONNECTIONS}{connection.uid}/", {"kind": ConnectorKind.MYSQL}, format="json")
        assert res.status_code == 200, res.data
        connection.refresh_from_db()
        assert connection.kind == ConnectorKind.POSTGRESQL

    @pytest.mark.django_db
    def test_password_is_not_audited(self, admin_client):
        connection = DatabaseConnectionFactory.create()
        admin_client.patch(f"{CONNECTIONS}{connection.uid}/", {"password": "new-pass-9999"}, format="json")
        assert all("password" not in (e.changes_dict or {}) for e in LogEntry.objects.get_for_object(connection))

    @pytest.mark.django_db
    def test_delete_in_use(self, admin_client):
        connection = DatabaseConnectionFactory.create()
        connector = ConnectorFactory.create(connection=connection)
        url = f"{CONNECTIONS}{connection.uid}/"
        assert admin_client.get(url).json()["connector_count"] == 1
        assert admin_client.delete(url).status_code == 409
        connector.deleted = True
        connector.save()
        assert admin_client.delete(url).status_code == 204
        connection.refresh_from_db()
        assert connection.deleted is True

    @pytest.mark.django_db
    def test_moving_back_after_concurrent_move_requires_password(self):
        # 取得した後に、別の要求が接続先とパスワードを変えた。古い接続先へ戻す要求は、パスワードなしでは断る
        connection = DatabaseConnectionFactory.create(host="db.example.co.jp", password="old-pass-0000")
        stale = DatabaseConnection.objects.get(pk=connection.pk)
        DatabaseConnection.objects.filter(pk=connection.pk).update(host="db2.example.co.jp")
        connection.refresh_from_db()
        connection.password = "new-pass-9999"
        connection.save()
        serializer = DatabaseConnectionUpdateSerializer(stale, data={"host": "db.example.co.jp"}, partial=True)
        assert serializer.is_valid(), serializer.errors  # 取得した時点の値では、接続先は変わっていない
        with pytest.raises(ValidationError) as exc_info:
            serializer.save()
        assert "password" in exc_info.value.detail
        connection.refresh_from_db()
        assert connection.host == "db2.example.co.jp"

    @pytest.mark.django_db
    def test_update_serializer_keeps_kind(self):
        assert "kind" in DatabaseConnectionUpdateSerializer.Meta.read_only_fields


class TestCreate:
    @pytest.mark.django_db
    def test_create(self, admin_client, platform_admin, department):
        Group.objects.create(name="経理担当")
        connection = DatabaseConnectionFactory.create()
        res = admin_client.post(
            CONNECTORS,
            _data(
                connection,
                permissions=[
                    {"group": "経理担当", "operations": ["datasource_describe"]},
                    {"department": department.uid},
                ],
            ),
            format="json",
        )
        assert res.status_code == 201, res.data
        body = res.json()
        assert body["connection"] == connection.uid
        assert body["connection_info"]["host"] == "db.example.co.jp"
        assert body["kind"] == ConnectorKind.POSTGRESQL
        assert body["version"] == 1
        assert body["default_schema"] == "public"
        assert body["tables"] == [
            {"schema": "public", "name": "vendors", "description": "取引先", "columns": {"code": "取引先コード"}}
        ]
        assert body["is_sensitive"] is False
        operations = {p["group"] or p["department"]: p["operations"] for p in body["permissions"]}
        assert operations == {
            "経理担当": ["datasource_describe"],
            department.uid: ["datasource_describe", "datasource_query"],  # 省略するとすべて
        }

        connector = Connector.objects.get(uid=body["id"])
        assert connector.connection == connection
        assert connector.created_by == platform_admin
        assert connector.current_version is not None
        assert connector.current_version.number == 1
        assert connector.current_version.created_by == platform_admin

    @pytest.mark.django_db
    def test_mysql_has_no_schema(self, admin_client):
        mysql = DatabaseConnectionFactory.create(kind=ConnectorKind.MYSQL, port=3306)
        res = admin_client.post(CONNECTORS, _data(mysql, tables=[{"schema": "x", "name": "vendors"}]), format="json")
        assert res.status_code == 201, res.data
        assert res.json()["default_schema"] == ""
        assert res.json()["tables"][0]["schema"] == ""

        res = admin_client.post(CONNECTORS, _data(mysql, name="別", default_schema="x"), format="json")
        assert res.status_code == 400
        assert "default_schema" in res.json()

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        ("overrides", "field"),
        [
            ({"connection": "missing"}, "connection"),
            ({"tables": [{"name": "vendors"}, {"schema": "public", "name": "vendors"}]}, "tables"),
            ({"permissions": [{"operations": ["datasource_query"]}]}, "permissions"),
            ({"permissions": [{"group": "経理担当", "department": "x"}]}, "permissions"),
            ({"permissions": [{"group": "経理担当", "operations": []}]}, "permissions"),
            ({"permissions": [{"group": "経理担当"}, {"group": "経理担当"}]}, "permissions"),
            ({"max_rows": 0}, "max_rows"),
        ],
    )
    def test_validation(self, admin_client, overrides, field):
        Group.objects.create(name="経理担当")
        res = admin_client.post(CONNECTORS, _data(**overrides), format="json")
        assert res.status_code == 400
        assert field in res.json()

    @pytest.mark.django_db
    def test_duplicate_name(self, admin_client):
        ConnectorFactory.create(name="取引先マスタ")
        res = admin_client.post(CONNECTORS, _data(), format="json")
        assert res.status_code == 400
        assert "name" in res.json()

    @pytest.mark.django_db
    def test_deleted_connection(self, admin_client):
        connection = DatabaseConnectionFactory.create(deleted=True)
        res = admin_client.post(CONNECTORS, _data(connection), format="json")
        assert res.status_code == 400
        assert "connection" in res.json()

    @pytest.mark.django_db
    def test_many_connectors_on_one_connection(self, admin_client):
        connection = DatabaseConnectionFactory.create()
        for name in ("取引先マスタ", "受注実績"):
            assert admin_client.post(CONNECTORS, _data(connection, name=name), format="json").status_code == 201
        assert connection.connectors.count() == 2


class TestUpdate:
    @pytest.mark.django_db
    def test_boundary_change_creates_version(self, admin_client):
        connector = ConnectorFactory.create(version__tables=[{"schema": "public", "name": "vendors"}])
        url = f"{CONNECTORS}{connector.uid}/"
        res = admin_client.patch(url, {"version": 1, "is_sensitive": True, "change_reason": "人事DB"}, format="json")
        assert res.status_code == 200, res.data
        assert res.json()["version"] == 2
        latest = ConnectorVersion.objects.get(connector=connector, number=2)
        assert latest.is_sensitive is True
        assert latest.tables == [{"schema": "public", "name": "vendors"}]  # 送らなかった項目は前の版のまま
        assert latest.change_reason == "人事DB"
        # 前の版は変わらない（進行中のRunが使う）
        assert ConnectorVersion.objects.get(connector=connector, number=1).is_sensitive is False

    @pytest.mark.django_db
    def test_same_boundary_does_not_create_version(self, admin_client):
        connector = ConnectorFactory.create()
        res = admin_client.patch(f"{CONNECTORS}{connector.uid}/", {"version": 1, "name": "受注実績"}, format="json")
        assert res.status_code == 200, res.data
        assert res.json()["version"] == 1
        connector.refresh_from_db()
        assert connector.name == "受注実績"

    @pytest.mark.django_db
    def test_conflict(self, admin_client):
        connector = ConnectorFactory.create()
        connector.revise(actor=connector.created_by, expected_version=1, **_content(connector, max_rows=5))
        res = admin_client.patch(f"{CONNECTORS}{connector.uid}/", {"version": 1, "max_rows": 10}, format="json")
        assert res.status_code == 409

    @pytest.mark.django_db
    def test_requires_version(self, admin_client):
        connector = ConnectorFactory.create()
        res = admin_client.patch(f"{CONNECTORS}{connector.uid}/", {"max_rows": 10}, format="json")
        assert res.status_code == 400
        assert "version" in res.json()

    @pytest.mark.django_db
    def test_connection_cannot_change(self, admin_client):
        connector = ConnectorFactory.create()
        other = DatabaseConnectionFactory.create()
        res = admin_client.patch(
            f"{CONNECTORS}{connector.uid}/", {"version": 1, "connection": other.uid}, format="json"
        )
        assert res.status_code == 200, res.data
        connector.refresh_from_db()
        assert connector.connection != other

    @pytest.mark.django_db
    def test_permissions_are_replaced(self, admin_client, department):
        connector = ConnectorFactory.create()
        ConnectorPermissionFactory.create(connector=connector, group=Group.objects.create(name="経理担当"))
        url = f"{CONNECTORS}{connector.uid}/"
        # 送らなければ変えない
        admin_client.patch(url, {"version": 1}, format="json")
        assert connector.permissions.count() == 1
        res = admin_client.patch(url, {"version": 1, "permissions": [{"department": department.uid}]}, format="json")
        assert res.status_code == 200, res.data
        assert [(p.group, p.department) for p in connector.permissions.all()] == [(None, department)]

    @pytest.mark.django_db
    def test_permission_changes_are_audited(self, admin_client, department):
        connector = ConnectorFactory.create()
        res = admin_client.patch(
            f"{CONNECTORS}{connector.uid}/",
            {"version": 1, "permissions": [{"department": department.uid}]},
            format="json",
        )
        assert res.status_code == 200, res.data
        permission = connector.permissions.get()
        entries = LogEntry.objects.get_for_object(permission)
        assert [e.action for e in entries] == [LogEntry.Action.CREATE]

    @pytest.mark.django_db
    def test_update_does_not_restore_deleted(self, platform_admin):
        # 更新の要求が取得した後に、別の要求が削除した
        connector = ConnectorFactory.create()
        stale = Connector.objects.get(pk=connector.pk)
        Connector.objects.filter(pk=connector.pk).update(deleted=True)
        serializer = ConnectorUpdateSerializer(stale, data={"version": 1, "name": "受注実績"}, partial=True)
        assert serializer.is_valid(), serializer.errors
        with pytest.raises(Conflict):
            serializer.save(actor=platform_admin)
        connector.refresh_from_db()
        assert connector.deleted is True
        assert connector.name != "受注実績"

    @pytest.mark.django_db
    def test_delete(self, admin_client):
        connector = ConnectorFactory.create()
        assert admin_client.delete(f"{CONNECTORS}{connector.uid}/").status_code == 204
        connector.refresh_from_db()
        assert connector.deleted is True
        assert admin_client.get(f"{CONNECTORS}{connector.uid}/").status_code == 404


class TestVersions:
    @pytest.mark.django_db
    def test_list(self, admin_client):
        connector = ConnectorFactory.create()
        connector.revise(actor=connector.created_by, expected_version=1, **_content(connector, max_rows=5))
        res = admin_client.get(f"{CONNECTORS}{connector.uid}/versions/")
        assert res.status_code == 200
        assert [v["number"] for v in res.json()["results"]] == [2, 1]
        assert admin_client.get(f"{CONNECTORS}{connector.uid}/versions/1/").json()["max_rows"] == 1000


class TestOperations:
    """接続先への操作。Adapterは差し替え、APIの形とエラーの扱いを確かめる（実際の接続は test_adapters.py）"""

    @pytest.mark.django_db
    def test_check(self, admin_client, monkeypatch):
        connection = DatabaseConnectionFactory.create()
        monkeypatch.setattr("connector.adapters.postgresql.PostgreSQLAdapter.check", lambda self: None)
        assert admin_client.post(f"{CONNECTIONS}{connection.uid}/check/").json() == {"ok": True, "detail": ""}

        def fail(self):
            raise ConnectorError(ConnectorErrorKind.CONNECTION, "password authentication failed")

        monkeypatch.setattr("connector.adapters.postgresql.PostgreSQLAdapter.check", fail)
        res = admin_client.post(f"{CONNECTIONS}{connection.uid}/check/")
        assert res.json() == {"ok": False, "detail": "password authentication failed"}

    @pytest.mark.django_db
    def test_tables_of_connection(self, admin_client, monkeypatch):
        connection = DatabaseConnectionFactory.create()

        def fail(self):
            raise ConnectorError(ConnectorErrorKind.CONNECTION, "timeout")

        monkeypatch.setattr("connector.adapters.postgresql.PostgreSQLAdapter.list_tables", fail)
        res = admin_client.get(f"{CONNECTIONS}{connection.uid}/tables/")
        assert res.json() == {"ok": False, "detail": "timeout", "tables": []}

    @pytest.mark.django_db
    def test_connector_uses_its_connection(self, monkeypatch):
        connection = DatabaseConnectionFactory.create(kind=ConnectorKind.MYSQL)
        connector = ConnectorFactory.create(connection=connection)
        adapter = get_adapter(connector)
        assert adapter.connection == connection
        assert adapter.version == connector.current_version
        assert get_adapter(connection).version is None

    @pytest.mark.django_db
    def test_query_is_checked_before_connecting(self, admin_client, monkeypatch):
        connector = ConnectorFactory.create(version__tables=[{"schema": "public", "name": "vendors"}])

        def connect(self):
            raise AssertionError("検査で断る問い合わせでは接続しない")

        monkeypatch.setattr("connector.adapters.postgresql.PostgreSQLAdapter.connect", connect)
        res = admin_client.post(f"{CONNECTORS}{connector.uid}/query/", {"sql": "DELETE FROM vendors"}, format="json")
        assert res.status_code == 200
        assert res.json()["ok"] is False
        assert res.json()["error_kind"] == "rejected"

    @pytest.mark.django_db
    def test_query_preview_is_marked(self, admin_client, monkeypatch):
        connector = ConnectorFactory.create(version__tables=[{"schema": "public", "name": "vendors"}])

        def query(self, sql, params, *, keep_rows=None, csv_out=None):
            # 応答に載せる行だけを残させる
            assert keep_rows == 200
            return QueryResult(columns=["id"], rows=[(i,) for i in range(keep_rows)], row_count=250, sql=sql)

        monkeypatch.setattr("connector.adapters.postgresql.PostgreSQLAdapter.query", query)
        body = admin_client.post(
            f"{CONNECTORS}{connector.uid}/query/", {"sql": "SELECT id FROM vendors"}, format="json"
        ).json()
        assert body["ok"] is True
        assert len(body["rows"]) == 200
        assert body["row_count"] == 250
        assert body["preview_truncated"] is True
        assert body["truncated"] is False

    @pytest.mark.django_db
    def test_query_params_must_be_scalars(self, admin_client):
        connector = ConnectorFactory.create()
        res = admin_client.post(
            f"{CONNECTORS}{connector.uid}/query/", {"sql": "SELECT 1", "params": [{"a": 1}]}, format="json"
        )
        assert res.status_code == 400
        assert "params" in res.json()


class TestUsableBy:
    @pytest.mark.django_db
    def test_group_and_department(self, department):
        group = Group.objects.create(name="経理担当")
        by_group = ConnectorFactory.create()
        ConnectorPermissionFactory.create(connector=by_group, group=group, operations=["datasource_describe"])
        by_department = ConnectorFactory.create()
        ConnectorPermissionFactory.create(connector=by_department, department=department)
        ConnectorFactory.create()  # 誰にも許可していない

        member = UserFactory.create()
        member.groups.add(group)
        DepartmentMembership.objects.create(department=department, user=member, role=DepartmentRole.MEMBER)
        assert set(Connector.objects.usable_by(member)) == {by_group, by_department}
        assert by_group.operations_for(member) == {Operation.DATASOURCE_DESCRIBE}
        assert by_department.operations_for(member) == {Operation.DATASOURCE_DESCRIBE, Operation.DATASOURCE_QUERY}

        outsider = UserFactory.create()
        assert list(Connector.objects.usable_by(outsider)) == []
        assert by_department.operations_for(outsider) == set()

    @pytest.mark.django_db
    def test_operations_are_combined(self, department):
        group = Group.objects.create(name="経理担当")
        connector = ConnectorFactory.create()
        ConnectorPermissionFactory.create(connector=connector, group=group, operations=["datasource_describe"])
        ConnectorPermissionFactory.create(connector=connector, department=department, operations=["datasource_query"])
        user = UserFactory.create()
        user.groups.add(group)
        DepartmentMembership.objects.create(department=department, user=user, role=DepartmentRole.MEMBER)
        assert connector.operations_for(user) == {Operation.DATASOURCE_DESCRIBE, Operation.DATASOURCE_QUERY}
        assert list(Connector.objects.usable_by(user)) == [connector]  # 重複しない

    @pytest.mark.django_db
    def test_inactive_deleted_and_removed_department(self, department):
        connector = ConnectorFactory.create()
        ConnectorPermissionFactory.create(connector=connector, department=department)
        user = UserFactory.create()
        DepartmentMembership.objects.create(department=department, user=user, role=DepartmentRole.MEMBER)
        assert list(Connector.objects.usable_by(user)) == [connector]

        department.deleted = True
        department.save()
        assert list(Connector.objects.usable_by(user)) == []
        assert connector.operations_for(user) == set()
        department.deleted = False
        department.save()

        connector.is_active = False
        connector.save()
        assert list(Connector.objects.usable_by(user)) == []
        assert connector.operations_for(user) == set()

        connector.is_active = True
        connector.save()
        # DB接続を止めたら、その上のConnectorも使えない
        connector.connection.is_active = False
        connector.connection.save()
        assert list(Connector.objects.usable_by(user)) == []
        assert connector.operations_for(user) == set()
        connector.connection.is_active = True
        connector.connection.save()

        user.is_active = False
        user.save()
        assert list(Connector.objects.usable_by(user)) == []

    @pytest.mark.django_db
    def test_platform_admin_can_use_all(self, platform_admin):
        connector = ConnectorFactory.create()
        assert list(Connector.objects.usable_by(platform_admin)) == [connector]
        assert connector.operations_for(platform_admin) == set(connector.operations)


def _content(connector: Connector, **overrides):
    current = connector.current_version
    return {name: overrides.get(name, getattr(current, name)) for name in VERSION_FIELDS}
