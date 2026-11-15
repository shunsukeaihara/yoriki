"""Pythonのpackageの追加（design.md §7.6・§7.7）のうち、Dockerを使わない部分：カタログ、取得したものの確かめ、
環境の版、カタログの管理API"""

import hashlib
from decimal import Decimal

import pytest

from run.factories import RunFactory
from run.models import RunTrigger, Subagent
from runtime.launcher import SandboxSpec
from runtime.models import EnvironmentRevision, PythonPackage
from runtime.packages import (
    MAX_REQUESTED,
    PackageError,
    Requested,
    _verify,
    allowed,
    catalog_problems,
    current_revision,
    merge_requested,
    parse_requests,
    record_revision,
    requirements,
)

from .wheels import build_wheel, stored_wheel

PACKAGES = "/api/v1/python_packages/"
SPEC = SandboxSpec(
    image="yoriki/yoriki-sandbox-docs@sha256:" + "a" * 64,
    platform="linux/amd64",
    memory_mb=2048,
    cpus=Decimal(2),
    pids_limit=256,
    tmp_mb=256,
    work_mb=512,
    output_mb=256,
    env_mb=512,
)


class TestRequests:
    def test_names_are_normalized_and_versions_must_be_exact(self):
        assert parse_requests([{"name": "Rapid_Fuzz"}, {"name": "tabulate", "version": "0.9.0"}]) == [
            Requested("rapid-fuzz", ""),
            Requested("tabulate", "0.9.0"),
        ]
        with pytest.raises(PackageError, match="exact version"):
            parse_requests([{"name": "tabulate", "version": ">=0.9"}])
        with pytest.raises(PackageError, match="not a valid package name"):
            parse_requests([{"name": "../evil"}])

    def test_duplicates_and_too_many_are_rejected(self):
        with pytest.raises(PackageError, match="more than once"):
            parse_requests([{"name": "tabulate"}, {"name": "Tabulate"}])
        with pytest.raises(PackageError, match="at most"):
            parse_requests([{"name": f"p{i}"} for i in range(MAX_REQUESTED + 1)])


@pytest.mark.django_db
class TestCatalog:
    def test_only_active_entries_within_the_range(self):
        PythonPackage.objects.create(name="tabulate", specifier=">=0.9,<1")
        PythonPackage.objects.create(name="humanize", is_active=False)
        PythonPackage.objects.create(name="arrow", deleted=True)
        problems = catalog_problems(
            [
                Requested("tabulate", ""),
                Requested("tabulate", "0.8.10"),
                Requested("humanize", ""),
                Requested("arrow", ""),
                Requested("unknown", ""),
            ]
        )
        assert problems == [
            "tabulate 0.8.10 is outside the allowed versions (>=0.9,<1)",
            "humanize is not in the package catalog",
            "arrow is not in the package catalog",
            "unknown is not in the package catalog",
        ]
        assert allowed("tabulate", "0.9.0")
        assert not allowed("tabulate", "1.0")
        assert not allowed("humanize", "4.0")

    def test_requirements_use_the_catalog_range_without_a_version(self):
        PythonPackage.objects.create(name="tabulate", specifier=">=0.9")
        PythonPackage.objects.create(name="humanize")
        assert requirements([Requested("tabulate", ""), Requested("humanize", ""), Requested("tabulate", "0.9.0")]) == [
            "tabulate>=0.9",
            "humanize",
            "tabulate==0.9.0",
        ]


class TestVerify:
    TAGS = ["cp313-cp313-manylinux_2_17_x86_64", "py3-none-any"]

    def write(self, tmp_path, name, version):
        filename, data = build_wheel(name, version)
        (tmp_path / filename).write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def test_accepts_wheels_that_match(self, tmp_path):
        sha = self.write(tmp_path, "tabulate", "0.9.0")
        result = {
            "tags": self.TAGS,
            "items": [{"name": "tabulate", "version": "0.9.0", "sha256": sha, "requested": True}],
        }
        [wheel] = _verify(result, tmp_path)
        assert (wheel.name, wheel.version, wheel.requested, wheel.sha256) == ("tabulate", "0.9.0", True, sha)

    def test_rejects_unsupported_tags(self, tmp_path):
        sha = self.write(tmp_path, "tabulate", "0.9.0")
        result = {
            "tags": ["cp313-cp313-manylinux_2_17_aarch64"],
            "items": [{"name": "tabulate", "version": "0.9.0", "sha256": sha}],
        }
        with pytest.raises(PackageError, match="does not support"):
            _verify(result, tmp_path)

    def test_rejects_a_different_hash_and_missing_wheels(self, tmp_path):
        self.write(tmp_path, "tabulate", "0.9.0")
        with pytest.raises(PackageError, match="does not match"):
            _verify(
                {"tags": self.TAGS, "items": [{"name": "tabulate", "version": "0.9.0", "sha256": "0" * 64}]}, tmp_path
            )
        with pytest.raises(PackageError, match="was not fetched"):
            _verify(
                {"tags": self.TAGS, "items": [{"name": "humanize", "version": "4.0", "sha256": "0" * 64}]}, tmp_path
            )

    def test_rejects_too_many_wheels(self, tmp_path, monkeypatch):
        monkeypatch.setattr("runtime.packages.MAX_WHEELS", 1)
        items = [{"name": f"p{i}", "version": "1.0", "sha256": ""} for i in range(2)]
        with pytest.raises(PackageError, match="at most 1"):
            _verify({"tags": self.TAGS, "items": items}, tmp_path)


