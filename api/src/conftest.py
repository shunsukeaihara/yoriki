import pytest
from django.conf import settings
from rest_framework.test import APIClient

from user.factories import UserFactory
from user.models import User


@pytest.fixture(name="api_client")
def fixture_api_client() -> APIClient:
    return APIClient()


@pytest.fixture(name="app_user")
def fixture_app_user(db) -> User:
    return UserFactory.create()


@pytest.fixture(name="other_user")
def fixture_other_user(db) -> User:
    return UserFactory.create()


@pytest.fixture(name="platform_admin")
def fixture_platform_admin(db) -> User:
    return UserFactory.create(is_superuser=True, is_staff=True)


def pytest_collection_modifyitems(config, items):
    # PostgreSQLの機能（advisory lock、select_for_update、LISTEN/NOTIFY）に依存するテストは、SQLiteでは飛ばす
    if settings.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql":
        return
    skip = pytest.mark.skip(reason="PostgreSQLが必要（YORIKI_TEST_DATABASE=postgres で実行する）")
    for item in items:
        if "postgres" in item.keywords:
            item.add_marker(skip)
