"""確認用のデータ（seed_dev）とセッション（dev_session）"""

import io
import uuid

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from connector.models import Connector
from devtools import seed
from devtools.seed import Seeder
from llm.factories import ModelProfileFactory
from llm.models import LLMConnection, ModelProfile
from runtime.models import RuntimeProfile
from runtime.registry import ImageReference, InspectedImage
from user.models import Department, User
from utils.permissions import AppPermission

pytestmark = pytest.mark.django_db

DIGEST = "sha256:" + "d" * 64


def current(profile: ModelProfile | RuntimeProfile):
    assert profile.current_version is not None
    return profile.current_version


def counts() -> dict[str, int]:
    return {
        "users": User.objects.count(),
        "departments": Department.objects.count(),
        "connections": LLMConnection.objects.count(),
        "profiles": ModelProfile.objects.count(),
        "versions": sum(p.versions.count() for p in ModelProfile.objects.all()),
        "connectors": Connector.objects.count(),
    }


@pytest.fixture(name="seeder")
def fixture_seeder() -> Seeder:
    # ロールはPostgreSQLのクラスタで共有なので、テストごとに別の名前にする（テストのtransactionで消える）
    return Seeder(db_role=f"check_ro_{uuid.uuid4().hex[:8]}")


def test_seed_is_idempotent(seeder):
    seeder.run()
    before = counts()
    second = Seeder(db_role=seeder.db_role)
    second.run()
    assert counts() == before
    assert "モデル設定：確認用の偽モデル（そのまま）" in second.lines
    assert "Connector：確認用DB（全社。そのまま）" in second.lines

    member, lead, admin = (User.objects.get(username=u) for u, _, _ in seed.USERS)
    assert member.check_password(seed.DEFAULT_PASSWORD)
    assert not member.user_permissions.exists()
    assert lead.has_perm(AppPermission.PUBLISH_DEPARTMENT_SKILLS.perm)
    assert not lead.has_perm(AppPermission.PUBLISH_COMPANY_SKILLS.perm)
    assert admin.has_perm("connector.add_connector") and admin.has_perm("delivery.add_destination")
    assert all(Department.objects.joined_by(u).filter(name=seed.DEPARTMENT).exists() for u in (member, lead, admin))

    fake = ModelProfile.objects.get(name="確認用の偽モデル")
    assert fake.is_default
    assert current(fake).connection.base_url == seed.DEFAULT_LLM_URL
    reasoning = current(ModelProfile.objects.get(name="確認用の偽モデル（推論）"))
    assert reasoning.supports_reasoning and reasoning.effort_values == ["low", "medium", "high"]

    connector = Connector.objects.get(name=seed.CONNECTOR)
    assert connector.connection.username == seeder.db_role
    assert connector.connection.database == connection.settings_dict["NAME"]
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT name FROM {seed.SCHEMA}.vendors ORDER BY code")
        assert [row[0] for row in cursor.fetchall()] == ["A商事", "B物産", "C工業"]
        cursor.execute("SELECT has_table_privilege(%s, %s, 'SELECT')", [seeder.db_role, f"{seed.SCHEMA}.invoices"])
        assert cursor.fetchone()[0]


def test_seed_restores_changed_data(seeder):
    """確認の途中で変えたもの（無効にした、接続先・中身を変えた、permissionを足した）を元に戻す"""
    seeder.run()
    profile = ModelProfile.objects.get(name="確認用の偽モデル")
    profile.revise(
        actor=profile.created_by,
        expected_version=1,
        **{name: getattr(profile.current_version, name) for name in seed.MODEL_FIELDS if name != "max_output_tokens"},
        max_output_tokens=10,
    )
    ModelProfile.objects.filter(pk=profile.pk).update(is_active=False)
    LLMConnection.objects.filter(name=seed.FAKE_CONNECTION).update(base_url="http://127.0.0.1:1/v1")
    member = User.objects.get(username="check-member")
    member.user_permissions.add(seed._permission(AppPermission.PUBLISH_COMPANY_SKILLS))

    Seeder(db_role=seeder.db_role, llm_url="http://127.0.0.1:9999/v1").run()
    profile.refresh_from_db()
    assert profile.is_active
    assert (current(profile).number, current(profile).max_output_tokens) == (3, 8_000)
    assert LLMConnection.objects.get(name=seed.FAKE_CONNECTION).base_url == "http://127.0.0.1:9999/v1"
    assert not member.user_permissions.exists()


