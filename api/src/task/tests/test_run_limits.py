"""タスク単位の受付頻度と同時実行数（design.md §9.3）。受付での確認、数えるRun、入口の応答、APIでの設定"""

from datetime import timedelta

import pytest
from django.utils import timezone

from artifact.models import BlobObject
from llm.factories import ModelProfileFactory
from run.factories import RunFactory
from run.models import Run, RunLaunch, RunStatus, RunTrigger
from task.dispatch import check_run_limits, start_task_run
from task.errors import CannotRun, RunLimitReached
from task.models import TaskDefinition, TriggerReceipt
from task.tests.test_api import _create
from task.tests.test_chat_launch import _launch
from task.tests.test_fix_chat import _fix
from task.tests.test_webhook import (  # noqa: F401  fixtureを使う
    _payload,
    _preview,
    _send,
    fixture_manager,
    fixture_task,
    fixture_webhook,
)
from usage.models import BudgetSettings

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("default_runtime")]


def _limit(task: TaskDefinition, **values) -> None:
    TaskDefinition.objects.filter(pk=task.pk).update(**values)
    task.refresh_from_db()


def _run(task: TaskDefinition, launch=RunLaunch.MANUAL, **kwargs) -> Run:
    return RunFactory.create(task=task, trigger=RunTrigger.TASK, launch=launch, **kwargs)


def _manual(api_client, user, task):
    api_client.force_login(user)
    return api_client.post(f"/api/app/v1/tasks/{task.uid}/run/", {"inputs": {"vendor": "A商事"}}, format="json")


# --- 数えるRun ---


def test_no_limit_by_default(task):
    assert (task.hourly_runs_limit, task.concurrent_runs_limit) == (None, None)
    for _ in range(3):
        _run(task, status=RunStatus.RUNNING)
    check_run_limits(task)


def test_hourly_limit_counts_production_runs_accepted_in_the_last_hour(task):
    _limit(task, hourly_runs_limit=3)
    # 終わったRunも数える。1時間より前のRun、修正チャットのRun、ほかのタスクのRunは数えない
    _run(task, status=RunStatus.FAILED)
    _run(task, RunLaunch.WEBHOOK, status=RunStatus.COMPLETED)
    old = _run(task, RunLaunch.CHAT)
    Run.objects.filter(pk=old.pk).update(created=timezone.now() - timedelta(hours=1, seconds=1))
    _run(task, RunLaunch.FIX)
    RunFactory.create(trigger=RunTrigger.TASK, launch=RunLaunch.MANUAL)
    check_run_limits(task)

    _run(task, RunLaunch.CHAT, status=RunStatus.CANCELLED)
    with pytest.raises(RunLimitReached, match="1時間あたりの実行回数の上限（3回）"):
        check_run_limits(task)


def test_concurrent_limit_does_not_count_waiting_for_the_user_or_finished(task):
    _limit(task, concurrent_runs_limit=5)
    for status in (RunStatus.WAITING_USER, RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED):
        _run(task, status=status)
    _run(task, RunLaunch.FIX, status=RunStatus.RUNNING)
    counted = (
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.RETRY_WAIT,
        RunStatus.CANCELLING,
        RunStatus.WAITING_BACKGROUND,
    )
    for status in counted[:4]:
        _run(task, status=status)
    check_run_limits(task)
    _run(task, RunLaunch.WEBHOOK, status=counted[4])
    with pytest.raises(RunLimitReached, match="同時に動かせる数の上限（5件）"):
        check_run_limits(task)


# --- 受付 ---


def test_manual_runs_are_refused_at_the_limit_and_accepted_when_a_run_ends(api_client, manager, task):
    _limit(task, concurrent_runs_limit=1)
    first = _manual(api_client, manager, task)
    assert first.status_code == 201, first.json()
    res = _manual(api_client, manager, task)
    assert res.status_code == 400
    assert "同時に動かせる数の上限（1件）" in res.json()["non_field_errors"][0]
    assert Run.objects.count() == 1

    # 動いているRunは止めない。終われば次を受け付ける
    assert Run.objects.get().status == RunStatus.QUEUED
    Run.objects.update(status=RunStatus.COMPLETED)
    assert _manual(api_client, manager, task).status_code == 201

    _limit(task, concurrent_runs_limit=None, hourly_runs_limit=2)
    res = _manual(api_client, manager, task)
    assert res.status_code == 400
    assert "1時間あたりの実行回数の上限（2回）" in res.json()["non_field_errors"][0]


def test_chat_launches_are_refused_at_the_limit(api_client, app_user, task):
    _limit(task, chat_launchable=True, hourly_runs_limit=1)
    assert _launch(api_client, app_user, task).status_code == 201
    res = _launch(api_client, app_user, task)
    assert res.status_code == 400
    assert "1時間あたりの実行回数" in res.json()["non_field_errors"][0]
    assert Run.objects.count() == 1


