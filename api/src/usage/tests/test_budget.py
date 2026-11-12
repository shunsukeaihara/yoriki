"""月間予算（design.md §9.1、harness.md §10.6）。計上先の決定、残額と予約、Runnerでの予約・まとめ・知らせ"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from conversation.dispatch import CannotStart, post_message
from conversation.factories import ConversationFactory
from conversation.models import ConversationPurpose
from harness import runner as runner_module
from harness.runner import Runner
from harness.tests.fakes import FakeAdapter, calls, reply
from llm.factories import ModelProfileFactory
from llm.providers.base import ToolChoice, Usage
from run.factories import RunFactory
from run.models import ExecutionAttempt, Run, RunEndReason, RunEvent, RunEventKind, RunStatus
from run.tasks import claim_run
from skill.factories import SkillFactory
from skill.models import SkillScope
from task.factories import TaskDefinitionFactory
from task.models import TaskScope
from usage import budget as budgets
from usage.models import Budget, BudgetReservation, BudgetSettings, UsageLedger, UsagePurpose, month_start, usage_cost
from user.factories import DepartmentFactory, DepartmentMembershipFactory

pytestmark = pytest.mark.usefixtures("default_runtime")

# 100万tokenあたり1万ドル：FakeAdapterの1回の応答（入力100・出力20 token）は1.2ドル
PRICE = Decimal(10_000)


@pytest.fixture(name="conversation")
def fixture_conversation(app_user):
    profile = ModelProfileFactory.create(version__input_price=PRICE, version__output_price=PRICE)
    return ConversationFactory.create(owner=app_user, model_profile=profile)


@pytest.fixture(name="system_limit")
def fixture_system_limit(db):
    """所属のない人の会話はシステムの枠に入る。その上限を決める"""

    def set_limit(value: str | None) -> None:
        defaults = BudgetSettings.load()
        defaults.system_monthly_limit = None if value is None else Decimal(value)
        defaults.save()

    return set_limit


def start(conversation, body="請求書を確認して") -> Run:
    post_message(conversation, body=body, client_message_id="", actor=conversation.owner)
    return Run.objects.get(conversation=conversation)


def execute(run: Run, adapter: FakeAdapter) -> Run:
    attempt = claim_run(run.pk)
    assert attempt is not None
    Runner(attempt, adapter_factory=adapter, sleep=lambda _s: None).run()
    run.refresh_from_db()
    return run


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


# --- 計上先 ---


@pytest.mark.django_db
def test_member_budget_follows_the_departments(app_user):
    assert budgets.for_member(app_user, None) == Budget.system()  # 所属がなければシステムの枠

    first = DepartmentMembershipFactory.create(user=app_user).department
    assert budgets.for_member(app_user, None).department == first  # 所属が1つならその部門

    second = DepartmentMembershipFactory.create(user=app_user).department
    with pytest.raises(budgets.BudgetUnavailable, match="選んでください"):
        budgets.for_member(app_user, None)
    assert budgets.for_member(app_user, second).department == second

    with pytest.raises(budgets.BudgetUnavailable, match="所属していません"):
        budgets.for_member(app_user, DepartmentFactory.create())
    second.deleted = True
    second.save()
    with pytest.raises(budgets.BudgetUnavailable, match="所属していません"):
        budgets.for_member(app_user, second)


@pytest.mark.django_db
def test_task_budget_follows_the_scope(app_user):
    department = DepartmentFactory.create()
    assert budgets.for_task(TaskDefinitionFactory.create(scope=TaskScope.DEPARTMENT, department=department)) == (
        Budget.for_department(department)
    )
    assert budgets.for_task(TaskDefinitionFactory.create(scope=TaskScope.COMPANY)) == Budget.system()
    assert budgets.for_task(TaskDefinitionFactory.create(scope=TaskScope.SYSTEM)) == Budget.system()
    joined = DepartmentMembershipFactory.create(user=app_user).department
    personal = TaskDefinitionFactory.create(created_by=app_user)
    assert budgets.for_task(personal) == Budget.for_department(joined)


@pytest.mark.django_db
def test_fix_chat_uses_the_task_budget(app_user):
    department = DepartmentFactory.create()
    task = TaskDefinitionFactory.create(scope=TaskScope.DEPARTMENT, department=department)
    fix_chat = ConversationFactory.create(owner=app_user, purpose=ConversationPurpose.TASK_FIX, task=task)
    assert budgets.for_conversation(fix_chat) == Budget.for_department(department)


@pytest.mark.django_db
def test_skill_evaluation_budget(app_user):
    department = DepartmentFactory.create()
    assert budgets.for_skill(
        SkillFactory.create(scope=SkillScope.DEPARTMENT, department=department), app_user, None
    ) == (Budget.for_department(department))
    assert budgets.for_skill(SkillFactory.create(scope=SkillScope.COMPANY), app_user, None) == Budget.system()
    personal = SkillFactory.create(created_by=app_user)
    assert budgets.for_skill(personal, app_user, None) == Budget.system()


# --- 残額と予約 ---


@pytest.mark.django_db
def test_department_limit_override_and_default():
    defaults = BudgetSettings.load()
    budget = Budget.for_department(DepartmentFactory.create())
    assert budget.limit() is None  # 既定が空なら無制限
    defaults.department_monthly_limit = Decimal(100)
    defaults.save()
    assert budget.limit() == Decimal(100)
    budget.monthly_limit = Decimal(0)
    budget.save()
    assert budget.limit() == Decimal(0)  # 0なら使わせない
    with pytest.raises(budgets.BudgetUnavailable, match="使い切りました"):
        budgets.check(budget)


@pytest.mark.django_db
def test_remaining_counts_this_month_and_live_reservations(system_limit):
    system_limit("10")
    budget = Budget.system()
    spend(budget, "3")
    old = RunFactory.create(budget=budget)
    UsageLedger.objects.create(
        owner=old.owner,
        run=old,
        budget=budget,
        model_version=old.model_version,
        purpose=UsagePurpose.STEP,
        input_tokens=0,
        output_tokens=0,
        cost=Decimal(5),
    )
    UsageLedger.objects.filter(run=old).update(created=month_start() - timedelta(seconds=1))  # 先月の分
    assert budget.remaining() == Decimal(7)

    run = RunFactory.create(budget=budget)
    live = ExecutionAttempt.start(run)
    budgets.reserve(run, live, Decimal(2))
    assert budget.remaining() == Decimal(5)
    # 終わったAttempt・leaseが切れたAttemptの予約は数えない（Workerが落ちて残った予約）
    ExecutionAttempt.objects.filter(pk=live.pk).update(lease_expires=timezone.now() - timedelta(seconds=1))
    assert budget.remaining() == Decimal(7)


@pytest.mark.django_db
def test_reserve_keeps_a_margin(system_limit):
    system_limit("2.5")
    run = RunFactory.create(budget=Budget.system())
    attempt = ExecutionAttempt.start(run)
    reservation = budgets.reserve(run, attempt, Decimal(1), margin=Decimal(1))
    with pytest.raises(budgets.BudgetExhausted):
        budgets.reserve(run, attempt, Decimal(1), margin=Decimal(1))  # 残り1.5は、予約1＋余裕1に足りない
    budgets.reserve(run, attempt, Decimal(1))  # 余裕なし（まとめの1回）なら予約できる
    budgets.settle(reservation)
    assert BudgetReservation.objects.count() == 1


@pytest.mark.django_db
def test_unlimited_budget_is_not_reserved():
    run = RunFactory.create(budget=Budget.system())
    assert budgets.reserve(run, ExecutionAttempt.start(run), Decimal(1_000_000)) is None


def test_usage_cost_counts_cache_writes():
    class Version:
        input_price = Decimal(3)
        cached_input_price = Decimal("0.3")
        cache_write_price: Decimal | None = Decimal("3.75")
        output_price = Decimal(15)

    usage = Usage(1_000_000, 1_000_000, cached_input_tokens=200_000, cache_write_tokens=300_000)
    # 書いた分は書く単価、読んだ分は読む単価、残り50万は入力の単価
    assert usage_cost(Version, usage) == Decimal("1.5") + Decimal("0.06") + Decimal("1.125") + Decimal(15)
    Version.cache_write_price = None  # 空なら入力の単価
    assert usage_cost(Version, usage) == Decimal("1.5") + Decimal("0.06") + Decimal("0.9") + Decimal(15)


# --- 受付とRunner ---


@pytest.mark.django_db
def test_conversation_is_not_accepted_without_budget(conversation, system_limit):
    system_limit("1")
    spend(Budget.system(), "1")
    with pytest.raises(CannotStart, match="システムの枠"):
        start(conversation)


@pytest.mark.django_db
def test_run_is_charged_to_the_fixed_budget(conversation, system_limit):
    # 見積もりは最大出力token（16,000）で数えるので、上限は大きくする
    system_limit("10000")
    run = start(conversation)
    assert run.budget == Budget.system()
    run = execute(run, FakeAdapter([reply("終わりました。")]))
    ledger = UsageLedger.objects.get(run=run)
    assert (ledger.budget, ledger.cost) == (Budget.system(), Decimal("1.2"))
    assert not BudgetReservation.objects.exists()  # 精算した


@pytest.mark.django_db
def test_exhausted_budget_ends_with_a_summary_without_tools(conversation, system_limit, monkeypatch):
    monkeypatch.setattr(runner_module, "estimate_cost", lambda _version, _tokens: Decimal(1))
    system_limit("3")
    run = start(conversation)
    todos = {"todos": [{"content": "読む", "status": "pending"}]}
    adapter = FakeAdapter([calls(("c1", "todo_write", todos)), reply("ここまでの結果です。")])
    run = execute(run, adapter)

    # 1回目：残り3は予約1＋まとめの1以上。2回目：残り1.8では足りないので、まとめの1回だけ
    assert run.status == RunStatus.FAILED
    assert run.end_reason == RunEndReason.BUDGET
    assert "使い切った" in run.error
    assert adapter.requests[-1].tool_choice == ToolChoice.NONE
    assert len(adapter.requests) == 2


@pytest.mark.django_db
def test_budget_without_room_for_a_summary_fails_without_calling(conversation, system_limit, monkeypatch):
    monkeypatch.setattr(runner_module, "estimate_cost", lambda _version, _tokens: Decimal(1))
    system_limit("0.5")
    run = start(conversation)
    adapter = FakeAdapter([reply("呼ばれない")])
    run = execute(run, adapter)
    assert run.end_reason == RunEndReason.BUDGET
    assert adapter.requests == []


@pytest.mark.django_db
def test_low_budget_is_noticed_once(conversation, system_limit, monkeypatch):
    monkeypatch.setattr(runner_module, "estimate_cost", lambda _version, _tokens: Decimal("0.1"))
    system_limit("6")
    spend(Budget.system(), "4")
    run = start(conversation)
    todos = {"todos": [{"content": "読む", "status": "pending"}]}
    adapter = FakeAdapter([calls(("c1", "todo_write", todos)), reply("終わりました。")])
    run = execute(run, adapter)

    assert run.status == RunStatus.COMPLETED
    notices = RunEvent.objects.filter(run=run, kind=RunEventKind.CONTEXT, data__budget_low=True)
    assert notices.count() == 1  # 2回とも2割を切っているが、知らせるのは1回
    assert any("20%" in m.text for m in adapter.requests[1].messages)


@pytest.mark.django_db
def test_detached_reservation_counts_for_an_hour(system_limit):
    """Runが終わった後の呼出し（評価の判定）の予約はAttemptを持たず、精算できずに残っても1時間で数えなくなる"""
    system_limit("10")
    budget = Budget.system()
    run = RunFactory.create(budget=budget)
    reservation = budgets.reserve(run, None, Decimal(4))
    assert budget.remaining() == Decimal(6)
    assert reservation is not None
    BudgetReservation.objects.filter(pk=reservation.pk).update(created=timezone.now() - timedelta(hours=2))
    assert budget.remaining() == Decimal(10)
