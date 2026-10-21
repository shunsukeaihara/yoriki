from rest_framework.routers import DefaultRouter

from .views import AppModelProfileViewSet, LLMConnectionViewSet, ModelProfileVersionViewSet, ModelProfileViewSet

router = DefaultRouter()

router.register("v1/llm_connections", LLMConnectionViewSet, basename="llm_connection_viewset")
router.register("v1/model_profiles", ModelProfileViewSet, basename="model_profile_viewset")
router.register(
    "v1/model_profiles/(?P<model_profile>[^/]+)/versions",
    ModelProfileVersionViewSet,
    basename="model_profile_version_viewset",
)

router.register("app/v1/model_profiles", AppModelProfileViewSet, basename="app_model_profile_viewset")

urlpatterns = router.urls
