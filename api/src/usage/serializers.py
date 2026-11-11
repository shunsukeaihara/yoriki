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


class BudgetStatusSerializer(serializers.Serializer):
    """今月の利用額・上限・残額（米ドル）。上限が空なら無制限"""

    limit = serializers.DecimalField(max_digits=14, decimal_places=2, allow_null=True, read_only=True)
    spent = serializers.DecimalField(max_digits=16, decimal_places=4, read_only=True)
    remaining = serializers.DecimalField(
        max_digits=16,
        decimal_places=4,
        allow_null=True,
        read_only=True,
        help_text=_("上限 −（利用額＋動いているLLM呼出しの予約）。無制限なら空"),
    )


def _status(serializer, budget: Budget | None) -> dict[str, Any]:
    usage: MonthUsage = serializer.context.setdefault("month_usage", MonthUsage())
    return dict(BudgetStatusSerializer(usage.status(budget)).data)


class BudgetSettingsSerializer(serializers.ModelSerializer):
    """既定の月の上限（design.md §9.1）。空なら無制限"""

    department_monthly_limit = limit_field(_("部門の月の上限（米ドル）。部門ごとに上書きできる"))
    company_monthly_limit = limit_field(_("全社の枠の月の上限（米ドル）。全社・システムのタスクと、所属のない人の利用"))
    company = serializers.SerializerMethodField(help_text=_("全社の枠の今月の利用額・上限・残額"))

    class Meta:
        model = BudgetSettings
        fields = ["department_monthly_limit", "company_monthly_limit", "company", "updated"]
        read_only_fields = ["updated"]

    @extend_schema_field(BudgetStatusSerializer)
    def get_company(self, obj: BudgetSettings) -> dict[str, Any]:
        return _status(self, Budget.company())


class DepartmentBudgetSerializer(serializers.Serializer):
    """部門の予算。上限の上書きだけを変えられる。部門は `monthly_limit`（予算の行の上書き）を注釈して渡す"""

    id = serializers.CharField(source="uid", read_only=True)
    name = serializers.CharField(read_only=True)
    monthly_limit = limit_field(_("上限の上書き（米ドル）。空なら既定に従い、0ならこの部門では使わせない"))
    status = serializers.SerializerMethodField(help_text=_("今月の利用額・上限・残額"))

    @extend_schema_field(BudgetStatusSerializer)
    def get_status(self, obj: Department) -> dict[str, Any]:
        return _status(self, department_budget(obj))

    def update(self, instance: Department, validated_data) -> Department:
        budget = Budget.for_department(instance)
        budget.monthly_limit = validated_data["monthly_limit"]
        budget.save(update_fields=["monthly_limit", "updated"])
        instance.budget = budget
        instance.monthly_limit = budget.monthly_limit  # type: ignore[attr-defined]
        return instance

    def create(self, validated_data):
        raise NotImplementedError("部門の予算は作らない（部門の行を使う）")


def department_budget(department: Department) -> Budget | None:
    try:
        return department.budget
    except Budget.DoesNotExist:
        return None


class MyBudgetSerializer(serializers.Serializer):
    """利用者が見られる予算（所属している部門・管理できる部門・全社の枠）"""

    kind = serializers.ChoiceField(choices=BudgetKind.choices, read_only=True)
    department = MinimumDepartmentSerializer(read_only=True, allow_null=True)
    joined = serializers.BooleanField(read_only=True, help_text=_("所属している部門（会話の計上先に選べる）"))
    status = BudgetStatusSerializer(read_only=True)

    @staticmethod
    def entry(kind: BudgetKind, department: Department | None, status: BudgetStatus, *, joined: bool = False):
        return {"kind": kind, "department": department, "joined": joined, "status": status}
