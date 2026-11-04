from rest_framework.routers import DefaultRouter

from .views import (
    AppLaunchableTaskViewSet,
    AppTaskRevisionViewSet,
    AppTaskRunMessageViewSet,
    AppTaskRunViewSet,
    AppTaskTriggerReceiptViewSet,
    AppTaskTriggerRevisionViewSet,
    AppTaskTriggerViewSet,
    AppTaskViewSet,
    HookTaskRunViewSet,
    HookTaskViewSet,
    TriggerBindingViewSet,
)

router = DefaultRouter()

router.register("app/v1/tasks", AppTaskViewSet, basename="app_task_viewset")
router.register("app/v1/launchable_tasks", AppLaunchableTaskViewSet, basename="app_launchable_task_viewset")
router.register("app/v1/tasks/(?P<task>[^/]+)/revisions", AppTaskRevisionViewSet, basename="app_task_revision_viewset")
router.register("app/v1/tasks/(?P<task>[^/]+)/runs", AppTaskRunViewSet, basename="app_task_run_viewset")
router.register(
    "app/v1/tasks/(?P<task>[^/]+)/runs/(?P<run>[^/]+)/messages",
    AppTaskRunMessageViewSet,
    basename="app_task_run_message_viewset",
)
router.register("app/v1/tasks/(?P<task>[^/]+)/triggers", AppTaskTriggerViewSet, basename="app_task_trigger_viewset")
router.register(
    "app/v1/tasks/(?P<task>[^/]+)/triggers/(?P<binding>[^/]+)/revisions",
    AppTaskTriggerRevisionViewSet,
    basename="app_task_trigger_revision_viewset",
)
router.register(
    "app/v1/tasks/(?P<task>[^/]+)/triggers/(?P<binding>[^/]+)/receipts",
    AppTaskTriggerReceiptViewSet,
    basename="app_task_trigger_receipt_viewset",
)
router.register("v1/trigger_bindings", TriggerBindingViewSet, basename="trigger_binding_viewset")
# Webhookの受け口は `POST hooks/v1/tasks/{task}/`（タスクごとの一覧のURLに create だけを置く）
router.register("hooks/v1/tasks/(?P<task>[^/]+)", HookTaskViewSet, basename="hooks_task_viewset")
router.register("hooks/v1/tasks/(?P<task>[^/]+)/runs", HookTaskRunViewSet, basename="hooks_task_run_viewset")

urlpatterns = router.urls
