from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import BudgetSettingsAPIView, DepartmentBudgetViewSet, MyBudgetsAPIView

router = DefaultRouter()

router.register("v1/budgets/departments", DepartmentBudgetViewSet, basename="department_budget_viewset")

urlpatterns = [
    path("v1/budgets/settings/", BudgetSettingsAPIView.as_view()),
    path("app/v1/my/budgets/", MyBudgetsAPIView.as_view()),
    *router.urls,
]
