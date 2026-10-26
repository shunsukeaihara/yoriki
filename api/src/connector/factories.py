import factory
from factory.django import DjangoModelFactory

from user.factories import UserFactory

from .models import Connector, ConnectorKind, ConnectorPermission, TLSMode


class ConnectorFactory(DjangoModelFactory[Connector]):
    """Connectorと最初の版を作る。版の項目は `version__tables=[...]` のように指定する"""

    class Meta:
        model = Connector
        skip_postgeneration_save = True  # version() の revise() で保存する

    name = factory.Sequence(lambda n: f"社内DB{n}")
    kind = ConnectorKind.POSTGRESQL
    host = "db.example.co.jp"
    port = 5432
    database = "core"
    username = "yoriki_reader"
    password = "secret-pass-1234"
    tls = TLSMode.DISABLE
    created_by = factory.SubFactory(UserFactory)

    @factory.post_generation
    def version(obj: Connector, create, extracted, **kwargs):
        if not create:
            return
        kwargs.setdefault("description", "")
        kwargs.setdefault("default_schema", ConnectorKind(obj.kind).default_schema)
        kwargs.setdefault("tables", [])
        kwargs.setdefault("max_rows", 1000)
        kwargs.setdefault("max_result_bytes", 10 * 1024 * 1024)
        kwargs.setdefault("timeout_seconds", 30)
        kwargs.setdefault("is_sensitive", False)
        obj.revise(actor=obj.created_by, expected_version=0, **kwargs)


class ConnectorPermissionFactory(DjangoModelFactory[ConnectorPermission]):
    class Meta:
        model = ConnectorPermission

    connector = factory.SubFactory(ConnectorFactory)
    operations = factory.LazyFunction(lambda: ["datasource_describe", "datasource_query"])
