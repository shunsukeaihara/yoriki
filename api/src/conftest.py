import pytest
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
