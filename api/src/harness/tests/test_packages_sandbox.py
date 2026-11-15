"""`install_python_packages` と、追加したpackageの引き継ぎ（design.md §7.6・§7.7）。Sandboxは実際のDockerで
動かす（イメージがなければ飛ばす）。取得用のコンテナでの取得は、PyPIへつなげるときだけ1つのテストで確かめ、ほかは
取得を差し替える"""

import hashlib
import socket
from pathlib import Path

import pytest
from django.conf import settings

from conversation.factories import ConversationFactory
from run.models import ExecutionAttempt, RunStatus
from runtime import packages as runtime_packages
from runtime.launcher import Launcher
from runtime.models import EnvironmentRevision, PythonPackage
from runtime.packages import FetchResult
from runtime.tests.wheels import build_wheel, stored_wheel

from .fakes import FakeAdapter, calls, reply
from .test_sandbox import execute, post, results, shell, text

pytestmark = [pytest.mark.usefixtures("default_runtime", "sandbox_image"), pytest.mark.django_db, pytest.mark.sandbox]

IMPORT = 'python -c "import yoriki_pkg; print(yoriki_pkg.VALUE)"'


@pytest.fixture(name="conversation")
def fixture_conversation(app_user):
    return ConversationFactory.create(owner=app_user)


@pytest.fixture(name="fake_fetch")
def fixture_fake_fetch(monkeypatch):
    """取得用のコンテナの代わりに、作ったwheelを返す。呼ばれた指定を記録する"""
    fetched: list[dict] = []

    def fake(launcher, sandbox_spec, labels, *, requirement_lines, base, workdir: Path, name):
        fetched.append({"lines": requirement_lines, "base": list(base), "image": sandbox_spec.image})
        workdir.mkdir(parents=True, exist_ok=True)
        wheels = []
        for line in requirement_lines:
            package = line.split("==")[0].split(">")[0].split("<")[0]
            version = line.split("==")[1] if "==" in line else "1.0"
            filename, data = build_wheel(package, version, value=f"{package} {version}")
            (workdir / filename).write_bytes(data)
            item = {"name": package, "version": version, "requested": True, "sha256": hashlib.sha256(data).hexdigest()}
            wheels += runtime_packages._verify({"tags": ["py3-none-any"], "items": [item]}, workdir)
        return FetchResult(wheels, "3.13.16")

    monkeypatch.setattr("runtime.packages.fetch", fake)
    return fetched


def install(*names: str, call_id: str = "p1") -> list:
    return calls((call_id, "install_python_packages", {"packages": [{"name": n} for n in names]}))


def test_installed_package_is_used_and_kept_for_the_next_run(conversation, fake_fetch):
    PythonPackage.objects.create(name="yoriki-pkg")
    run = execute(
        post(conversation),
        FakeAdapter([install("yoriki-pkg"), calls(("c1", "shell", shell(IMPORT))), reply("できました。")]),
    )
    assert run.status == RunStatus.COMPLETED
    r = results(run)
    assert "Installed: yoriki-pkg 1.0" in text(r["p1"]), text(r["p1"])
    assert "yoriki-pkg 1.0" in text(r["c1"])
    revision = EnvironmentRevision.objects.get(conversation=conversation)
    assert revision.requested == [{"name": "yoriki-pkg", "version": "1.0"}]
    assert ExecutionAttempt.objects.get(run=run).environment == revision

    # 同じ会話の次のRunでは、取得せずに保存したwheelから入れ直す
    run = execute(post(conversation, "続けて"), FakeAdapter([calls(("c2", "shell", shell(IMPORT))), reply("OK")]))
    assert "yoriki-pkg 1.0" in text(results(run)["c2"])
    assert len(fake_fetch) == 1
    assert ExecutionAttempt.objects.get(run=run).environment == revision


def test_package_outside_the_catalog_is_not_fetched(conversation, fake_fetch):
    PythonPackage.objects.create(name="yoriki-pkg", is_active=False)
    run = execute(post(conversation), FakeAdapter([install("yoriki-pkg", "other"), reply("できませんでした。")]))
    result = results(run)["p1"]
    assert result["is_error"]
    assert "yoriki-pkg is not in the package catalog; other is not in the package catalog" in text(result)
    assert "administrator" in text(result)
    assert fake_fetch == []
    assert not EnvironmentRevision.objects.exists()


