"""画面の通知（in_app。design.md §4.3・§4.4、authorization.md §5.5・§7.1）。受け手は送るときに決め、今もタスクを
管理できる人に限る。個人のタスクには全員で共通の出口（受け手は持ち主）を使い、機密のタスクにも使える"""

from unittest import mock

import pytest
from rest_framework.test import APIClient

from delivery.factories import DestinationFactory
from delivery.models import (
    Delivery,
    DeliveryStatus,
    Destination,
    DestinationKind,
    DestinationScope,
    Notification,
)
from delivery.tasks import send_delivery
from llm.factories import LLMConnectionFactory, ModelProfileFactory
from llm.models import ProviderKind
from run.models import RunLaunch, RunStatus
from task.dispatch import start_task_run
from task.factories import TaskDefinitionFactory
from task.models import TaskScope
from user.factories import DepartmentFactory, DepartmentMembershipFactory, UserFactory, grant
from user.models import system_user
from utils.permissions import AppPermission

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("default_runtime")]

NOTIFICATIONS = "/api/app/v1/notifications/"


def _in_app(**kwargs) -> Destination:
    return DestinationFactory.create(kind=DestinationKind.IN_APP, revision__url="", **kwargs)


def _finish(task, actor, *, launch=RunLaunch.MANUAL) -> Delivery:
    run = start_task_run(task, inputs={}, actor=actor, launch=launch)
    run.set_status(RunStatus.RUNNING)
    run.set_status(RunStatus.COMPLETED)
    delivery = Delivery.objects.get(run=run)
    send_delivery(delivery.pk)
    delivery.refresh_from_db()
    return delivery


def _task(scope=TaskScope.COMPANY, destination=None, **kwargs):
    return TaskDefinitionFactory.create(
        scope=scope,
        revision__destination=destination or _in_app(),
        revision__notify_events=["run.completed"],
        **kwargs,
    )


def _recipients(delivery: Delivery) -> set:
    return set(Notification.objects.filter(delivery=delivery).values_list("user_id", flat=True))


def test_a_personal_task_notifies_its_owner_even_when_sensitive():
    owner = UserFactory.create()
    local = ModelProfileFactory.create(version__connection=LLMConnectionFactory.create(kind=ProviderKind.LOCAL))
    task = _task(
        TaskScope.PERSONAL,
        Destination.personal_in_app(),
        owner=owner,
        revision__handles_sensitive_data=True,
        revision__model_profile=local,
    )
    delivery = _finish(task, owner)
    assert delivery.status == DeliveryStatus.DELIVERED
    notification = Notification.objects.get(delivery=delivery)
    assert (notification.user, notification.task_name, notification.event_type) == (owner, task.name, "run.completed")
    # 共通の出口は1つだけ
    assert Destination.personal_in_app() == Destination.personal_in_app()


def test_a_manual_run_notifies_the_person_who_ran_it():
    runner = grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)
    other = grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)
    with mock.patch("delivery.in_app.notify_notification") as notify:
        delivery = _finish(_task(), runner)
    assert _recipients(delivery) == {runner.pk}
    # 受け手の画面へ、通知が届いたことを知らせる
    notify.assert_called_once_with(runner.uid)
    assert other.pk not in _recipients(delivery)


def test_a_webhook_run_notifies_everyone_who_can_manage_the_task():
    managers = [grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS) for _ in range(2)]
    inactive = grant(UserFactory.create(is_active=False), AppPermission.MANAGE_COMPANY_TASKS)
    UserFactory.create()  # 管理できない人
    delivery = _finish(_task(), system_user(), launch=RunLaunch.WEBHOOK)
    assert _recipients(delivery) == {m.pk for m in managers}
    assert inactive.pk not in _recipients(delivery)


def test_a_department_task_notifies_only_managers_of_that_department():
    mine, other = DepartmentFactory.create(), DepartmentFactory.create()
    member = grant(UserFactory.create(), AppPermission.MANAGE_DEPARTMENT_TASKS)
    DepartmentMembershipFactory.create(department=mine, user=member)
    outsider = grant(UserFactory.create(), AppPermission.MANAGE_DEPARTMENT_TASKS)
    DepartmentMembershipFactory.create(department=other, user=outsider)
    everywhere = grant(
        UserFactory.create(), AppPermission.MANAGE_DEPARTMENT_TASKS, AppPermission.MANAGE_ALL_DEPARTMENTS
    )
    destination = _in_app(scope=DestinationScope.DEPARTMENT, department=mine)
    task = _task(TaskScope.DEPARTMENT, destination, department=mine)
    delivery = _finish(task, system_user(), launch=RunLaunch.WEBHOOK)
    assert _recipients(delivery) == {member.pk, everywhere.pk}


