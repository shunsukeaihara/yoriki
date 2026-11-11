"""タスクをまたいだ実行の履歴（design.md §11.9、authorization.md §7.1）。本番の実行のうち、Runの結果を見られるものを
並べる。修正チャットのRunは出さない"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from conversation.factories import ConversationFactory
from delivery.factories import DestinationFactory
from run.factories import RunFactory
from run.models import Run, RunLaunch, RunStatus
from task.models import TaskDefinition, TaskScope
from task.tests.test_chat_launch import _launch, _task
from task.tests.test_fix_chat import _fix
from user.factories import DepartmentFactory, DepartmentMembershipFactory, UserFactory, grant
from utils.permissions import AppPermission

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("default_runtime")]

HISTORY = "/api/app/v1/task_runs/"


def _manager(department):
    user = UserFactory.create()
    DepartmentMembershipFactory.create(department=department, user=user)
    return grant(user, AppPermission.MANAGE_DEPARTMENT_TASKS)


def _run(api_client, user, task, vendor="A商事") -> str:
    api_client.force_login(user)
    res = api_client.post(f"/api/app/v1/tasks/{task.uid}/run/", {"inputs": {"vendor": vendor}}, format="json")
    assert res.status_code == 201, res.json()
    return res.json()["id"]


def _results(api_client, user, **params) -> list[dict]:
    api_client.force_login(user)
    res = api_client.get(HISTORY, params)
    assert res.status_code == 200, res.json()
    return res.json()["results"]


def _ids(api_client, user, **params) -> list[str]:
    return [r["id"] for r in _results(api_client, user, **params)]


def test_history_lists_production_runs_whose_results_the_user_sees(api_client):
    department = DepartmentFactory.create()
    manager = _manager(department)
    task = _task(TaskScope.DEPARTMENT, department=department)
    member = UserFactory.create()
    DepartmentMembershipFactory.create(department=department, user=member)

    manual = _run(api_client, manager, task)
    chat = _launch(api_client, member, task).json()["id"]
    fix = _fix(api_client, manager, task)
    assert fix.status_code == 201, fix.json()
    RunFactory.create(conversation=ConversationFactory.create(owner=manager))  # 会話のRunは出さない

    # 管理できる人には本番の実行（修正チャットのRunは出さない）を新しい順に、ログも見られるものとして返す
    results = _results(api_client, manager)
    assert [(r["id"], r["task"], r["task_name"], r["can_view_logs"]) for r in results] == [
        (chat, task.uid, "請求書チェック", True),
        (manual, task.uid, "請求書チェック", True),
    ]
    # 起動した人には自分が起動したRunの結果だけ（配信・使ったSkillはログなので空）
    [launched] = _results(api_client, member)
    assert (launched["id"], launched["can_view_logs"], launched["deliveries_info"], launched["skills"]) == (
        chat,
        False,
        [],
        [],
    )
    assert _ids(api_client, UserFactory.create()) == []
    # タスクの画面の実行の履歴にも、修正チャットのRunは出さない
    api_client.force_login(manager)
    listed = api_client.get(f"/api/app/v1/tasks/{task.uid}/runs/").json()["results"]
    assert [r["id"] for r in listed] == [chat, manual]


def test_history_keeps_runs_of_moved_or_deleted_tasks(api_client):
    """見られるかは実行したときの範囲と部門で決める（タスクの画面の一覧と違い、今の範囲では判定しない）"""
    department = DepartmentFactory.create()
    manager = _manager(department)
    task = _task(TaskScope.DEPARTMENT, department=department)
    run = _run(api_client, manager, task)

    other = DepartmentFactory.create()
    TaskDefinition.objects.filter(pk=task.pk).update(department=other)
    assert _ids(api_client, manager) == [run]
    assert _ids(api_client, _manager(other)) == []
    TaskDefinition.objects.filter(pk=task.pk).update(department=department, deleted=True)
    assert _ids(api_client, manager) == [run]


def test_history_is_filtered_by_status_launch_and_fixed_task_name(api_client):
    manager = grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)
    invoice = _task(name="請求書チェック")
    contract = _task(name="契約書レビュー")
    first = _run(api_client, manager, invoice)
    second = _run(api_client, manager, contract)
    chat = _launch(api_client, manager, invoice).json()["id"]
    Run.objects.filter(uid=first).update(status=RunStatus.FAILED)

    assert _ids(api_client, manager, status=RunStatus.FAILED) == [first]
    assert _ids(api_client, manager, launch=RunLaunch.CHAT) == [chat]
    assert _ids(api_client, manager, task_name="請求書") == [chat, first]
    # 名前は受付時にManifestへ固定したもので絞る（後で名前を変えても、過去のRunは前の名前で出る）
    TaskDefinition.objects.filter(pk=contract.pk).update(name="契約書の確認")
    assert _ids(api_client, manager, task_name="契約書レビュー") == [second]
    assert _ids(api_client, manager, task_name="契約書の確認") == []


def test_history_is_listed_without_a_query_per_run(api_client):
    manager = grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)
    tasks = [_task(name=f"タスク{i}", destination=DestinationFactory.create()) for i in range(3)]
    _run(api_client, manager, tasks[0])
    _launch(api_client, manager, tasks[1])

    def count():
        api_client.force_login(manager)
        with CaptureQueriesContext(connection) as queries:
            res = api_client.get(HISTORY)
        assert res.status_code == 200
        return len(queries), len(res.json()["results"])

    before, _ = count()
    for task in tasks:
        _run(api_client, manager, task)
        _launch(api_client, manager, task)
    after, listed = count()
    assert (listed, after) == (8, before)
