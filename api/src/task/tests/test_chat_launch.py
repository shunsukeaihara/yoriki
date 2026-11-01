"""チャットからのタスクの起動（authorization.md §5.3・§6・§7.1）。起動できる人、起動した人の権限、会話のカード、
起動した人が見られる結果"""

import pytest

from artifact.factories import ArtifactFactory
from artifact.models import ArtifactKind
from connector.factories import ConnectorFactory
from connector.models import ConnectorScope
from conversation.factories import ConversationFactory, ConversationMessageFactory
from conversation.models import ConversationMessage, MessageRole
from delivery.checks import send_problem
from delivery.factories import DestinationFactory
from delivery.models import Delivery, DestinationScope
from llm.factories import ModelProfileFactory
from run.models import Run, RunEvent, RunEventKind, RunLaunch, RunStatus, RunTrigger
from skill.factories import SkillFactory, SkillVersionFactory
from skill.models import SkillScope
from task.factories import TaskDefinitionFactory
from task.models import TaskDefinition, TaskScope
from user.factories import DepartmentFactory, DepartmentMembershipFactory, UserFactory, grant
from utils.permissions import AppPermission

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("default_runtime")]

LAUNCHABLE = "/api/app/v1/launchable_tasks/"
INPUTS = [{"name": "vendor", "label": "取引先", "type": "string", "required": True}]


def _task(scope=TaskScope.COMPANY, *, department=None, name="請求書チェック", **revision):
    revision.setdefault("prompt_template", "{{ inputs.vendor }} の請求書を確認してください。")
    revision.setdefault("inputs", INPUTS)
    revision.setdefault("model_profile", ModelProfileFactory.create(version__supports_tools=True))
    return TaskDefinitionFactory.create(
        scope=scope,
        department=department,
        name=name,
        chat_launchable=True,
        **{f"revision__{k}": v for k, v in revision.items()},
    )


def _launch(api_client, user, task, conversation=None, inputs=None, **params):
    conversation = conversation or ConversationFactory.create(owner=user)
    api_client.force_login(user)
    query = "&".join(f"{k}={v}" for k, v in {"conversation": conversation.uid, **params}.items())
    return api_client.post(
        f"{LAUNCHABLE}{task.uid}/run/?{query}", {"inputs": inputs or {"vendor": "A商事"}}, format="json"
    )


def _names(api_client, user) -> list[str]:
    api_client.force_login(user)
    return [t["name"] for t in api_client.get(LAUNCHABLE).json()["results"]]


# --- 起動できる人と候補 ---


def test_launchable_tasks_follow_the_scope_and_the_mark(api_client, app_user):
    department = DepartmentFactory.create()
    DepartmentMembershipFactory.create(department=department, user=app_user)
    _task(name="全社")
    _task(TaskScope.DEPARTMENT, department=department, name="自部門")
    _task(TaskScope.DEPARTMENT, department=DepartmentFactory.create(), name="他部門")
    TaskDefinitionFactory.create(scope=TaskScope.PERSONAL, created_by=app_user, chat_launchable=True, name="自分")
    TaskDefinitionFactory.create(scope=TaskScope.PERSONAL, chat_launchable=True, name="他人")
    TaskDefinitionFactory.create(scope=TaskScope.COMPANY, name="印なし")
    suspended = _task(name="停止中")
    suspended.set_suspended(True)
    assert sorted(_names(api_client, app_user)) == sorted(["全社", "自部門", "自分"])


def test_department_tasks_are_not_launched_by_the_audit_permission(api_client):
    """すべての部門を使える（監査の）permissionでは広がらない。管理できる部門なら起動できる"""
    department = DepartmentFactory.create()
    _task(TaskScope.DEPARTMENT, department=department, name="部門")
    auditor = grant(UserFactory.create(), AppPermission.USE_ALL_DEPARTMENTS)
    assert _names(api_client, auditor) == []
    admin = grant(UserFactory.create(), AppPermission.MANAGE_ALL_DEPARTMENTS)
    assert _names(api_client, admin) == ["部門"]