def test_fix_chat_runs_are_neither_counted_nor_refused(api_client, manager, task):
    _limit(task, hourly_runs_limit=1, concurrent_runs_limit=1)
    # 修正チャットの実行は上限に達していても動かせ、本番の実行の枠を使わない
    _fix(api_client, manager, task)
    _fix(api_client, manager, task)
    assert _manual(api_client, manager, task).status_code == 201
    assert _manual(api_client, manager, task).status_code == 400
    _fix(api_client, manager, task)
    assert Run.objects.filter(launch=RunLaunch.FIX).count() == 3


def test_the_limits_of_the_budget_also_apply(manager, task):
    _limit(task, concurrent_runs_limit=10)
    settings = BudgetSettings.load()
    settings.system_concurrent_runs = 1
    settings.save()
    start_task_run(task, inputs={"vendor": "A商事"}, actor=manager, launch=RunLaunch.MANUAL)
    with pytest.raises(CannotRun, match=r"「.+」で同時に動かせる数の上限（1件）"):
        start_task_run(task, inputs={"vendor": "A商事"}, actor=manager, launch=RunLaunch.MANUAL)


# --- 入口 ---


def test_a_webhook_is_refused_with_429_and_leaves_nothing(api_client, task, webhook):
    _binding, key = webhook
    _limit(task, hourly_runs_limit=1)
    first = _send(api_client, task, key, _payload("evt-1"))
    assert first.status_code == 202, first.json()
    blobs = BlobObject.objects.count()

    res = _send(api_client, task, key, _payload("evt-2"))
    assert res.status_code == 429
    # タスクの設定の中身（上限の値）は送り元に返さない
    assert res.json() == {
        "code": "task_run_limit",
        "detail": "このタスクの受付の上限に達しています。しばらくしてから送り直してください",
    }
    assert Run.objects.count() == 1
    assert TriggerReceipt.objects.count() == 1
    assert BlobObject.objects.count() == blobs

    # 受付済みのイベントの再送には、上限に達していても同じRunを返す
    again = _send(api_client, task, key, _payload("evt-1"))
    assert (again.status_code, again.json()["run_id"]) == (200, first.json()["run_id"])

    # 断ったイベントは、時間をおいて同じイベントIDで送り直せば受け付ける
    Run.objects.update(created=timezone.now() - timedelta(hours=2))
    assert _send(api_client, task, key, _payload("evt-2")).status_code == 202


def test_preview_reports_the_limit(api_client, manager, task, webhook):
    binding, _key = webhook
    _limit(task, concurrent_runs_limit=1)
    _run(task, status=RunStatus.RUNNING)
    api_client.force_login(manager)
    body = _preview(api_client, binding, _payload()).json()
    assert body["outcome"] == "refused"
    assert "同時に動かせる数の上限（1件）" in body["run_problems"][0]


# --- 設定（タスクを管理できる人。版の外） ---


def test_limits_are_set_outside_the_revision(api_client, manager, task):
    api_client.force_login(manager)
    url = f"/api/app/v1/tasks/{task.uid}/"
    data = api_client.get(url).json()
    assert (data["hourly_runs_limit"], data["concurrent_runs_limit"]) == (None, None)

    res = api_client.patch(url, {"version": 1, "hourly_runs_limit": 30, "concurrent_runs_limit": 2}, format="json")
    assert res.status_code == 200, res.json()
    data = res.json()
    assert (data["hourly_runs_limit"], data["concurrent_runs_limit"], data["version"]) == (30, 2, 1)
    # 送った項目だけを変える。空にすれば無制限に戻る
    res = api_client.patch(url, {"version": 1, "hourly_runs_limit": None}, format="json")
    assert (res.json()["hourly_runs_limit"], res.json()["concurrent_runs_limit"]) == (None, 2)
    res = api_client.patch(url, {"version": 1, "description": "直した"}, format="json")
    assert (res.json()["concurrent_runs_limit"], res.json()["version"]) == (2, 2)

    # 1以上（止めるにはタスクを停止する）
    for value in (0, -1, 100_001):
        res = api_client.patch(url, {"version": 2, "concurrent_runs_limit": value}, format="json")
        assert res.status_code == 400, value
        assert "concurrent_runs_limit" in res.json()


def test_limits_can_be_set_on_creation(api_client, app_user):
    model = ModelProfileFactory.create(version__supports_tools=True)
    res = _create(api_client, app_user, model, hourly_runs_limit=5)
    assert res.status_code == 201, res.json()
    assert (res.json()["hourly_runs_limit"], res.json()["concurrent_runs_limit"]) == (5, None)
    task = TaskDefinition.objects.get(uid=res.json()["id"])
    assert task.hourly_runs_limit == 5
