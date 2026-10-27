from rest_framework.routers import DefaultRouter

from .views import (
    AppTaskRevisionViewSet,
    AppTaskRunViewSet,
    AppTaskViewSet,
    HookTaskRunViewSet,
    HookTaskViewSet,
    TriggerBindingRevisionViewSet,
    TriggerBindingViewSet,
    TriggerReceiptViewSet,
)

router = DefaultRouter()

router.register("app/v1/tasks", AppTaskViewSet, basename="app_task_viewset")
router.register("app/v1/tasks/(?P<task>[^/]+)/revisions", AppTaskRevisionViewSet, basename="app_task_revision_viewset")
router.register("app/v1/tasks/(?P<task>[^/]+)/runs", AppTaskRunViewSet, basename="app_task_run_viewset")
router.register("v1/trigger_bindings", TriggerBindingViewSet, basename="trigger_binding_viewset")
router.register(
    "v1/trigger_bindings/(?P<binding>[^/]+)/revisions",
    TriggerBindingRevisionViewSet,
    basename="trigger_binding_revision_viewset",
)
router.register(
    "v1/trigger_bindings/(?P<binding>[^/]+)/receipts", TriggerReceiptViewSet, basename="trigger_receipt_viewset"
)
# Webhookの受け口は `POST hooks/v1/tasks/{task}/`（タスクごとの一覧のURLに create だけを置く）
router.register("hooks/v1/tasks/(?P<task>[^/]+)", HookTaskViewSet, basename="hooks_task_viewset")
router.register("hooks/v1/tasks/(?P<task>[^/]+)/runs", HookTaskRunViewSet, basename="hooks_task_run_viewset")

urlpatterns = router.urls
