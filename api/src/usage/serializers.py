from decimal import Decimal
from typing import Any

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from user.models import Department
from user.serializers import MinimumDepartmentSerializer

from .budget import BudgetStatus, MonthUsage
from .models import Budget, BudgetKind, BudgetSettings


def limit_field(help_text) -> serializers.DecimalField:
    return serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal(0), allow_null=True, help_text=help_text
    )


def count_field(help_text) -> serializers.IntegerField:
    return serializers.IntegerField(min_value=0, max_value=2147483647, allow_null=True, help_text=help_text)


class BudgetStatusSerializer(serializers.Serializer):
    """今月の利用額・上限・残額（米ドル）と、今月の実行回数・今の同時実行数。上限が空なら無制限"""

    limit = serializers.DecimalField(max_digits=14, decimal_places=2, allow_null=True, read_only=True)
    spent = serializers.DecimalField(max_digits=16, decimal_places=4, read_only=True)
    remaining = serializers.DecimalField(
        max_digits=16,
        decimal_places=4,
        allow_null=True,
        read_only=True,
        help_text=_("上限 −（利用額＋動いているLLM呼出しの予約）。無制限なら空"),
    )
    monthly_runs = serializers.IntegerField(read_only=True, help_text=_("今月に受け付けたRunの数"))
    monthly_runs_limit = serializers.IntegerField(read_only=True, allow_null=True)
    concurrent_runs = serializers.IntegerField(read_only=True, help_text=_("確認待ち以外の、終わっていないRunの数"))
    concurrent_runs_limit = serializers.IntegerField(read_only=True, allow_null=True)


def _status(serializer, budget: Budget | None) -> dict[str, Any]:
    usage: MonthUsage = serializer.context.setdefault("month_usage", MonthUsage())
    return dict(BudgetStatusSerializer(usage.status(budget)).data)


class BudgetSettingsSerializer(serializers.ModelSerializer):
    """既定の上限（design.md §9.1・§9.2）。空なら無制限"""

    department_monthly_limit = limit_field(_("部門の月の上限（米ドル）。部門ごとに上書きできる"))
    system_monthly_limit = limit_field(
        _("システムの枠の月の上限（米ドル）。全社・システムのタスクと、所属のない人の利用")
    )
    department_monthly_runs = count_field(_("部門の月の実行回数の上限。部門ごとに上書きできる"))
    system_monthly_runs = count_field(_("システムの枠の月の実行回数の上限"))
    department_concurrent_runs = count_field(_("部門の同時実行数の上限。部門ごとに上書きできる"))
    system_concurrent_runs = count_field(_("システムの枠の同時実行数の上限"))
    system = serializers.SerializerMethodField(help_text=_("システムの枠の今月の利用額・上限・残額"))

    class Meta:
        model = BudgetSettings
        fields = [
            "department_monthly_limit",
            "system_monthly_limit",
            "department_monthly_runs",
            "system_monthly_runs",
            "department_concurrent_runs",
            "system_concurrent_runs",
            "system",
            "updated",
        ]
        read_only_fields = ["updated"]

    @extend_schema_field(BudgetStatusSerializer)
    def get_system(self, obj: BudgetSettings) -> dict[str, Any]:
        return _status(self, Budget.system())


class DepartmentBudgetSerializer(serializers.Serializer):
    """部門の予算。上限の上書きだけを変えられる。部門は予算の行の上書き（`OVERRIDES`）を注釈して渡す"""

    OVERRIDES = ("monthly_limit", "monthly_runs", "concurrent_runs")

    id = serializers.CharField(source="uid", read_only=True)
    name = serializers.CharField(read_only=True)
    monthly_limit = limit_field(_("上限の上書き（米ドル）。空なら既定に従い、0ならこの部門では使わせない"))
    monthly_runs = count_field(_("月の実行回数の上限の上書き。空なら既定に従い、0ならこの部門では使わせない"))
    concurrent_runs = count_field(_("同時実行数の上限の上書き。空なら既定に従い、0ならこの部門では使わせない"))
    status = serializers.SerializerMethodField(help_text=_("今月の利用額・上限・残額"))

    @extend_schema_field(BudgetStatusSerializer)
    def get_status(self, obj: Department) -> dict[str, Any]:
        return _status(self, department_budget(obj))

    def update(self, instance: Department, validated_data) -> Department:
        budget = Budget.for_department(instance)
        fields = [name for name in self.OVERRIDES if name in validated_data]
        for name in fields:
            setattr(budget, name, validated_data[name])
        budget.save(update_fields=[*fields, "updated"])
        instance.budget = budget
        for name in self.OVERRIDES:
            setattr(instance, name, getattr(budget, name))
        return instance

    def create(self, validated_data):
        raise NotImplementedError("部門の予算は作らない（部門の行を使う）")


def department_budget(department: Department) -> Budget | None:
    try:
        return department.budget
    except Budget.DoesNotExist:
        return None


class MyBudgetSerializer(serializers.Serializer):
    """利用者が見られる予算（所属している部門・管理できる部門・システムの枠）"""

    kind = serializers.ChoiceField(choices=BudgetKind.choices, read_only=True)
    department = MinimumDepartmentSerializer(read_only=True, allow_null=True)
    joined = serializers.BooleanField(read_only=True, help_text=_("所属している部門（会話の計上先に選べる）"))
    status = BudgetStatusSerializer(read_only=True)

    @staticmethod
    def entry(kind: BudgetKind, department: Department | None, status: BudgetStatus, *, joined: bool = False):
        return {"kind": kind, "department": department, "joined": joined, "status": status}
