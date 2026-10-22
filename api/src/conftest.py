from typing import Any

import psycopg
import pytest
from django.conf import settings
from rest_framework.test import APIClient

from runtime.factories import RuntimeProfileFactory
from runtime.models import RuntimeProfile
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


@pytest.fixture(name="default_runtime")
def fixture_default_runtime(db) -> RuntimeProfile:
    """既定のRuntime Profile。これがないとRunを始めない（design.md §7.3）"""
    return RuntimeProfileFactory.create(is_default=True)


def pytest_sessionstart(session):
    """PostgreSQLに接続できなければ、接続の待ちで長く止まらずに、起動方法を案内して終わる"""
    db: dict[str, Any] = settings.DATABASES["default"]
    try:
        psycopg.connect(
            host=db["HOST"],
            port=db["PORT"],
            user=db["USER"],
            password=db["PASSWORD"],
            dbname=db["NAME"],
            connect_timeout=3,
        ).close()
    except psycopg.OperationalError as e:
        pytest.exit(
            f"PostgreSQL（{db['HOST']}:{db['PORT']}）に接続できません: {e}\n"
            "テストはPostgreSQLで実行します。リポジトリのルートで次のコマンドを実行して起動してください。\n"
            "  docker compose -f deploy/compose.dev.yaml up -d",
            returncode=2,
        )
