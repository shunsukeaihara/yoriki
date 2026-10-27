from rest_framework.routers import DefaultRouter

from .views import AppConnectorViewSet, ConnectorVersionViewSet, ConnectorViewSet, DatabaseConnectionViewSet

router = DefaultRouter()

router.register("v1/database_connections", DatabaseConnectionViewSet, basename="database_connection_viewset")
router.register("v1/connectors", ConnectorViewSet, basename="connector_viewset")
router.register(
    "v1/connectors/(?P<connector>[^/]+)/versions", ConnectorVersionViewSet, basename="connector_version_viewset"
)

router.register("app/v1/connectors", AppConnectorViewSet, basename="app_connector_viewset")

urlpatterns = router.urls
