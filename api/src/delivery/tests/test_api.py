"""出口の管理（連携の管理者。authorization.md §5.5）と、タスクの結果の通知の設定・候補・配信の履歴（authorization.md
§5.4・§7）"""

import socket
from unittest import mock

import pytest

from delivery.factories import DestinationFactory
from delivery.models import (
    Delivery,
    DeliveryStatus,
    Destination,
    DestinationKind,
    DestinationRevision,
    DestinationScope,
    NotifyEvent,
)
from llm.factories import LLMConnectionFactory, ModelProfileFactory
from llm.models import ProviderKind
from run.models import RunLaunch, RunStatus
from task.dispatch import start_task_run
from task.errors import CannotRun
from task.factories import TaskDefinitionFactory
from task.models import REVISION_FIELDS, TaskDefinition, TaskScope, TriggerBinding
from user.factories import DepartmentFactory, DepartmentMembershipFactory, UserFactory, grant
from user.models import User
from utils.permissions import AppPermission

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("default_runtime")]

DESTINATIONS = "/api/v1/destinations/"
CHOICES = "/api/app/v1/destinations/"
TASKS = "/api/app/v1/tasks/"
DESTINATION_PERMS = [f"delivery.{kind}_destination" for kind in ("view", "add", "change", "delete")]
HOOK_URL = "https://hooks.zapier.com/hooks/catch/123/abcdef/"
PUBLIC_ADDRESS = "93.184.216.34"


def _resolving_to(address: str):
    """名前を引いた先を差し替える"""
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    info = [(family, socket.SOCK_STREAM, 6, "", (address, 443))]
    return mock.patch("delivery.network.socket.getaddrinfo", return_value=info)


@pytest.fixture(autouse=True)
def fixture_public_dns():
    with _resolving_to(PUBLIC_ADDRESS):
        yield


@pytest.fixture(name="integrator")
def fixture_integrator(db) -> User:
    """連携の管理者（出口のModelのpermissionだけ。タスクを管理する権限は持たない）"""
    return grant(UserFactory.create(), *DESTINATION_PERMS)


@pytest.fixture(name="company_manager")
def fixture_company_manager(db) -> User:
    return grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)


def _create(api_client, user, **data):
    api_client.force_login(user)
    body = {"kind": DestinationKind.ZAPIER, "scope": DestinationScope.COMPANY, "name": "経理のZap", "url": HOOK_URL}
    return api_client.post(DESTINATIONS, {**body, **data}, format="json")


# --- 出口の管理 ---


def test_creating_a_destination_needs_the_permission_and_shows_the_secret_once(api_client, app_user, integrator):
    assert _create(api_client, app_user).status_code == 403

    res = _create(api_client, integrator)
    assert res.status_code == 201, res.json()
    data = res.json()
    assert data["signing_secret"].startswith("whsec_")
    assert data["version"] == 1
    # 送り先のURLは返さず、ホストと末尾だけを出す
    assert "url" not in data
    assert data["url_display"] == "https://hooks.zapier.com/…def/"
    assert "abcdef" not in str(api_client.get(f"{DESTINATIONS}{data['id']}/").json())
    assert "signing_secret" not in api_client.get(f"{DESTINATIONS}{data['id']}/").json()
    destination = Destination.objects.get(uid=data["id"])
    assert destination.current_revision is not None
    assert destination.current_revision.url.reveal() == HOOK_URL
    assert destination.signing_secret.reveal() == data["signing_secret"]


@pytest.mark.parametrize(
    ("kind", "url", "message"),
    [
        (DestinationKind.WEBHOOK, "http://hooks.example.com/a", "https://"),
        (DestinationKind.WEBHOOK, "https://user:pass@hooks.example.com/a", "パスワード"),
        (DestinationKind.ZAPIER, "https://hooks.example.com/hooks/catch/1/", "Catch Hook"),
        (DestinationKind.ZAPIER, "https://hooks.zapier.com/other/", "Catch Hook"),
        (DestinationKind.WEBHOOK, "https://[::1/hook", "形"),
    ],
)
def test_the_url_must_fit_the_kind(api_client, integrator, kind, url, message):
    res = _create(api_client, integrator, kind=kind, url=url)
    assert res.status_code == 400
    assert message in str(res.json()["url"])


