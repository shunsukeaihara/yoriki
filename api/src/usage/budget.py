"""予算の計上先の決定と、LLM呼出しごとの予約・精算（design.md §9.1、harness.md §10.6）"""

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
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
    所属がなければ全社の枠"""
    joined = list(Department.objects.joined_by(user))
    if department is not None:
        if department not in joined:
            raise BudgetUnavailable(
                _("予算を計上する部門「%(name)s」に所属していません。計上する部門を選び直してください。")
                % {"name": department.name}
            )
        return Budget.for_department(department)
    if not joined:
        return Budget.company()
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
    return Budget.company()


def for_conversation(conversation) -> Budget:
    if conversation.task_id is not None:
        # 修正チャットの中のRunは、タスクと同じ計上先
        return for_task(conversation.task)
    return for_member(conversation.owner, conversation.budget_department)


def for_skill(skill, user, department: Department | None) -> Budget:
    """Draftの評価の計上先。部門のSkillはその部門、全社・システムのSkillは全社の枠、個人のSkillは依頼した人の部門"""
    from skill.models import SkillScope  # noqa: PLC0415  skillのアプリがusageをimportしうるため

    if skill.scope == SkillScope.PERSONAL:
        return for_member(user, department)
    if skill.scope == SkillScope.DEPARTMENT:
        return Budget.for_department(skill.department)
    return Budget.company()


def check(budget: Budget) -> Budget:
    """受付の確認：今月の残額がなければ断る"""
    remaining = budget.remaining()
    if remaining is not None and remaining <= 0:
        raise BudgetUnavailable(
            _("「%(name)s」の今月の予算を使い切りました。予算の管理者に問い合わせてください。") % {"name": budget}
        )
    return budget


def reserve(run, attempt, amount: Decimal, *, margin: Decimal = Decimal(0)) -> BudgetReservation | None:
    """計上先の行をロックし、残額が予約額＋余裕（まとめの1回分）以上なら予約する。無制限なら予約しない"""
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
    """計上先の今月の利用額・上限・残額（一覧の表示用）"""

    limit: Decimal | None
    spent: Decimal
    remaining: Decimal | None


class MonthUsage:
    """計上先ごとの今月の利用額と有効な予約の合計を、1回ずつの集計で読む（一覧の表示用）"""

    def __init__(self):
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
            return BudgetStatus(limit=limit, spent=Decimal(0), remaining=limit)
        limit = budget.limit(self.defaults)
        spent = self.spent.get(budget.pk, Decimal(0))
        remaining = None if limit is None else limit - spent - self.reserved.get(budget.pk, Decimal(0))
        return BudgetStatus(limit=limit, spent=spent, remaining=remaining)
