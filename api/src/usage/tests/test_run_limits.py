"""月間実行回数と同時実行数（design.md §9.2）。受付での確認、数えるRun、APIの設定と表示"""

from datetime import timedelta

import pytest

from conversation.dispatch import CannotStart, post_message, start_next_request
from conversation.factories import ConversationFactory
from conversation.models import ConversationMessage, InstructionKind, MessageRole, UserInstruction
from run.factories import RunFactory
from run.models import Run, RunStatus
from task.factories import TaskDefinitionFactory
from task.models import TaskScope
from usage import budget as budgets
from usage.models import Budget, BudgetSettings, month_start
from user.factories import DepartmentFactory, DepartmentMembershipFactory, grant

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("default_runtime")]

SETTINGS = "/api/v1/budgets/settings/"
DEPARTMENTS = "/api/v1/budgets/departments/"
MY = "/api/app/v1/my/budgets/"


def defaults(**values) -> None:
    settings = BudgetSettings.load()
    for name, value in values.items():
        setattr(settings, name, value)
    settings.save()


def test_monthly_runs_count_every_run_accepted_this_month():
    budget = Budget.system()
    defaults(system_monthly_runs=3)
    # 失敗・停止したRunも数える。先月のRunと、ほかの計上先のRunは数えない
    RunFactory.create(budget=budget, status=RunStatus.FAILED)
    RunFactory.create(budget=budget, status=RunStatus.CANCELLED)
    old = RunFactory.create(budget=budget, status=RunStatus.COMPLETED)
    Run.objects.filter(pk=old.pk).update(created=month_start() - timedelta(seconds=1))
    RunFactory.create(budget=Budget.for_department(DepartmentFactory.create()))

    assert budgets.check(budget) == budget
    with pytest.raises(budgets.BudgetUnavailable, match="実行回数の上限（3回）"):
        budgets.check(budget, runs=2)
    RunFactory.create(budget=budget)
    with pytest.raises(budgets.BudgetUnavailable, match="実行回数"):
        budgets.check(budget)


def test_concurrent_runs_do_not_count_waiting_for_the_user_or_finished():
    budget = Budget.system()
    defaults(system_concurrent_runs=5)
    for status in (RunStatus.WAITING_USER, RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED):
        RunFactory.create(budget=budget, status=status)
    counted = (
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.RETRY_WAIT,
        RunStatus.CANCELLING,
        RunStatus.WAITING_BACKGROUND,
    )
    for status in counted[:4]:
        RunFactory.create(budget=budget, status=status)
    budgets.check(budget)
    RunFactory.create(budget=budget, status=counted[4])
    with pytest.raises(budgets.BudgetUnavailable, match="同時に動かせる数の上限（5件）"):
        budgets.check(budget)


def test_department_override_and_zero():
    department, other = DepartmentFactory.create(), DepartmentFactory.create()
    defaults(department_monthly_runs=10, department_concurrent_runs=2)
    budget = Budget.for_department(department)
    assert (budget.monthly_runs_limit(), budget.concurrent_runs_limit()) == (10, 2)
    budget.monthly_runs, budget.concurrent_runs = 0, 7
    budget.save()
    assert (budget.monthly_runs_limit(), budget.concurrent_runs_limit()) == (0, 7)
    # 0ならその部門では使わせない。ほかの部門は既定のまま
    with pytest.raises(budgets.BudgetUnavailable, match="実行回数"):
        budgets.check(budget)
    budgets.check(Budget.for_department(other))
    # システムの枠は部門の既定に従わない
    budgets.check(Budget.system(), runs=100)


def test_conversation_is_not_accepted_over_the_limits(app_user):
    defaults(system_concurrent_runs=1)
    first = ConversationFactory.create(owner=app_user)
    post_message(first, body="請求書を確認して", client_message_id="", actor=app_user)
    second = ConversationFactory.create(owner=app_user)
    with pytest.raises(CannotStart, match="同時に動かせる数"):
        post_message(second, body="契約書を確認して", client_message_id="", actor=app_user)
    assert not Run.objects.filter(conversation=second).exists()

    # 確認待ちのRunは数えないので、枠が空く
    Run.objects.filter(conversation=first).update(status=RunStatus.WAITING_USER)
    post_message(second, body="契約書を確認して", client_message_id="", actor=app_user)
    assert Run.objects.filter(conversation=second).count() == 1


