import factory
from django.contrib.auth.models import Permission
from factory.django import DjangoModelFactory

from utils.permissions import AppPermission, to_perm_str

from .models import Department, DepartmentMembership, SSOKind, SSOProvider, User


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


class DepartmentFactory(DjangoModelFactory[Department]):
    class Meta:
        model = Department

    name = factory.Sequence(lambda n: f"部門{n}")


class DepartmentMembershipFactory(DjangoModelFactory[DepartmentMembership]):
    class Meta:
        model = DepartmentMembership

    department = factory.SubFactory(DepartmentFactory)
    user = factory.SubFactory(UserFactory)


def grant(user: User, *perms: AppPermission | str) -> User:
    """ユーザーにpermissionを直接付け、permissionのキャッシュのない新しいオブジェクトを返す。

    判定のテストは、superuserではなく必要なpermissionだけを付けたユーザーで行う（authorization.md §7.2）
    """
    for perm in perms:
        app_label, codename = to_perm_str(perm).split(".", 1)
        user.user_permissions.add(Permission.objects.get(content_type__app_label=app_label, codename=codename))
    return User.objects.get(pk=user.pk)