@pytest.mark.parametrize("address", ["10.0.0.5", "127.0.0.1", "169.254.169.254", "::1", "fd00::1", "100.64.0.1"])
def test_internal_addresses_are_refused_unless_allowed(api_client, integrator, settings, address):
    url = "https://intranet.example.co.jp/hook"
    with _resolving_to(address):
        res = _create(api_client, integrator, kind=DestinationKind.WEBHOOK, url=url)
        assert res.status_code == 400
        assert "社内のアドレス" in str(res.json()["url"])

        # 基盤管理者が許したネットワークなら送れる
        settings.YORIKI_DELIVERY_ALLOWED_NETWORKS = [
            "10.0.0.0/8",
            "127.0.0.0/8",
            "169.254.0.0/16",
            "::1/128",
            "fd00::/8",
            "100.64.0.0/10",
        ]
        assert _create(api_client, integrator, kind=DestinationKind.WEBHOOK, url=url).status_code == 201


def test_a_name_that_cannot_be_resolved_is_refused(api_client, integrator):
    with mock.patch("delivery.network.socket.getaddrinfo", side_effect=socket.gaierror("no such host")):
        res = _create(api_client, integrator)
    assert res.status_code == 400
    assert "解決できません" in str(res.json()["url"])


def test_a_department_destination_needs_a_department(api_client, integrator):
    res = _create(api_client, integrator, scope=DestinationScope.DEPARTMENT)
    assert res.status_code == 400
    assert "department" in res.json()
    department = DepartmentFactory.create()
    res = _create(api_client, integrator, scope=DestinationScope.DEPARTMENT, department=department.uid)
    assert res.status_code == 201
    assert res.json()["department_info"]["id"] == department.uid


def test_updating_makes_a_revision_and_conflicts_with_other_changes(api_client, integrator):
    data = _create(api_client, integrator).json()
    url = f"{DESTINATIONS}{data['id']}/"
    # 中身が同じなら版を作らない（URLは暗号文でなく平文で比べる）
    res = api_client.patch(url, {"version": 1, "url": HOOK_URL, "name": "経理"}, format="json")
    assert res.status_code == 200, res.json()
    assert (res.json()["version"], res.json()["name"]) == (1, "経理")

    res = api_client.patch(
        url, {"version": 1, "allows_sensitive_results": True, "change_reason": "人事"}, format="json"
    )
    assert res.json()["version"] == 2
    revision = DestinationRevision.objects.get(destination__uid=data["id"], number=2)
    # URLを送らなければ前の版のまま
    assert revision.url.reveal() == HOOK_URL
    assert revision.allows_sensitive_results

    assert api_client.patch(url, {"version": 1, "name": "x"}, format="json").status_code == 409
    assert api_client.patch(url, {"name": "x"}, format="json").status_code == 400  # 版を送らない
    # 種類・範囲は変えられない
    res = api_client.patch(url, {"version": 2, "kind": DestinationKind.WEBHOOK, "scope": 4}, format="json")
    assert (res.json()["kind"], res.json()["scope"]) == (DestinationKind.ZAPIER, DestinationScope.COMPANY)
    assert [r["number"] for r in api_client.get(f"{url}revisions/").json()["results"]] == [2, 1]


def test_suspend_resume_rotate_and_delete(api_client, integrator):
    data = _create(api_client, integrator).json()
    url = f"{DESTINATIONS}{data['id']}/"
    assert api_client.post(f"{url}suspend/").json()["suspended"] is True
    assert api_client.post(f"{url}resume/").json()["suspended"] is False
    rotated = api_client.post(f"{url}rotate_secret/").json()
    assert rotated["signing_secret"] != data["signing_secret"]
    assert Destination.objects.get(uid=data["id"]).signing_secret.reveal() == rotated["signing_secret"]
    assert api_client.delete(url).status_code == 204
    assert Destination.objects.get(uid=data["id"]).deleted
    assert api_client.get(url).status_code == 404


# --- タスクの結果の通知の設定 ---


def _create_task(api_client, user, **data):
    api_client.force_login(user)
    body = {
        "scope": TaskScope.COMPANY,
        "name": "請求書チェック",
        "prompt_template": "請求書を確認してください。",
        "model_profile": ModelProfileFactory.create(version__supports_tools=True).uid,
        **data,
    }
    return api_client.post(TASKS, body, format="json")


