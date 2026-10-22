from rest_framework.routers import DefaultRouter

from .views import RuntimeProfileVersionViewSet, RuntimeProfileViewSet

router = DefaultRouter()

router.register("v1/runtime_profiles", RuntimeProfileViewSet, basename="runtime_profile_viewset")
router.register(
    "v1/runtime_profiles/(?P<runtime_profile>[^/]+)/versions",
    RuntimeProfileVersionViewSet,
    basename="runtime_profile_version_viewset",
)

urlpatterns = router.urls
