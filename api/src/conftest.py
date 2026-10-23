import os
from typing import Any

import docker
import psycopg
import pytest
from django.conf import settings
from docker.errors import DockerException, ImageNotFound
from rest_framework.test import APIClient

from runtime.factories import RuntimeProfileFactory
from runtime.models import RuntimeProfile
from user.factories import UserFactory
from user.models import User


@pytest.fixture(autouse=True)
def fixture_storage(settings, tmp_path) -> None:
    """添付・成果物・snapshotの保存先を、テストごとの一時ディレクトリにする"""
    settings.YORIKI_STORAGE = {
        "DEFAULT": "test-main",
        "BACKENDS": {
            "test-main": {"BACKEND": "artifact.storage.LocalStorage", "OPTIONS": {"root": str(tmp_path / "objects")}}
        },
    }


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


@pytest.fixture(name="sandbox_image")
def fixture_sandbox_image(settings, tmp_path) -> str:
    """実際のDockerで動かすSandboxのイメージ（sandbox/README.md の手順でビルドしたもの）。

    イメージがなければテストを飛ばす。CIのSandboxのjobでは `YORIKI_SANDBOX_TEST_REQUIRED` を付けて、飛ばさずに
    失敗させる。Sandboxの実行環境は `YORIKI_SANDBOX_TEST_RUNTIME`（契約テストと同じ。空ならDockerの既定）
    """
    image = os.getenv("YORIKI_SANDBOX_TEST_IMAGE", "yoriki-sandbox-docs:dev")
    try:
        docker.from_env().images.get(image)
    except (DockerException, ImageNotFound) as exc:
        if os.getenv("YORIKI_SANDBOX_TEST_REQUIRED"):
            raise
        pytest.skip(f"the sandbox image {image} is not available: {exc}")
    settings.YORIKI_SANDBOX_IMAGE_OVERRIDE = image
    settings.YORIKI_SANDBOX_RUNTIME = os.getenv("YORIKI_SANDBOX_TEST_RUNTIME", "")
    settings.YORIKI_WORKSPACES_DIR = str(tmp_path / "workspaces")
    return image


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