def test_a_task_uses_only_destinations_of_its_scope(api_client, company_manager):
    company = DestinationFactory.create(name="全社のZap")
    system = DestinationFactory.create(scope=DestinationScope.SYSTEM)
    department = DestinationFactory.create(scope=DestinationScope.DEPARTMENT, department=DepartmentFactory.create())

    for other in (system, department):
        res = _create_task(api_client, company_manager, destination=other.uid, notify_events=["run.failed"])
        assert res.status_code == 400
        assert "範囲" in str(res.json()["destination"])

    res = _create_task(
        api_client, company_manager, destination=company.uid, notify_events=["run.completed", "run.failed"]
    )
    assert res.status_code == 201, res.json()
    data = res.json()
    assert (data["destination"], data["notify_events"], data["notify_content"]) == (
        company.uid,
        ["run.completed", "run.failed"],
        "status_and_result",
    )
    assert data["destination_info"] == {
        "id": company.uid,
        "name": "全社のZap",
        "kind": DestinationKind.WEBHOOK,
        "scope": DestinationScope.COMPANY,
        "department": None,
        "allows_sensitive_results": False,
    }


def test_events_and_destination_go_together(api_client, company_manager):
    res = _create_task(api_client, company_manager, notify_events=["run.failed"])
    assert "destination" in res.json()
    res = _create_task(api_client, company_manager, destination=DestinationFactory.create().uid)
    assert "notify_events" in res.json()
    suspended = DestinationFactory.create(suspended=True)
    res = _create_task(api_client, company_manager, destination=suspended.uid, notify_events=["run.failed"])
    assert "停止中" in str(res.json()["destination"])


def test_a_department_task_uses_the_destination_of_its_department(api_client, app_user):
    mine, other = DepartmentFactory.create(), DepartmentFactory.create()
    DepartmentMembershipFactory.create(department=mine, user=app_user)
    user = grant(app_user, AppPermission.MANAGE_DEPARTMENT_TASKS)
    ours = DestinationFactory.create(scope=DestinationScope.DEPARTMENT, department=mine)
    theirs = DestinationFactory.create(scope=DestinationScope.DEPARTMENT, department=other)
    DestinationFactory.create()  # 全社の出口は部門のタスクでは使えない（部門のデータを全社向けに流さない）

    common = {"scope": TaskScope.DEPARTMENT, "department": mine.uid, "notify_events": ["run.failed"]}
    assert _create_task(api_client, user, destination=theirs.uid, **common).status_code == 400
    assert _create_task(api_client, user, destination=ours.uid, **common).status_code == 201

    # 候補は、タスクを管理できる範囲の出口だけ（部門は管理できる部門のもの）
    assert [d["id"] for d in api_client.get(CHOICES).json()["results"]] == [ours.uid]


def test_a_sensitive_task_needs_a_destination_that_allows_sensitive_results(api_client, company_manager):
    local = ModelProfileFactory.create(
        version__connection=LLMConnectionFactory.create(kind=ProviderKind.LOCAL), version__supports_tools=True
    )
    plain = DestinationFactory.create()
    allowed = DestinationFactory.create(revision__allows_sensitive_results=True)
    common = {"handles_sensitive_data": True, "model_profile": local.uid, "notify_events": ["run.completed"]}
    res = _create_task(api_client, company_manager, destination=plain.uid, **common)
    assert "機密" in str(res.json()["destination"])
    assert _create_task(api_client, company_manager, destination=allowed.uid, **common).status_code == 201


def test_choices_show_only_what_task_managers_may_pick(api_client, app_user, company_manager):
    company = DestinationFactory.create(name="全社")
    DestinationFactory.create(suspended=True)
    DestinationFactory.create(scope=DestinationScope.SYSTEM)
    api_client.force_login(app_user)
    assert api_client.get(CHOICES).json()["results"] == []
    api_client.force_login(company_manager)
    results = api_client.get(CHOICES).json()["results"]
    assert [d["id"] for d in results] == [company.uid]
    # 送り先・鍵は返さない
    assert set(results[0]) == {"id", "name", "kind", "scope", "department", "allows_sensitive_results"}


# --- Webhookで起動するタスクは、失敗の通知先が必須（design.md §4.2） ---


