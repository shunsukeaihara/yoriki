from rest_framework.routers import DefaultRouter

from .views import AppTaskRevisionViewSet, AppTaskRunViewSet, AppTaskViewSet

router = DefaultRouter()

router.register("app/v1/tasks", AppTaskViewSet, basename="app_task_viewset")
router.register("app/v1/tasks/(?P<task>[^/]+)/revisions", AppTaskRevisionViewSet, basename="app_task_revision_viewset")
router.register("app/v1/tasks/(?P<task>[^/]+)/runs", AppTaskRunViewSet, basename="app_task_run_viewset")

urlpatterns = router.urls
