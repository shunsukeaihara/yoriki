"""予算の計上先の決定と、LLM呼出しごとの予約・精算（design.md §9.1、harness.md §10.6）"""

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Sum
from django.utils.translation import gettext as _

from user.models import Department

from .models import Budget, BudgetReservation, BudgetSettings, UsageLedger, month_start

# 残りがこの割合を切ったらモデルに知らせる（harness.md §10.6）
LOW_RATIO = Decimal("0.2")


class BudgetUnavailable(Exception):
    """計上先を決められないか、残額がない。受付で断る理由として利用者に見せる"""


class BudgetExhausted(Exception):
    """LLM呼出しの予約ができない"""


def for_member(user, department: Department | None) -> Budget:
    """会話・個人タスクの計上先。選んだ部門に今も所属していればその部門、選んでいなければ所属が1つならその部門、
    所属がなければシステムの枠"""
    joined = list(Department.objects.joined_by(user))
    if department is not None:
        if department not in joined:
            raise BudgetUnavailable(
                _("予算を計上する部門「%(name)s」に所属していません。計上する部門を選び直してください。")
                % {"name": department.name}
            )
        return Budget.for_department(department)
    if not joined:
        return Budget.system()
    if len(joined) == 1:
        return Budget.for_department(joined[0])
    raise BudgetUnavailable(_("予算を計上する部門を選んでください。"))


def default_department(user) -> Department | None:
    """所属が1つなら、その部門（会話・個人タスクを作るときに計上する部門として入れる）"""
    joined = list(Department.objects.joined_by(user)[:2])
    return joined[0] if len(joined) == 1 else None


def for_task(task) -> Budget:
    from task.models import TaskScope  # noqa: PLC0415  taskがusageをimportするため

    if task.scope == TaskScope.PERSONAL:
        return for_member(task.owner, task.budget_department)
    if task.scope == TaskScope.DEPARTMENT:
        return Budget.for_department(task.department)
    return Budget.system()


def for_conversation(conversation) -> Budget:
    if conversation.task_id is not None:
        # 修正チャットの中のRunは、タスクと同じ計上先
        return for_task(conversation.task)
    return for_member(conversation.owner, conversation.budget_department)


def for_skill(skill, user, department: Department | None) -> Budget:
    """Draftの評価の計上先。部門のSkillはその部門、全社・システムのSkillはシステムの枠、個人のSkillは依頼した人の部門"""
    from skill.models import SkillScope  # noqa: PLC0415  skillのアプリがusageをimportしうるため

    if skill.scope == SkillScope.PERSONAL:
        return for_member(user, department)
    if skill.scope == SkillScope.DEPARTMENT:
        return Budget.for_department(skill.department)
    return Budget.system()


def counted_statuses() -> list[int]:
    """同時実行数に数えるRunの状態。確認待ちは数えない（design.md §9.2）"""
    from run.models import RunStatus  # noqa: PLC0415  runがusageをimportするため

    return [
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.RETRY_WAIT,
        RunStatus.CANCELLING,
        RunStatus.WAITING_BACKGROUND,
    ]


def check(budget: Budget, *, runs: int = 1, lock: bool = True) -> Budget:
    """受付の確認：今月の残額がないか、`runs` 個のRunを受け付けると月の実行回数・同時実行数の上限を超えるなら断る。

    Runを作るtransactionの中で呼ぶ。実行回数・同時実行数の上限がある計上先は、行をロックしてから数えるので、
    同時に受け付けても上限を超えない（design.md §9.2）。`lock` が偽なら読むだけ（受付と同じ判定を見せるとき）"""
    from run.models import Run  # noqa: PLC0415  runがusageをimportするため

    defaults = BudgetSettings.load()
    remaining = budget.remaining(defaults)
    if remaining is not None and remaining <= 0:
        raise BudgetUnavailable(
            _("「%(name)s」の今月の予算を使い切りました。予算の管理者に問い合わせてください。") % {"name": budget}
        )
    monthly = budget.monthly_runs_limit(defaults)
    concurrent = budget.concurrent_runs_limit(defaults)
    if monthly is None and concurrent is None:
        return budget
    if lock:
        Budget.objects.select_for_update().get(pk=budget.pk)
    if monthly is not None:
        count = Run.objects.filter(budget=budget, created__gte=month_start()).count()
        if count + runs > monthly:
            raise BudgetUnavailable(
                _("「%(name)s」の今月の実行回数の上限（%(limit)s回）に達しました。予算の管理者に問い合わせてください。")
                % {"name": budget, "limit": monthly}
            )
    if concurrent is not None:
        count = Run.objects.filter(budget=budget, status__in=counted_statuses()).count()
        if count + runs > concurrent:
            raise BudgetUnavailable(
                _(
                    "「%(name)s」で同時に動かせる数の上限（%(limit)s件）に達しています。"
                    "動いている作業が終わってから、もう一度試してください。"
                )
                % {"name": budget, "limit": concurrent}
            )
    return budget