def test_a_runner_who_lost_the_permission_is_not_notified():
    runner = grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)
    task = _task()
    run = start_task_run(task, inputs={}, actor=runner, launch=RunLaunch.MANUAL)
    runner.user_permissions.clear()
    run.set_status(RunStatus.RUNNING)
    run.set_status(RunStatus.COMPLETED)
    delivery = Delivery.objects.get(run=run)
    send_delivery(delivery.pk)
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.FAILED
    assert "受け取れる人" in delivery.last_error
    assert not Notification.objects.exists()


def test_sending_again_does_not_duplicate():
    runner = grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)
    delivery = _finish(_task(), runner)
    Delivery.objects.filter(pk=delivery.pk).update(status=DeliveryStatus.FAILED)
    delivery.retry()
    send_delivery(delivery.pk)
    assert Notification.objects.filter(delivery=delivery).count() == 1


def test_a_sensitive_company_task_can_use_in_app_without_the_flag():
    destination = _in_app()
    assert destination.problem_for(_task(destination=destination), sensitive=True) == ""
    webhook = DestinationFactory.create()
    assert webhook.problem_for(_task(destination=webhook), sensitive=True)


# --- 通知欄のAPI ---


def test_the_list_shows_only_my_notifications_and_marks_them_read():
    me = grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)
    other = grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)
    task = _task()
    for _ in range(2):
        _finish(task, me)
    theirs = Notification.objects.get(delivery=_finish(task, other))
    client = APIClient()
    client.force_login(me)
    res = client.get(NOTIFICATIONS)
    assert res.status_code == 200
    items = res.json()["results"]
    assert len(items) == 2
    assert items[0]["task_name"] == task.name
    assert items[0]["event_display"] == "完了"
    assert set(items[0]) == {
        "id",
        "event_type",
        "event_display",
        "task",
        "task_name",
        "message",
        "run",
        "read",
        "created",
    }
    assert client.get(f"{NOTIFICATIONS}unread_count/").json() == {"unread": 2}

    read = client.post(f"{NOTIFICATIONS}{items[0]['id']}/read/")
    assert read.json()["read"] is not None
    assert client.get(f"{NOTIFICATIONS}unread_count/").json() == {"unread": 1}
    assert len(client.get(NOTIFICATIONS, {"unread": "true"}).json()["results"]) == 1
    # 他の人の通知は既読にできない
    assert client.post(f"{NOTIFICATIONS}{theirs.uid}/read/").status_code == 404

    assert client.post(f"{NOTIFICATIONS}read_all/").json() == {"unread": 0}
    assert client.get(f"{NOTIFICATIONS}unread_count/").json() == {"unread": 0}
    assert Notification.objects.get(pk=theirs.pk).read is None


def test_notifications_are_hidden_after_losing_the_permission():
    """送った後に権限を失ったら、そのRunの通知は見せない（Runの結果を見られる人と同じ。authorization.md §7.1）"""
    manager = grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)
    _finish(_task(), manager)
    client = APIClient()
    client.force_login(manager)
    assert client.get(f"{NOTIFICATIONS}unread_count/").json() == {"unread": 1}
    manager.user_permissions.clear()
    client.force_login(manager)
    assert client.get(NOTIFICATIONS).json()["results"] == []
    assert client.get(f"{NOTIFICATIONS}unread_count/").json() == {"unread": 0}
    notification = Notification.objects.get(user=manager)
    assert client.post(f"{NOTIFICATIONS}{notification.uid}/read/").status_code == 404
    client.post(f"{NOTIFICATIONS}read_all/")
    assert Notification.objects.get(pk=notification.pk).read is None


# --- 出口の管理 ---


def test_an_in_app_destination_has_no_url():
    admin = grant(UserFactory.create(), "delivery.add_destination", "delivery.view_destination")
    client = APIClient()
    client.force_login(admin)
    body = {"kind": DestinationKind.IN_APP, "scope": DestinationScope.COMPANY, "name": "画面"}
    res = client.post("/api/v1/destinations/", body, format="json")
    assert res.status_code == 201, res.json()
    res = client.post("/api/v1/destinations/", {**body, "url": "https://hooks.example.com/x"}, format="json")
    assert "url" in res.json()
    res = client.post("/api/v1/destinations/", {**body, "kind": DestinationKind.WEBHOOK}, format="json")
    assert "url" in res.json()
    # 個人の出口はYorikiが作り、管理では作れない・一覧に出さない
    personal = Destination.personal_in_app()
    res = client.post("/api/v1/destinations/", {**body, "scope": DestinationScope.PERSONAL}, format="json")
    assert "scope" in res.json()
    assert personal.uid not in [d["id"] for d in client.get("/api/v1/destinations/").json()["results"]]
