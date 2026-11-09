from rest_framework.routers import DefaultRouter

from .views import (
    AppClarificationViewSet,
    AppRunEventViewSet,
    AppRunViewSet,
    AppScriptCallViewSet,
    AppSubagentViewSet,
)

router = DefaultRouter()

router.register("app/v1/runs", AppRunViewSet, basename="app_run_viewset")
router.register("app/v1/runs/(?P<run>[^/]+)/events", AppRunEventViewSet, basename="app_run_event_viewset")
router.register("app/v1/runs/(?P<run>[^/]+)/script_calls", AppScriptCallViewSet, basename="app_run_script_call_viewset")
router.register("app/v1/runs/(?P<run>[^/]+)/subagents", AppSubagentViewSet, basename="app_run_subagent_viewset")
router.register(
    "app/v1/runs/(?P<run>[^/]+)/clarifications", AppClarificationViewSet, basename="app_run_clarification_viewset"
)

urlpatterns = router.urls
