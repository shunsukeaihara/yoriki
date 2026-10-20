import factory
from factory.django import DjangoModelFactory

from .models import SSOKind, SSOProvider, User


class UserFactory(DjangoModelFactory[User]):
    class Meta:
        model = User

    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.co.jp")
    display_name = factory.Sequence(lambda n: f"社員{n}")


class SSOProviderFactory(DjangoModelFactory[SSOProvider]):
    class Meta:
        model = SSOProvider

    kind = SSOKind.MICROSOFT
    client_id = "test-client"
    client_secret = "test-secret-0123456789"
    tenant_id = factory.LazyAttribute(lambda o: "test-tenant" if o.kind == SSOKind.MICROSOFT else "")
    hosted_domain = factory.LazyAttribute(lambda o: "example.co.jp" if o.kind == SSOKind.GOOGLE else "")
    allowed_email_domains = factory.LazyFunction(lambda: ["example.co.jp"])
    created_by = factory.SubFactory(UserFactory)
