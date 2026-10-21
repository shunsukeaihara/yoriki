from rest_framework.routers import DefaultRouter

from .views import AppClarificationViewSet, AppRunEventViewSet, AppRunViewSet

router = DefaultRouter()

router.register("app/v1/runs", AppRunViewSet, basename="app_run_viewset")
router.register("app/v1/runs/(?P<run>[^/]+)/events", AppRunEventViewSet, basename="app_run_event_viewset")
router.register(
    "app/v1/runs/(?P<run>[^/]+)/clarifications", AppClarificationViewSet, basename="app_run_clarification_viewset"
)

urlpatterns = router.urls
