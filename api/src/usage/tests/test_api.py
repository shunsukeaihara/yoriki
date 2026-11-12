"""予算のAPI（design.md §9.1、authorization.md §7.3・§10）。管理画面の既定・部門の上書き、利用者の一覧、
会話・個人タスクの計上する部門"""

from decimal import Decimal

import pytest

from conversation.factories import ConversationFactory
from conversation.models import ConversationPurpose
from llm.factories import ModelProfileFactory
from run.factories import RunFactory
from task.factories import TaskDefinitionFactory
from task.models import TaskScope
from usage.models import Budget, BudgetSettings, UsageLedger, UsagePurpose
from user.factories import DepartmentFactory, DepartmentMembershipFactory, grant
from utils.permissions import AppPermission

pytestmark = pytest.mark.django_db

SETTINGS = "/api/v1/budgets/settings/"
DEPARTMENTS = "/api/v1/budgets/departments/"
MY = "/api/app/v1/my/budgets/"
CONVERSATIONS = "/api/app/v1/conversations/"
TASKS = "/api/app/v1/tasks/"


def spend(budget: Budget, amount: str) -> None:
    run = RunFactory.create(budget=budget)
    UsageLedger.objects.create(
        owner=run.owner,
        run=run,
        budget=budget,
        model_version=run.model_version,
        purpose=UsagePurpose.STEP,
        input_tokens=0,
        output_tokens=0,
        cost=Decimal(amount),
    )


# --- 管理画面 ---


def money(status: dict) -> tuple:
    return status["limit"], status["spent"], status["remaining"]


def test_settings_need_the_budget_permissions(api_client, app_user):
    api_client.force_login(app_user)
    assert api_client.get(SETTINGS).status_code == 403

    viewer = grant(app_user, "usage.view_budget")
    api_client.force_login(viewer)
    res = api_client.get(SETTINGS)
    assert res.status_code == 200
    assert res.json()["department_monthly_limit"] is None
    assert api_client.patch(SETTINGS, {"system_monthly_limit": "50"}, format="json").status_code == 403

    manager = grant(viewer, "usage.change_budget")
    api_client.force_login(manager)
    spend(Budget.system(), "12.5")
    res = api_client.patch(SETTINGS, {"system_monthly_limit": "50", "department_monthly_limit": "20"}, format="json")
    assert res.status_code == 200, res.json()
    assert money(res.json()["system"]) == ("50.00", "12.5000", "37.5000")
    assert BudgetSettings.load().department_monthly_limit == Decimal(20)
    assert api_client.patch(SETTINGS, {"system_monthly_limit": "-1"}, format="json").status_code == 400


def test_department_override(api_client, app_user):
    manager = grant(app_user, "usage.view_budget", "usage.change_budget")
    api_client.force_login(manager)
    sales = DepartmentFactory.create(name="営業")
    DepartmentFactory.create(name="経理")
    defaults = BudgetSettings.load()
    defaults.department_monthly_limit = Decimal(100)
    defaults.save()
    spend(Budget.for_department(sales), "30")

    rows = {r["name"]: r for r in api_client.get(DEPARTMENTS).json()["results"]}
    assert rows["営業"]["monthly_limit"] is None
    assert money(rows["営業"]["status"]) == ("100.00", "30.0000", "70.0000")
    assert money(rows["経理"]["status"]) == ("100.00", "0.0000", "100.0000")

    res = api_client.patch(f"{DEPARTMENTS}{sales.uid}/", {"monthly_limit": "0"}, format="json")
    assert res.status_code == 200, res.json()
    assert res.json()["monthly_limit"] == "0.00"
    assert res.json()["status"]["remaining"] == "-30.0000"
    res = api_client.patch(f"{DEPARTMENTS}{sales.uid}/", {"monthly_limit": None}, format="json")
    assert res.json()["status"]["limit"] == "100.00"  # 空なら既定に戻る


# --- 利用者の一覧 ---


