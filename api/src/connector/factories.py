import factory
from factory.django import DjangoModelFactory

from user.factories import UserFactory

from .models import Connector, ConnectorKind, ConnectorScope, DatabaseConnection, TLSMode


class DatabaseConnectionFactory(DjangoModelFactory[DatabaseConnection]):
    class Meta:
        model = DatabaseConnection

    name = factory.Sequence(lambda n: f"社内DB接続{n}")
    kind = ConnectorKind.POSTGRESQL
    host = "db.example.co.jp"
    port = 5432
    database = "core"
    username = "yoriki_reader"
    password = "secret-pass-1234"
    tls = TLSMode.DISABLE
    created_by = factory.SubFactory(UserFactory)


class ConnectorFactory(DjangoModelFactory[Connector]):
    """Connectorと最初の版を作る。版の項目は `version__tables=[...]`、接続の項目は `connection__kind=...` のように
    指定する
    """

    class Meta:
        model = Connector
        skip_postgeneration_save = True  # version() の revise() で保存する

    name = factory.Sequence(lambda n: f"社内DB{n}")
    connection = factory.SubFactory(DatabaseConnectionFactory)
    scope = ConnectorScope.COMPANY  # 部門の範囲にするときは department も渡す
    created_by = factory.SubFactory(UserFactory)

    @factory.post_generation
    def version(obj: Connector, create, extracted, **kwargs):
        if not create:
            return
        kwargs.setdefault("description", "")
        kwargs.setdefault("default_schema", ConnectorKind(obj.connection.kind).default_schema)
        kwargs.setdefault("tables", [])
        kwargs.setdefault("max_rows", 1000)
        kwargs.setdefault("max_result_bytes", 10 * 1024 * 1024)
        kwargs.setdefault("timeout_seconds", 30)
        kwargs.setdefault("is_sensitive", False)
        obj.revise(actor=obj.created_by, expected_version=0, **kwargs)