def reserve(run, attempt, amount: Decimal, *, margin: Decimal = Decimal(0)) -> BudgetReservation | None:
    """計上先の行をロックし、残額が予約額＋余裕（まとめの1回分）以上なら予約する。無制限なら予約しない。
    `attempt` がNoneなら、Runが終わった後の呼出し（評価の判定）の予約"""
    with transaction.atomic():
        budget = Budget.objects.select_for_update().get(pk=run.budget_id)
        remaining = budget.remaining()
        if remaining is None:
            return None
        if remaining < amount + margin:
            raise BudgetExhausted()
        return BudgetReservation.objects.create(budget=budget, run=run, attempt=attempt, amount=amount)


def settle(reservation: BudgetReservation | None) -> None:
    """応答の利用量を記録したら予約を消す"""
    if reservation is not None:
        BudgetReservation.objects.filter(pk=reservation.pk).delete()


def is_low(budget: Budget) -> bool:
    """残りが上限の2割を切った"""
    defaults = BudgetSettings.load()
    limit = budget.limit(defaults)
    if not limit:
        return False
    remaining = budget.remaining(defaults)
    return remaining is not None and remaining < limit * LOW_RATIO


@dataclass(frozen=True)
class BudgetStatus:
    """計上先の今月の利用額・上限・残額と、今月の実行回数・今の同時実行数とその上限（一覧の表示用）"""

    limit: Decimal | None
    spent: Decimal
    remaining: Decimal | None
    monthly_runs: int = 0
    monthly_runs_limit: int | None = None
    concurrent_runs: int = 0
    concurrent_runs_limit: int | None = None


class MonthUsage:
    """計上先ごとの今月の利用額・有効な予約の合計・実行回数・同時実行数を、1回ずつの集計で読む（一覧の表示用）"""

    def __init__(self):
        from run.models import Run  # noqa: PLC0415  runがusageをimportするため

        self.monthly_runs = {
            row["budget"]: row["n"]
            for row in Run.objects.filter(created__gte=month_start()).values("budget").annotate(n=Count("pk"))
        }
        self.concurrent_runs = {
            row["budget"]: row["n"]
            for row in Run.objects.filter(status__in=counted_statuses()).values("budget").annotate(n=Count("pk"))
        }
        self.defaults = BudgetSettings.load()
        self.spent = {
            row["budget"]: row["t"]
            for row in UsageLedger.objects.filter(created__gte=month_start()).values("budget").annotate(t=Sum("cost"))
        }
        self.reserved = {
            row["budget"]: row["t"]
            for row in BudgetReservation.objects.active().values("budget").annotate(t=Sum("amount"))
        }

    def status(self, budget: Budget | None) -> BudgetStatus:
        """`budget` がNoneなら、まだ行のない部門（計上したことも、上限を上書きしたこともない）"""
        if budget is None:
            limit = self.defaults.department_monthly_limit
            return BudgetStatus(
                limit=limit,
                spent=Decimal(0),
                remaining=limit,
                monthly_runs_limit=self.defaults.department_monthly_runs,
                concurrent_runs_limit=self.defaults.department_concurrent_runs,
            )
        limit = budget.limit(self.defaults)
        spent = self.spent.get(budget.pk, Decimal(0))
        remaining = None if limit is None else limit - spent - self.reserved.get(budget.pk, Decimal(0))
        return BudgetStatus(
            limit=limit,
            spent=spent,
            remaining=remaining,
            monthly_runs=self.monthly_runs.get(budget.pk, 0),
            monthly_runs_limit=budget.monthly_runs_limit(self.defaults),
            concurrent_runs=self.concurrent_runs.get(budget.pk, 0),
            concurrent_runs_limit=budget.concurrent_runs_limit(self.defaults),
        )