def test_my_budgets_show_joined_and_manageable_departments(api_client, app_user):
    joined = DepartmentMembershipFactory.create(
        user=app_user, department=DepartmentFactory.create(name="営業")
    ).department
    DepartmentFactory.create(name="経理")
    api_client.force_login(app_user)
    res = api_client.get(MY).json()
    assert [(r["department"]["name"], r["joined"]) for r in res] == [("営業", True)]  # システムの枠は出さない
    assert res[0]["status"]["limit"] is None

    admin = grant(app_user, AppPermission.MANAGE_ALL_DEPARTMENTS, AppPermission.MANAGE_COMPANY_TASKS)
    api_client.force_login(admin)
    res = api_client.get(MY).json()
    assert [(r["kind"], r["department"] and r["department"]["name"], r["joined"]) for r in res] == [
        (1, "営業", True),
        (1, "経理", False),
        (2, None, False),
    ]
    assert joined.pk


def test_user_without_departments_sees_the_system_budget(api_client, app_user):
    api_client.force_login(app_user)
    assert [r["kind"] for r in api_client.get(MY).json()] == [2]


# --- 会話・個人タスクの計上する部門 ---


def test_conversation_budget_department(api_client, app_user):
    sales = DepartmentMembershipFactory.create(user=app_user).department
    ModelProfileFactory.create(is_default=True)
    api_client.force_login(app_user)
    res = api_client.post(CONVERSATIONS, {}, format="json")
    assert res.status_code == 201, res.json()
    assert res.json()["budget_department"] == sales.uid  # 所属が1つならその部門

    url = f"{CONVERSATIONS}{res.json()['id']}/"
    other = DepartmentFactory.create()
    res = api_client.patch(url, {"budget_department": other.uid}, format="json")
    assert res.status_code == 400  # 所属していない部門
    second = DepartmentMembershipFactory.create(user=app_user).department
    res = api_client.patch(url, {"budget_department": second.uid}, format="json")
    assert res.json()["budget_department_info"]["name"] == second.name


def test_fix_chat_budget_department_cannot_be_set(api_client, app_user):
    sales = DepartmentMembershipFactory.create(user=app_user).department
    task = TaskDefinitionFactory.create(created_by=app_user)
    fix_chat = ConversationFactory.create(owner=app_user, purpose=ConversationPurpose.TASK_FIX, task=task)
    api_client.force_login(app_user)
    res = api_client.patch(f"{CONVERSATIONS}{fix_chat.uid}/", {"budget_department": sales.uid}, format="json")
    assert res.status_code == 400


def test_personal_task_budget_department(api_client, app_user):
    sales = DepartmentMembershipFactory.create(user=app_user).department
    second = DepartmentMembershipFactory.create(user=app_user).department
    model = ModelProfileFactory.create()
    api_client.force_login(app_user)
    body = {"scope": TaskScope.PERSONAL, "name": "請求書", "prompt_template": "確認して", "model_profile": model.uid}
    res = api_client.post(TASKS, body, format="json")
    assert res.status_code == 201, res.json()
    assert res.json()["budget_department"] is None  # 所属が2つなら決めない（実行の前に選ぶ）

    url = f"{TASKS}{res.json()['id']}/"
    res = api_client.patch(url, {"version": 1, "budget_department": second.uid}, format="json")
    assert res.status_code == 200, res.json()
    assert res.json()["budget_department_info"]["name"] == second.name
    assert res.json()["version"] == 1  # 版の外
    assert sales.pk


def test_department_task_has_no_budget_department(api_client, app_user):
    department = DepartmentMembershipFactory.create(user=app_user).department
    manager = grant(app_user, AppPermission.MANAGE_DEPARTMENT_TASKS)
    api_client.force_login(manager)
    body = {
        "scope": TaskScope.DEPARTMENT,
        "department": department.uid,
        "name": "請求書",
        "prompt_template": "確認して",
        "model_profile": ModelProfileFactory.create().uid,
        "budget_department": department.uid,
    }
    assert api_client.post(TASKS, body, format="json").status_code == 400