def test_stopped_package_is_not_reinstalled_and_the_model_is_told(conversation, fake_fetch):
    entry = PythonPackage.objects.create(name="yoriki-pkg")
    execute(post(conversation), FakeAdapter([install("yoriki-pkg"), reply("できました。")]))
    entry.is_active = False
    entry.save()
    run = execute(post(conversation, "続けて"), FakeAdapter([calls(("c1", "shell", shell(IMPORT))), reply("OK")]))
    output = text(results(run)["c1"])
    assert "No module named 'yoriki_pkg'" in output
    assert "could not be installed in this sandbox" in output
    assert "yoriki-pkg" in output
    assert ExecutionAttempt.objects.get(run=run).environment is None


def test_different_image_fetches_the_same_versions_again(conversation, fake_fetch):
    PythonPackage.objects.create(name="yoriki-pkg")
    old = EnvironmentRevision.objects.create(
        conversation=conversation,
        image="yoriki/yoriki-sandbox-docs@sha256:" + "0" * 64,
        platform=Launcher().platform(),
        python_version="3.13.1",
        requested=[{"name": "yoriki-pkg", "version": "0.5"}],
    )
    old.wheels.set([stored_wheel("yoriki-pkg", "0.5", value="old")])
    run = execute(post(conversation), FakeAdapter([calls(("c1", "shell", shell(IMPORT))), reply("OK")]))
    assert "yoriki-pkg 0.5" in text(results(run)["c1"])
    assert fake_fetch == [{"lines": ["yoriki-pkg==0.5"], "base": [], "image": settings.YORIKI_SANDBOX_IMAGE_OVERRIDE}]
    rebuilt = EnvironmentRevision.objects.filter(conversation=conversation).latest("pk")
    assert rebuilt.parent == old
    assert rebuilt.image == settings.YORIKI_SANDBOX_IMAGE_OVERRIDE


def test_newer_version_than_the_earlier_addition_replaces_it(conversation, fake_fetch):
    PythonPackage.objects.create(name="yoriki-pkg")
    PythonPackage.objects.create(name="other-pkg")
    run = execute(
        post(conversation),
        FakeAdapter(
            [
                install("yoriki-pkg", "other-pkg"),
                calls(("p2", "install_python_packages", {"packages": [{"name": "yoriki-pkg", "version": "2.0"}]})),
                calls(("c1", "shell", shell(IMPORT + ' && python -c "import other_pkg"'))),
                reply("OK"),
            ]
        ),
    )
    assert "yoriki-pkg 2.0" in text(results(run)["c1"])
    # 頼んだpackageはそれまでの版に縛らず、ほかは今の版のままにする
    assert fake_fetch[1]["base"] == [("other-pkg", "1.0")]
    latest = EnvironmentRevision.objects.filter(conversation=conversation).latest("pk")
    assert sorted((w.name, w.version) for w in latest.wheels.all()) == [("other-pkg", "1.0"), ("yoriki-pkg", "2.0")]


def _pypi_reachable() -> bool:
    try:
        socket.create_connection(("pypi.org", 443), timeout=3).close()
    except OSError:
        return False
    return True


@pytest.mark.skipif(not _pypi_reachable(), reason="PyPI is not reachable")
def test_fetch_from_the_index(conversation):
    """取得用のコンテナでPyPIから取得し、hashを確かめて入れる"""
    PythonPackage.objects.create(name="tabulate", specifier="==0.9.0")
    PythonPackage.objects.create(name="six")
    run = execute(
        post(conversation),
        FakeAdapter(
            [
                install("tabulate", "six"),
                calls(("c1", "shell", shell('python -c "import tabulate; print(tabulate.__version__)"'))),
                reply("OK"),
            ]
        ),
    )
    r = results(run)
    assert "Installed: tabulate 0.9.0" in text(r["p1"]), text(r["p1"])
    # six はイメージに入っているので取得しないが、イメージが変わったときに取り直せるよう版を記録する
    assert "Already installed: six" in text(r["p1"])
    assert "0.9.0" in text(r["c1"])
    revision = EnvironmentRevision.objects.get(conversation=conversation)
    [wheel] = revision.wheels.all()
    assert wheel.filename.startswith("tabulate-0.9.0-") and wheel.filename.endswith(".whl")
    assert [r["name"] for r in revision.requested] == ["six", "tabulate"]
