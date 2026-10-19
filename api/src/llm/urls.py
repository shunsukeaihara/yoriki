from rest_framework.routers import DefaultRouter

from .views import LLMConnectionViewSet, ModelProfileVersionViewSet, ModelProfileViewSet

router = DefaultRouter()

router.register("v1/llm_connections", LLMConnectionViewSet, basename="llm_connection_viewset")
router.register("v1/model_profiles", ModelProfileViewSet, basename="model_profile_viewset")
router.register(
    "v1/model_profiles/(?P<model_profile>[^/]+)/versions",
    ModelProfileVersionViewSet,
    basename="model_profile_version_viewset",
)

urlpatterns = router.urls