def test_next_request_over_the_limit_leaves_the_reason(app_user):
    conversation = ConversationFactory.create(owner=app_user)
    post_message(conversation, body="請求書を確認して", client_message_id="", actor=app_user)
    message = ConversationMessage.objects.create(
        conversation=conversation, sender=app_user, role=MessageRole.USER, body="次はこれ"
    )
    UserInstruction.objects.create(conversation=conversation, message=message, kind=InstructionKind.NEXT)
    Run.objects.filter(conversation=conversation).update(status=RunStatus.COMPLETED)
    defaults(system_monthly_runs=1)

    assert start_next_request(conversation) is None
    notice = ConversationMessage.objects.get(conversation=conversation, role=MessageRole.SYSTEM)
    assert "実行回数の上限" in notice.body


def test_department_task_counts_on_its_department(app_user):
    department = DepartmentFactory.create()
    task = TaskDefinitionFactory.create(scope=TaskScope.DEPARTMENT, department=department)
    defaults(department_concurrent_runs=1, system_concurrent_runs=0)
    budget = budgets.check(budgets.for_task(task))
    RunFactory.create(budget=budget, status=RunStatus.RUNNING)
    with pytest.raises(budgets.BudgetUnavailable, match=department.name):
        budgets.check(budgets.for_task(task))
    # 受付と同じ判定を見せるだけのとき（入口のサンプル）は、行をロックせずに読む
    with pytest.raises(budgets.BudgetUnavailable):
        budgets.check(budgets.for_task(task), lock=False)


def test_settings_and_overrides_api(api_client, app_user):
    department = DepartmentFactory.create(name="経理部")
    manager = grant(app_user, "usage.view_budget", "usage.change_budget")
    api_client.force_login(manager)
    res = api_client.get(SETTINGS)
    assert res.json()["department_monthly_runs"] is None
    assert res.json()["system"]["monthly_runs_limit"] is None

    RunFactory.create(budget=Budget.system(), status=RunStatus.RUNNING)
    RunFactory.create(budget=Budget.system(), status=RunStatus.COMPLETED)
    res = api_client.patch(
        SETTINGS,
        {"system_monthly_runs": 100, "system_concurrent_runs": 4, "department_monthly_runs": 50},
        format="json",
    )
    assert res.status_code == 200, res.json()
    system = res.json()["system"]
    assert (system["monthly_runs"], system["monthly_runs_limit"]) == (2, 100)
    assert (system["concurrent_runs"], system["concurrent_runs_limit"]) == (1, 4)
    assert api_client.patch(SETTINGS, {"system_monthly_runs": -1}, format="json").status_code == 400

    # 部門の上書き。送った項目だけを変える
    url = f"{DEPARTMENTS}{department.uid}/"
    res = api_client.patch(url, {"concurrent_runs": 3}, format="json")
    assert res.status_code == 200, res.json()
    body = res.json()
    assert (body["monthly_limit"], body["monthly_runs"], body["concurrent_runs"]) == (None, None, 3)
    assert (body["status"]["monthly_runs_limit"], body["status"]["concurrent_runs_limit"]) == (50, 3)
    res = api_client.patch(url, {"monthly_runs": 0}, format="json")
    assert (res.json()["monthly_runs"], res.json()["concurrent_runs"]) == (0, 3)
    listed = api_client.get(DEPARTMENTS, {"search": "経理部"}).json()["results"][0]
    assert (listed["monthly_runs"], listed["concurrent_runs"]) == (0, 3)


def test_my_budgets_show_the_counts(api_client, app_user):
    department = DepartmentFactory.create()
    DepartmentMembershipFactory.create(user=app_user, department=department)
    defaults(department_monthly_runs=30)
    RunFactory.create(budget=Budget.for_department(department), status=RunStatus.QUEUED)
    api_client.force_login(app_user)
    status = api_client.get(MY).json()[0]["status"]
    assert (status["monthly_runs"], status["monthly_runs_limit"]) == (1, 30)
    assert (status["concurrent_runs"], status["concurrent_runs_limit"]) == (1, None)
