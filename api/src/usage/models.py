"""利用量の記録と月間予算（design.md §9）"""

from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from utils.fields import NanoidField

MILLION = Decimal(1_000_000)


class UsagePurpose(models.IntegerChoices):
    STEP = 1, _("エージェントのステップ")
    COMPACTION = 2, _("文脈の圧縮")
    EVALUATION_JUDGE = 3, _("評価の判定")


class BudgetKind(models.IntegerChoices):
    DEPARTMENT = 1, _("部門")
    SYSTEM = 2, _("システムの枠")


def month_start(now: datetime | None = None) -> datetime:
    """今月の始まり。月の区切りは組織のタイムゾーン（`TIME_ZONE`）で決める（design.md §9）"""
    local = timezone.localtime(now)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class BudgetSettings(models.Model):
    """既定の上限（1行だけ）。空なら無制限（design.md §9.1・§9.2）"""

    department_monthly_limit = models.DecimalField(
        _("部門の月の上限（米ドル）"), max_digits=14, decimal_places=2, null=True, blank=True
    )
    system_monthly_limit = models.DecimalField(
        _("システムの枠の月の上限（米ドル）"), max_digits=14, decimal_places=2, null=True, blank=True
    )
    department_monthly_runs = models.PositiveIntegerField(_("部門の月の実行回数の上限"), null=True, blank=True)
    system_monthly_runs = models.PositiveIntegerField(_("システムの枠の月の実行回数の上限"), null=True, blank=True)
    department_concurrent_runs = models.PositiveIntegerField(_("部門の同時実行数の上限"), null=True, blank=True)
    system_concurrent_runs = models.PositiveIntegerField(_("システムの枠の同時実行数の上限"), null=True, blank=True)

    updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("予算の既定")
        verbose_name_plural = verbose_name
        default_permissions = ()

    def __str__(self):
        return str(_("予算の既定"))

    @classmethod
    def load(cls) -> "BudgetSettings":
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj


class Budget(models.Model):
    """予算の計上先（部門かシステムの枠）。Runを受け付けるときに決めてRunに固定し、予約はこの行をロックして行う。
    部門の行は、上限を上書きするか、初めて計上するときに作る"""

    uid = NanoidField(unique=True)
    kind = models.PositiveSmallIntegerField(_("種類"), choices=BudgetKind.choices)
    department = models.OneToOneField(
        "user.Department", on_delete=models.PROTECT, null=True, blank=True, related_name="budget"
    )
    # 部門の上限の上書き。空なら既定に従い、0ならその部門では使わせない。システムの枠では使わない（既定の値だけ）
    monthly_limit = models.DecimalField(_("月の上限（米ドル）"), max_digits=14, decimal_places=2, null=True, blank=True)
    # 月の実行回数・同時実行数の上限の上書き（design.md §9.2）。空・0の意味は金額と同じ
    monthly_runs = models.PositiveIntegerField(_("月の実行回数の上限"), null=True, blank=True)
    concurrent_runs = models.PositiveIntegerField(_("同時実行数の上限"), null=True, blank=True)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("予算")
        verbose_name_plural = verbose_name
        default_permissions = ("view", "change")
        constraints = [
            models.CheckConstraint(
                condition=Q(kind=BudgetKind.DEPARTMENT, department__isnull=False)
                | Q(
                    kind=BudgetKind.SYSTEM,
                    department__isnull=True,
                    monthly_limit__isnull=True,
                    monthly_runs__isnull=True,
                    concurrent_runs__isnull=True,
                ),
                name="budget_kind_department",
            ),
            models.UniqueConstraint(fields=["kind"], condition=Q(kind=BudgetKind.SYSTEM), name="uniq_system_budget"),
        ]

    def __str__(self):
        return str(self.department) if self.department_id else str(_("システムの枠"))

    @classmethod
    def system(cls) -> "Budget":
        obj, _created = cls.objects.get_or_create(kind=BudgetKind.SYSTEM)
        return obj

    @classmethod
    def for_department(cls, department) -> "Budget":
        obj, _created = cls.objects.get_or_create(kind=BudgetKind.DEPARTMENT, department=department)
        return obj

    def limit(self, defaults: BudgetSettings | None = None) -> Decimal | None:
        defaults = defaults or BudgetSettings.load()
        if self.kind == BudgetKind.SYSTEM:
            return defaults.system_monthly_limit
        return self.monthly_limit if self.monthly_limit is not None else defaults.department_monthly_limit

    def monthly_runs_limit(self, defaults: BudgetSettings | None = None) -> int | None:
        defaults = defaults or BudgetSettings.load()
        if self.kind == BudgetKind.SYSTEM:
            return defaults.system_monthly_runs
        return self.monthly_runs if self.monthly_runs is not None else defaults.department_monthly_runs

    def concurrent_runs_limit(self, defaults: BudgetSettings | None = None) -> int | None:
        defaults = defaults or BudgetSettings.load()
        if self.kind == BudgetKind.SYSTEM:
            return defaults.system_concurrent_runs
        return self.concurrent_runs if self.concurrent_runs is not None else defaults.department_concurrent_runs

    def spent(self, since: datetime | None = None) -> Decimal:
        """今月の利用額"""
        total = UsageLedger.objects.filter(budget=self, created__gte=since or month_start()).aggregate(t=Sum("cost"))
        return total["t"] or Decimal(0)

    def reserved(self) -> Decimal:
        """有効な予約の合計。終わったAttempt・leaseが切れたAttemptの予約は数えない（Workerが落ちて残ったもの）"""
        total = BudgetReservation.objects.active().filter(budget=self).aggregate(t=Sum("amount"))
        return total["t"] or Decimal(0)

    def remaining(self, defaults: BudgetSettings | None = None) -> Decimal | None:
        """残額（上限 −（今月の利用額＋有効な予約））。無制限ならNone"""
        limit = self.limit(defaults)
        if limit is None:
            return None
        return limit - self.spent() - self.reserved()