def test_candidates_show_only_the_name_description_and_inputs(api_client, app_user):
    task = _task(name="請求書チェック", description="請求書を確認します")
    api_client.force_login(app_user)
    [data] = api_client.get(LAUNCHABLE).json()["results"]
    assert (data["id"], data["description"], data["version"]) == (task.uid, "請求書を確認します", 1)
    assert [i["name"] for i in data["inputs"]] == ["vendor"]
    # 依頼文・使うもの・出口は、管理できる人だけに見せる
    assert not {"prompt_template", "skills", "connectors", "destination"} & set(data)


def test_system_tasks_cannot_be_marked(api_client):
    admin = grant(UserFactory.create(), AppPermission.MANAGE_SYSTEM_TASKS)
    task = TaskDefinitionFactory.create(scope=TaskScope.SYSTEM, created_by=admin)
    api_client.force_login(admin)
    res = api_client.post(f"/api/app/v1/tasks/{task.uid}/chat_launch/", {"chat_launchable": True}, format="json")
    assert res.status_code == 400
    assert not TaskDefinition.objects.get(pk=task.pk).chat_launchable


def test_managers_switch_the_mark_without_a_new_revision(api_client, app_user):
    task = TaskDefinitionFactory.create(created_by=app_user)
    api_client.force_login(app_user)
    url = f"/api/app/v1/tasks/{task.uid}/chat_launch/"
    res = api_client.post(url, {"chat_launchable": True}, format="json")
    assert (res.status_code, res.json()["chat_launchable"], res.json()["version"]) == (200, True, 1)
    api_client.force_login(UserFactory.create())
    assert api_client.post(url, {"chat_launchable": False}, format="json").status_code == 404


# --- 起動 ---


def test_launch_starts_a_run_of_the_launcher_and_adds_a_card(api_client, app_user):
    task = _task(name="請求書チェック")
    conversation = ConversationFactory.create(owner=app_user)
    ConversationMessageFactory.create(conversation=conversation, body="前の会話の内容")
    res = _launch(api_client, app_user, task, conversation, version=1)
    assert res.status_code == 201, res.json()
    run = Run.objects.get(uid=res.json()["id"])
    assert (run.trigger, run.launch, run.owner, run.task) == (RunTrigger.TASK, RunLaunch.CHAT, app_user, task)
    # タスクのRunは会話の文脈を引き継がない（Runごとの会話で、最初の依頼はタスクの定義と入力だけ）
    assert run.conversation != conversation
    [first] = run.conversation.messages.all()
    assert "前の会話の内容" not in first.body
    # 起動した会話にはカードを足す
    card = ConversationMessage.objects.get(conversation=conversation, task_run=run)
    assert card.role == MessageRole.SYSTEM
    messages = api_client.get(f"/api/app/v1/conversations/{conversation.uid}/messages/").json()["results"]
    [shown] = [m for m in messages if m["task_run"]]
    assert (shown["task_run"]["id"], shown["task_run"]["task_name"]) == (run.uid, "請求書チェック")
    assert shown["task_run"]["response"] == ""


def test_launch_needs_an_own_conversation_and_the_current_version(api_client, app_user):
    task = _task()
    other = ConversationFactory.create()
    assert _launch(api_client, app_user, task, other).status_code == 404
    assert _launch(api_client, app_user, task, version=2).status_code == 409
    assert _launch(api_client, app_user, task, inputs={"vendor": ""}).status_code == 400
    assert not Run.objects.exists()


def test_launch_is_refused_when_the_launcher_cannot_use_a_fixed_connector(api_client, app_user):
    department = DepartmentFactory.create()
    DepartmentMembershipFactory.create(department=department, user=app_user)
    connector = ConnectorFactory.create(name="経理DB", scope=ConnectorScope.DEPARTMENT, department=department)
    task = _task(TaskScope.DEPARTMENT, department=department, connectors=[connector.uid])
    # 定義の後で、Connectorを別の部門のものにした（起動した人は使えない）
    connector.department = DepartmentFactory.create()
    connector.save(update_fields=["department"])
    res = _launch(api_client, app_user, task)
    assert res.status_code == 400
    assert "Connector「経理DB」を使う権限がない" in res.json()["non_field_errors"][0]


