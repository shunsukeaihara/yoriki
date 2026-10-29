from rest_framework.routers import DefaultRouter

from .views import AppConversationArtifactViewSet, AppRunArtifactViewSet

router = DefaultRouter()

router.register(
    "app/v1/conversations/(?P<conversation>[^/]+)/artifacts",
    AppConversationArtifactViewSet,
    basename="app_conversation_artifact_viewset",
)
router.register("app/v1/runs/(?P<run>[^/]+)/artifacts", AppRunArtifactViewSet, basename="app_run_artifact_viewset")

urlpatterns = router.urls