def test_a_webhook_needs_a_failure_notice_and_the_task_cannot_drop_it(api_client, company_manager):
    integrator = grant(UserFactory.create(), "task.view_triggerbinding", "task.add_triggerbinding")
    task = TaskDefinitionFactory.create(scope=TaskScope.COMPANY)
    api_client.force_login(integrator)
    res = api_client.post("/api/v1/trigger_bindings/", {"task": task.uid, "name": "経費SaaS"}, format="json")
    assert res.status_code == 400
    assert "run.failed" in str(res.json()["task"])

    destination = DestinationFactory.create()
    task.revise(
        actor=task.created_by,
        expected_version=1,
        **{**_content(task), "destination": destination, "notify_events": [NotifyEvent.COMPLETED]},
    )
    res = api_client.post("/api/v1/trigger_bindings/", {"task": task.uid, "name": "経費SaaS"}, format="json")
    assert res.status_code == 400  # 完了だけでは足りない

    task.revise(actor=task.created_by, expected_version=2, **{**_content(task), "notify_events": [NotifyEvent.FAILED]})
    res = api_client.post("/api/v1/trigger_bindings/", {"task": task.uid, "name": "経費SaaS"}, format="json")
    assert res.status_code == 201, res.json()

    # 入口のあるタスクは、失敗の通知を外せない
    api_client.force_login(company_manager)
    res = api_client.patch(f"{TASKS}{task.uid}/", {"version": 3, "notify_events": ["run.completed"]}, format="json")
    assert res.status_code == 400
    assert "notify_events" in res.json()
    res = api_client.patch(
        f"{TASKS}{task.uid}/", {"version": 3, "destination": None, "notify_events": []}, format="json"
    )
    assert res.status_code == 400
    assert TaskDefinition.objects.get(pk=task.pk).version_number == 3
    assert TriggerBinding.objects.filter(task=task).exists()


def _content(task: TaskDefinition) -> dict:
    task.refresh_from_db()
    return {name: getattr(task.current_revision, name) for name in REVISION_FIELDS}


# --- Runの受付と配信の履歴 ---


def _notified_task(destination=None, events=("run.completed", "run.failed")) -> TaskDefinition:
    return TaskDefinitionFactory.create(
        scope=TaskScope.COMPANY,
        revision__destination=destination or DestinationFactory.create(),
        revision__notify_events=list(events),
    )


def test_a_run_pins_the_destination_revision_and_refuses_an_unusable_one(company_manager):
    task = _notified_task()
    assert task.current_revision is not None
    destination = task.current_revision.destination
    assert destination is not None
    run = start_task_run(task, inputs={}, actor=company_manager, launch=RunLaunch.MANUAL)
    assert run.destination_revision == destination.current_revision

    # 出口の版が変わっても、受け付けたRunは固定した版のまま
    destination.revise(
        actor=company_manager, expected_version=1, url="https://hooks.example.com/new", allows_sensitive_results=False
    )
    run.refresh_from_db()
    assert run.destination_revision is not None
    assert run.destination_revision.number == 1

    Destination.objects.filter(pk=destination.pk).update(suspended=True)
    with pytest.raises(CannotRun, match="通知先"):
        start_task_run(task, inputs={}, actor=company_manager, launch=RunLaunch.MANUAL)


def _finish(run, status=RunStatus.COMPLETED, **kwargs):
    run.set_status(RunStatus.RUNNING)
    if status == RunStatus.COMPLETED:
        run.result = {"ok": True}
        run.save(update_fields=["result"])
    run.set_status(status, **kwargs)


def test_deliveries_are_listed_and_retried_by_task_managers(api_client, app_user, company_manager):
    task = _notified_task()
    run = start_task_run(task, inputs={}, actor=company_manager, launch=RunLaunch.MANUAL)
    _finish(run)
    delivery = Delivery.objects.get(run=run)
    url = f"{TASKS}{task.uid}/deliveries/"

    api_client.force_login(app_user)
    assert api_client.get(url).status_code == 404  # タスクを管理できない人

    api_client.force_login(company_manager)
    data = api_client.get(url, {"run": run.uid}).json()["results"]
    assert [d["id"] for d in data] == [delivery.uid]
    assert data[0]["payload"]["result"] == {"ok": True}
    assert "url" not in str(data[0]["destination"])

    # 配信待ちは送り直せない
    assert api_client.post(f"{url}{delivery.uid}/retry/").status_code == 400
    Delivery.objects.filter(pk=delivery.pk).update(status=DeliveryStatus.UNKNOWN, attempt_count=1)
    res = api_client.post(f"{url}{delivery.uid}/retry/")
    assert res.status_code == 200, res.json()
    assert res.json()["status"] == DeliveryStatus.PENDING
    delivery.refresh_from_db()
    assert (delivery.status, delivery.retry_from) == (DeliveryStatus.PENDING, 1)