def test_launch_is_refused_for_a_task_without_the_mark(api_client, app_user):
    task = _task()
    task.set_chat_launchable(False)
    assert _launch(api_client, app_user, task).status_code == 404


# --- 起動した人が見られるもの（authorization.md §7.1） ---


def test_launcher_sees_only_the_results(api_client, app_user):
    department = DepartmentFactory.create()
    membership = DepartmentMembershipFactory.create(department=department, user=app_user)
    skill = SkillFactory.create(scope=SkillScope.COMPANY, owner=None)
    SkillVersionFactory.create(skill=skill)
    task = _task(TaskScope.DEPARTMENT, department=department, skills=[{"skill": skill.uid, "version": 1}])
    run = Run.objects.get(uid=_launch(api_client, app_user, task).json()["id"])
    ConversationMessageFactory.create(conversation=run.conversation, run=run, role=MessageRole.AGENT, body="完了")
    output = ArtifactFactory.create(conversation=run.conversation, run=run, kind=ArtifactKind.OUTPUT, name="a.csv")
    ArtifactFactory.create(conversation=run.conversation, run=run, kind=ArtifactKind.CONNECTOR_RESULT, name="q.csv")
    detail = f"/api/app/v1/tasks/{task.uid}/runs/{run.uid}/"

    # 起動した後に部門を離れても、自分が起動したRunの結果は見られる
    membership.delete()
    data = api_client.get(detail).json()
    assert (data["can_view_logs"], data["skills"], data["todos"], data["deliveries_info"]) == (False, [], [], [])
    assert data["started_by"]["id"] == app_user.uid
    messages = api_client.get(f"{detail}messages/").json()["results"]
    assert [(m["role"], m["body"]) for m in messages] == [(MessageRole.AGENT, "完了")]
    artifacts = api_client.get(f"/api/app/v1/runs/{run.uid}/artifacts/").json()["results"]
    assert [a["id"] for a in artifacts] == [output.uid]
    # ログ（イベント・ストリーム）と一覧は見られない
    assert api_client.get(f"/api/app/v1/runs/{run.uid}/events/").status_code == 404
    assert api_client.get(f"/api/app/v1/runs/{run.uid}/").status_code == 404
    assert api_client.get(f"/api/app/v1/tasks/{task.uid}/runs/").status_code == 404
    # 自分が起動したRunは止められる
    res = api_client.post(f"{detail}cancel/", {"reason": ""}, format="json")
    assert (res.status_code, res.json()["can_view_logs"]) == (200, False)
    assert Run.objects.get(pk=run.pk).status == RunStatus.CANCELLED

    # 管理できる人は、起動した人の入力と処理の記録を見られる
    manager = grant(UserFactory.create(), AppPermission.MANAGE_DEPARTMENT_TASKS)
    DepartmentMembershipFactory.create(department=department, user=manager)
    api_client.force_login(manager)
    data = api_client.get(detail).json()
    assert data["can_view_logs"]
    assert data["inputs"] == [{"name": "vendor", "label": "取引先", "value": "A商事"}]
    assert len(data["skills"]) == 1
    roles = [m["role"] for m in api_client.get(f"{detail}messages/").json()["results"]]
    assert sorted(roles) == [MessageRole.USER, MessageRole.AGENT]

    # ほかの人は見られない
    api_client.force_login(UserFactory.create())
    assert api_client.get(detail).status_code == 404
    assert api_client.get(f"/api/app/v1/runs/{run.uid}/artifacts/").status_code == 404


def test_card_shows_the_final_response(api_client, app_user):
    task = _task()
    conversation = ConversationFactory.create(owner=app_user)
    run = Run.objects.get(uid=_launch(api_client, app_user, task, conversation).json()["id"])
    ConversationMessageFactory.create(conversation=run.conversation, run=run, role=MessageRole.AGENT, body="確認済み")
    RunEvent.record(run, RunEventKind.STATUS, {"status": RunStatus.RUNNING})
    messages = api_client.get(f"/api/app/v1/conversations/{conversation.uid}/messages/").json()["results"]
    [card] = [m["task_run"] for m in messages if m["task_run"]]
    assert card["response"] == "確認済み"


