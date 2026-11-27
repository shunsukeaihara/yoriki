"""タスクの実行の論理削除（design.md §8、authorization.md §7.1）。ログを見られる人が消し、消した実行は誰にも見せない"""

import pytest
from auditlog.models import LogEntry

from conversation.factories import ConversationFactory, ConversationMessageFactory
from conversation.models import Conversation, MessageRole
from delivery.checks import send_problem
from delivery.factories import DestinationFactory
from delivery.models import Delivery
from run.models import Run, RunEvent, RunEventKind, RunStatus
from task.models import TaskScope
from task.tests.test_chat_launch import _launch, _task
from task.tests.test_fix_chat import _fix
from task.tests.test_run_history import _ids, _manager, _run
from user.factories import DepartmentFactory, DepartmentMembershipFactory, UserFactory, grant
from utils.permissions import AppPermission

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("default_runtime")]


def _delete(api_client, user, run: Run):
    assert run.task is not None
    api_client.force_login(user)
    return api_client.delete(f"/api/app/v1/tasks/{run.task.uid}/runs/{run.uid}/")


@pytest.fixture
def department():
    return DepartmentFactory.create()


@pytest.fixture
def manager(department):
    return _manager(department)


@pytest.fixture
def member(department):
    user = UserFactory.create()
    DepartmentMembershipFactory.create(department=department, user=user)
    return user


def test_manager_deletes_a_run_and_nobody_sees_it(api_client, department, manager, member):
    task = _task(TaskScope.DEPARTMENT, department=department)
    conversation = ConversationFactory.create(owner=member)
    run = Run.objects.get(uid=_launch(api_client, member, task, conversation).json()["id"])
    ConversationMessageFactory.create(conversation=run.conversation, run=run, role=MessageRole.AGENT, body="確認済み")
    kept = Run.objects.get(uid=_run(api_client, manager, task))

    res = _delete(api_client, manager, run)
    assert res.status_code == 204
    run.refresh_from_db()
    # 動いていたので停止を依頼し、ワークスペースを消すと決めた。履歴は残す
    assert run.status == RunStatus.CANCELLED
    assert (run.conversation.deleted, run.conversation.workspace_discarded is not None) == (True, True)
    assert run.conversation.messages.exists()
    # 操作した人を監査ログに残す
    entry = LogEntry.objects.get_for_object(run.conversation).filter(action=LogEntry.Action.UPDATE).get()
    assert entry.changes["deleted"] == ["False", "True"]
    assert entry.actor == manager

    # 管理できる人にも、起動した人にも見せない
    assert _ids(api_client, manager) == [kept.uid]
    assert _ids(api_client, member) == []
    api_client.force_login(manager)
    assert [r["id"] for r in api_client.get(f"/api/app/v1/tasks/{task.uid}/runs/").json()["results"]] == [kept.uid]
    assert api_client.get(f"/api/app/v1/tasks/{task.uid}/runs/{run.uid}/").status_code == 404
    assert api_client.get(f"/api/app/v1/runs/{run.uid}/artifacts/").status_code == 404
    api_client.force_login(member)
    assert api_client.get(f"/api/app/v1/tasks/{task.uid}/runs/{run.uid}/").status_code == 404
    # 起動した会話のカードは「削除された」とだけ出し、応答は出さない
    messages = api_client.get(f"/api/app/v1/conversations/{conversation.uid}/messages/").json()["results"]
    [card] = [m["task_run"] for m in messages if m["task_run"]]
    assert (card["id"], card["deleted"], card["response"]) == (run.uid, True, "")
    # もう一度消そうとしても見つからない
    assert _delete(api_client, manager, run).status_code == 404


def test_only_those_who_see_the_logs_delete(api_client, department, manager, member):
    task = _task(TaskScope.DEPARTMENT, department=department)
    run = Run.objects.get(uid=_launch(api_client, member, task).json()["id"])
    # 起動した人は結果を見られるが、ログは見られないので消せない
    assert _delete(api_client, member, run).status_code == 403
    assert _delete(api_client, UserFactory.create(), run).status_code == 404
    assert _delete(api_client, _manager(DepartmentFactory.create()), run).status_code == 404
    assert not Conversation.objects.get(pk=run.conversation_id).deleted


def test_runs_in_a_fix_chat_are_deleted_with_the_fix_chat(api_client):
    manager = grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)
    task = _task(TaskScope.COMPANY)
    res = _fix(api_client, manager, task)
    assert res.status_code == 201, res.json()
    run = Run.objects.get(uid=res.json()["id"])
    res = _delete(api_client, manager, run)
    assert res.status_code == 400
    run.conversation.refresh_from_db()
    assert not run.conversation.deleted


def test_deliveries_of_a_deleted_run_are_not_sent(api_client):
    manager = grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)
    task = _task(destination=DestinationFactory.create(), notify_events=["run.completed"])
    run = Run.objects.get(uid=_run(api_client, manager, task))
    assert run.destination_revision is not None
    delivery = Delivery.objects.create(
        run=run,
        run_event=RunEvent.record(run, RunEventKind.STATUS, {"status": RunStatus.COMPLETED}),
        event_type="run.completed",
        destination_revision=run.destination_revision,
        payload={},
    )
    assert send_problem(delivery) == ""
    assert _delete(api_client, manager, run).status_code == 204
    delivery.refresh_from_db()
    assert send_problem(delivery) == "実行が削除されました"
