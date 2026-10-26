from rest_framework.routers import DefaultRouter

from .views import ConnectorVersionViewSet, ConnectorViewSet

router = DefaultRouter()

router.register("v1/connectors", ConnectorViewSet, basename="connector_viewset")
router.register(
    "v1/connectors/(?P<connector>[^/]+)/versions", ConnectorVersionViewSet, basename="connector_version_viewset"
)

urlpatterns = router.urls