def test_delivery_is_not_sent_after_the_launcher_is_deactivated(api_client, app_user):
    task = _task(destination=DestinationFactory.create(), notify_events=["run.completed"])
    run = Run.objects.get(uid=_launch(api_client, app_user, task).json()["id"])
    assert run.destination_revision is not None
    delivery = Delivery.objects.create(
        run=run,
        run_event=RunEvent.record(run, RunEventKind.STATUS, {"status": RunStatus.COMPLETED}),
        event_type="run.completed",
        destination_revision=run.destination_revision,
        payload={},
    )
    assert send_problem(delivery) == ""
    app_user.is_active = False
    app_user.save(update_fields=["is_active"])
    assert "起動した人" in send_problem(delivery)


def test_launcher_does_not_see_the_error_text(api_client, app_user):
    task = _task()
    conversation = ConversationFactory.create(owner=app_user)
    run = Run.objects.get(uid=_launch(api_client, app_user, task, conversation).json()["id"])
    Run.objects.filter(pk=run.pk).update(status=RunStatus.FAILED, error="provider: 内部のエラー文", step_count=3)
    data = api_client.get(f"/api/app/v1/tasks/{task.uid}/runs/{run.uid}/").json()
    assert (data["error"], data["step_count"], data["model_profile_name"]) == ("", 0, "")
    messages = api_client.get(f"/api/app/v1/conversations/{conversation.uid}/messages/").json()["results"]
    [card] = [m["task_run"] for m in messages if m["task_run"]]
    assert "error" not in card
    assert card["status"] == RunStatus.FAILED


def test_resending_the_same_launch_returns_the_first_run(api_client, app_user):
    task = _task()
    conversation = ConversationFactory.create(owner=app_user)
    first = _launch(api_client, app_user, task, conversation, client_request_id="req-1")
    assert first.status_code == 201
    again = _launch(api_client, app_user, task, conversation, client_request_id="req-1")
    assert (again.status_code, again.json()["id"]) == (200, first.json()["id"])
    assert Run.objects.filter(task=task).count() == 1
    assert ConversationMessage.objects.filter(conversation=conversation).exclude(task_run=None).count() == 1
    # 起動の後に印を外しても、同じ要求の再送には前のRunを返す
    task.set_chat_launchable(False)
    again = _launch(api_client, app_user, task, conversation, client_request_id="req-1")
    assert (again.status_code, again.json()["id"]) == (200, first.json()["id"])
    task.set_chat_launchable(True)
    # 別の起動（別のID）は新しいRun
    assert _launch(api_client, app_user, task, conversation, client_request_id="req-2").status_code == 201
    assert Run.objects.filter(task=task).count() == 2


def test_delivery_is_not_sent_after_the_launcher_lost_a_fixed_connector(api_client, app_user):
    department = DepartmentFactory.create()
    membership = DepartmentMembershipFactory.create(department=department, user=app_user)
    connector = ConnectorFactory.create(name="経理DB", scope=ConnectorScope.DEPARTMENT, department=department)
    destination = DestinationFactory.create(scope=DestinationScope.DEPARTMENT, department=department)
    task = _task(
        TaskScope.DEPARTMENT,
        department=department,
        connectors=[connector.uid],
        destination=destination,
        notify_events=["run.completed"],
    )
    run = Run.objects.get(uid=_launch(api_client, app_user, task).json()["id"])
    assert run.destination_revision is not None
    delivery = Delivery.objects.create(
        run=run,
        run_event=RunEvent.record(run, RunEventKind.STATUS, {"status": RunStatus.COMPLETED}),
        event_type="run.completed",
        destination_revision=run.destination_revision,
        payload={},
    )
    assert send_problem(delivery) == ""
    membership.delete()  # 起動した後に部門を離れた
    assert "経理DB" in send_problem(delivery)