@pytest.mark.django_db
class TestRevisions:
    def record(self, run, subagent=None, *, parent=None, added=(), base=(), requested=()):
        return record_revision(
            run,
            subagent,
            parent=parent,
            spec=SPEC,
            python_version="3.13.16",
            requested=list(requested),
            added=list(added),
            base=list(base),
            bundle_requested=[],
        )

    def test_newer_version_replaces_the_older_one(self):
        run = RunFactory.create()
        old, other = stored_wheel("tabulate", "0.8.10"), stored_wheel("humanize", "4.0")
        first = self.record(run, added=[old, other])
        new = stored_wheel("tabulate", "0.9.0")
        second = self.record(run, parent=first, added=[new], base=first.wheels.all())
        assert set(second.wheels.all()) == {new, other}
        assert list(second.bundle.wheels.all()) == [new]

    def test_conversation_runs_share_and_task_runs_and_subagents_are_separate(self):
        run = RunFactory.create()
        later = RunFactory.create(conversation=run.conversation)
        conversation_revision = self.record(run)
        assert current_revision(later) == conversation_revision
        # タスクのRunは会話の環境を引き継がない
        task_run = RunFactory.create(conversation=run.conversation, trigger=RunTrigger.TASK)
        assert current_revision(task_run) is None
        # 子は起動したときの会話の版で始め、子が足したものは子だけ
        subagent = Subagent.objects.create(
            run=later, tool_call_id="c1", description="d", prompt="p", model_version=later.model_version
        )
        assert current_revision(later, subagent) == conversation_revision
        child_revision = self.record(later, subagent, parent=conversation_revision)
        assert current_revision(later, subagent) == child_revision
        assert current_revision(later) == conversation_revision
        # 子を起動した後に会話へ足したものは、子には入れない
        newer = self.record(later, parent=conversation_revision)
        assert current_revision(later) == newer
        other_child = Subagent.objects.create(
            run=later, tool_call_id="c2", description="d", prompt="p", model_version=later.model_version
        )
        EnvironmentRevision.objects.filter(pk=newer.pk).update(created=other_child.created.replace(year=2999))
        assert current_revision(later, other_child) == conversation_revision

    def test_merge_requested_keeps_earlier_requests(self, tmp_path):
        from runtime.packages import LocalWheel  # noqa: PLC0415

        def local(name, version, requested):
            return LocalWheel(tmp_path, f"{name}.whl", name, version, "", 0, requested)

        merged = merge_requested(
            [{"name": "tabulate", "version": "0.8.10"}, {"name": "humanize", "version": "4.0"}],
            [local("tabulate", "0.9.0", True), local("wcwidth", "0.2", False)],
        )
        assert merged == [{"name": "humanize", "version": "4.0"}, {"name": "tabulate", "version": "0.9.0"}]
        # 取得せずにすでに入っていたもの（イメージの版）も記録する
        merged = merge_requested([], [local("tabulate", "0.9.0", True)], {"six": "1.17.0"})
        assert merged == [{"name": "six", "version": "1.17.0"}, {"name": "tabulate", "version": "0.9.0"}]


@pytest.mark.django_db
class TestCatalogAPI:
    @pytest.fixture(name="admin_client")
    def fixture_admin_client(self, api_client, platform_admin):
        api_client.force_login(platform_admin)
        return api_client

    def test_only_platform_admin(self, api_client, app_user):
        assert api_client.get(PACKAGES).status_code == 401
        api_client.force_login(app_user)
        assert api_client.get(PACKAGES).status_code == 403
        assert api_client.post(PACKAGES, {"name": "tabulate"}, format="json").status_code == 403

    def test_create_normalizes_and_rejects_duplicates(self, admin_client):
        res = admin_client.post(PACKAGES, {"name": "Rapid_Fuzz", "specifier": " >=3, <4 "}, format="json")
        assert res.status_code == 201, res.data
        assert (res.data["name"], res.data["specifier"], res.data["is_active"]) == ("rapid-fuzz", ">=3,<4", True)
        res = admin_client.post(PACKAGES, {"name": "rapid.fuzz"}, format="json")
        assert res.status_code == 400
        assert "name" in res.data

    def test_invalid_name_and_specifier(self, admin_client):
        res = admin_client.post(PACKAGES, {"name": "a b", "specifier": "latest"}, format="json")
        assert res.status_code == 400
        assert set(res.data) >= {"name", "specifier"}

    def test_update_stop_and_delete(self, admin_client):
        uid = admin_client.post(PACKAGES, {"name": "tabulate"}, format="json").data["id"]
        res = admin_client.patch(f"{PACKAGES}{uid}/", {"is_active": False, "note": "表の整形"}, format="json")
        assert res.status_code == 200, res.data
        assert not allowed("tabulate", "0.9.0")
        assert admin_client.delete(f"{PACKAGES}{uid}/").status_code == 204
        assert PythonPackage.objects.get(uid=uid).deleted
        assert admin_client.get(PACKAGES).data["results"] == []
        # 消した後は同じ名前で登録し直せる
        assert admin_client.post(PACKAGES, {"name": "tabulate"}, format="json").status_code == 201
