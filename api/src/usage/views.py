from django.db.models import F
from drf_spectacular.utils import extend_schema
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin, UpdateModelMixin
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet

from task.models import SCOPE_PERMISSIONS, TaskScope
from user.models import Department
from utils.permissions import AppUser, ModelPermissions

from .budget import MonthUsage
from .models import Budget, BudgetKind, BudgetSettings
from .serializers import BudgetSettingsSerializer, DepartmentBudgetSerializer, MyBudgetSerializer, department_budget


@extend_schema(description="予算の既定の上限と、システムの枠の今月の利用額（design.md §9.1）", tags=["manage/budget"])
class BudgetSettingsAPIView(APIView):
    permission_classes = [ModelPermissions]
    permission_model = Budget
    http_method_names = ["get", "patch", "head", "options"]

    # ModelPermissions はViewSetのactionで判定するので、APIViewではメソッドから決める
    @property
    def action(self) -> str:
        return "retrieve" if self.request.method in ("GET", "HEAD", "OPTIONS") else "partial_update"

    @extend_schema(responses=BudgetSettingsSerializer)
    def get(self, request):
        return Response(BudgetSettingsSerializer(BudgetSettings.load()).data)

    @extend_schema(request=BudgetSettingsSerializer, responses=BudgetSettingsSerializer)
    def patch(self, request):
        serializer = BudgetSettingsSerializer(BudgetSettings.load(), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


@extend_schema(description="部門の予算（上限の上書きと今月の利用額。design.md §9.1）", tags=["manage/budget"])
class DepartmentBudgetViewSet(ListModelMixin, RetrieveModelMixin, UpdateModelMixin, GenericViewSet):
    permission_classes = [ModelPermissions]
    permission_model = Budget
    lookup_field = "uid"
    queryset = Department.objects.active().select_related("budget").annotate(monthly_limit=F("budget__monthly_limit"))
    serializer_class = DepartmentBudgetSerializer
    search_fields = ("name",)
    ordering = ("name", "pk")
    http_method_names = ["get", "patch", "head", "options"]


@extend_schema(
    description=(
        "自分が見られる予算の今月の利用額・上限・残額。所属している部門と管理できる部門、全社・システムのタスクを"
        "管理できる人にはシステムの枠（design.md §9.1）"
    ),
    tags=["app/budget"],
)
class MyBudgetsAPIView(APIView):
    permission_classes = [AppUser]

    @extend_schema(responses=MyBudgetSerializer(many=True))
    def get(self, request):
        user = request.user
        usage = MonthUsage()
        joined = set(Department.objects.joined_by(user).values_list("pk", flat=True))
        departments = (Department.objects.joined_by(user) | Department.objects.manageable_by(user)).distinct()
        entries = [
            MyBudgetSerializer.entry(
                BudgetKind.DEPARTMENT, d, usage.status(department_budget(d)), joined=d.pk in joined
            )
            for d in departments.select_related("budget").order_by("name", "pk")
        ]
        system_perms = (SCOPE_PERMISSIONS[TaskScope.COMPANY], SCOPE_PERMISSIONS[TaskScope.SYSTEM])
        if any(user.has_perm(p.perm) for p in system_perms) or not joined:
            # 所属のない人の利用はシステムの枠に入るので、その人にも見せる
            entries.append(MyBudgetSerializer.entry(BudgetKind.SYSTEM, None, usage.status(Budget.system())))
        return Response(MyBudgetSerializer(entries, many=True).data)