# Attemptを持たない予約（Runが終わった後に呼ぶ評価の判定）を数える時間。呼出しの接続のtimeout（3600秒まで）に合わせる
DETACHED_RESERVATION_TTL = timedelta(hours=1)


class BudgetReservationQuerySet(models.QuerySet):
    def active(self) -> "BudgetReservationQuerySet":
        now = timezone.now()
        return self.filter(
            Q(attempt__ended__isnull=True, attempt__lease_expires__gt=now)
            | Q(attempt__isnull=True, created__gt=now - DETACHED_RESERVATION_TTL)
        )


class BudgetReservation(models.Model):
    """LLM呼出し1回分の予約。応答の利用量を記録したら消す（精算。harness.md §10.6）"""

    budget = models.ForeignKey(Budget, on_delete=models.CASCADE, related_name="reservations")
    run = models.ForeignKey("run.Run", on_delete=models.CASCADE, related_name="+")
    # 空なら、Runが終わった後の呼出し（評価の判定）。DETACHED_RESERVATION_TTL の間だけ数える
    attempt = models.ForeignKey(
        "run.ExecutionAttempt", on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    amount = models.DecimalField(_("予約額（米ドル）"), max_digits=14, decimal_places=8)

    created = models.DateTimeField(auto_now_add=True)

    objects = BudgetReservationQuerySet.as_manager()

    class Meta:
        verbose_name = _("予算の予約")
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.budget}:{self.amount}"


def usage_cost(model_version, usage) -> Decimal:
    """利用量の金額。入力tokenにはcacheから読んだ分・cacheへ書いた分も含まれる"""
    write_price = model_version.cache_write_price
    if write_price is None:
        write_price = model_version.input_price
    plain = max(usage.input_tokens - usage.cached_input_tokens - usage.cache_write_tokens, 0)
    return (
        plain * model_version.input_price
        + usage.cached_input_tokens * model_version.cached_input_price
        + usage.cache_write_tokens * write_price
        + usage.output_tokens * model_version.output_price
    ) / MILLION


def estimate_cost(model_version, input_tokens: int) -> Decimal:
    """LLM呼出し1回の見積もり。入力は高いほうの単価（cacheへ書く分）で、出力は最大出力tokenで数える"""
    write_price = model_version.cache_write_price
    if write_price is None:
        write_price = model_version.input_price
    return (
        input_tokens * max(model_version.input_price, write_price)
        + model_version.max_output_tokens * model_version.output_price
    ) / MILLION


class UsageLedger(models.Model):
    """1回のLLM呼出しの利用量と金額。作成後に変更しない。金額は実際に使ったモデル設定の版の単価で計算する"""

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    run = models.ForeignKey("run.Run", on_delete=models.PROTECT, related_name="usages")
    # 予算の計上先（Runに固定したもの。design.md §9.1）
    budget = models.ForeignKey(Budget, on_delete=models.PROTECT, related_name="usages")
    # サブエージェントの呼出しなら、その子（harness.md §10.11）。合計は親のRun・agentセッションで見る
    subagent = models.ForeignKey("run.Subagent", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    model_version = models.ForeignKey("llm.ModelProfileVersion", on_delete=models.PROTECT, related_name="+")
    purpose = models.PositiveSmallIntegerField(_("用途"), choices=UsagePurpose.choices)
    input_tokens = models.PositiveIntegerField(_("入力token"))
    cached_input_tokens = models.PositiveIntegerField(_("cacheから読んだ入力token"), default=0)
    cache_write_tokens = models.PositiveIntegerField(_("cacheへ書いた入力token"), default=0)
    output_tokens = models.PositiveIntegerField(_("出力token"))
    reasoning_tokens = models.PositiveIntegerField(_("推論のtoken"), default=0)  # 出力tokenに含まれる
    cost = models.DecimalField(_("金額（米ドル）"), max_digits=14, decimal_places=8)
    request_id = models.CharField(_("プロバイダの要求ID"), max_length=200, blank=True)

    created = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _("利用量")
        verbose_name_plural = verbose_name
        indexes = [models.Index(fields=["budget", "created"])]

    def __str__(self):
        return f"{self.run_id}:{self.cost}"

    @classmethod
    def record(
        cls, *, run, model_version, purpose: UsagePurpose, usage, request_id: str = "", subagent=None
    ) -> "UsageLedger":
        return cls.objects.create(
            owner_id=run.owner_id,
            run=run,
            budget_id=run.budget_id,
            subagent=subagent,
            model_version=model_version,
            purpose=purpose,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            output_tokens=usage.output_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            cost=usage_cost(model_version, usage),
            request_id=request_id,
        )