def test_seed_keeps_another_default_model(seeder):
    ModelProfileFactory.create(is_default=True)
    seeder.run()
    assert not ModelProfile.objects.get(name="確認用の偽モデル").is_default


def test_seed_registers_runtime_profile(seeder, monkeypatch):
    def inspect(value):
        assert value == f"localhost:5001/yoriki-sandbox-docs@{DIGEST}"
        return InspectedImage(
            reference=ImageReference("localhost:5001", "yoriki-sandbox-docs", DIGEST),
            contract=2,
            platforms=["linux/arm64"],
            platform_digests={"linux/arm64": DIGEST},
            python_version="3.13.16",
            profile_label="docs",
        )

    monkeypatch.setattr(seed, "inspect_image", inspect)
    seeder.sandbox_image = f"localhost:5001/yoriki-sandbox-docs@{DIGEST}"
    seeder.database = False
    seeder.run()
    profile = RuntimeProfile.objects.get(name=seed.RUNTIME_PROFILE)
    assert profile.is_default
    assert current(profile).image_repository == "localhost:5001/yoriki-sandbox-docs"
    # 管理画面で変えた資源の上限は残す
    version = profile.current_version
    fields = {name: getattr(version, name) for name in seed.RUNTIME_FIELDS} | {"memory_mb": 4096}
    profile.revise(actor=profile.created_by, expected_version=1, **fields)
    Seeder(sandbox_image=seeder.sandbox_image, database=False).run()
    profile.refresh_from_db()
    assert (current(profile).number, current(profile).memory_mb) == (2, 4096)


def test_commands_need_debug(settings):
    settings.DEBUG = False
    for name in ("seed_dev", "fake_llm"):
        with pytest.raises(CommandError, match="YORIKI_DEBUG"):
            call_command(name)
    with pytest.raises(CommandError, match="YORIKI_DEBUG"):
        call_command("dev_session", "check-member")


def test_seed_dev_dry_run_changes_nothing(settings):
    settings.DEBUG = True
    out = io.StringIO()
    call_command("seed_dev", "--dry-run", "--skip-database", stdout=out)
    assert "ユーザー：check-member（確認用の社員。作成）" in out.getvalue()
    assert not User.objects.filter(username="check-member").exists()

    call_command("seed_dev", "--skip-database", stdout=io.StringIO())
    assert User.objects.filter(username="check-member").exists()
    assert not Connector.objects.exists()


def test_dev_session(settings):
    settings.DEBUG = True
    call_command("seed_dev", "--skip-database", stdout=io.StringIO())
    out = io.StringIO()
    call_command("dev_session", "check-lead@example.co.jp", stdout=out)
    name, key = out.getvalue().strip().split("=", 1)
    assert name == settings.SESSION_COOKIE_NAME
    assert SessionStore(session_key=key).get("_auth_user_id") == str(User.objects.get(username="check-lead").pk)
    with pytest.raises(CommandError):
        call_command("dev_session", "nobody")


def test_seed_replaces_an_older_check_table(seeder):
    """以前の確認で手で作った、列の違う同じ名前の表を作り直す"""
    with connection.cursor() as cursor:
        cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {seed.SCHEMA}")
        cursor.execute(f"CREATE TABLE {seed.SCHEMA}.vendors (code text, name text)")
    seeder.run()
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT invoice_number FROM {seed.SCHEMA}.vendors WHERE code = 'V001'")
        assert cursor.fetchone() == ("T1000000000001",)
