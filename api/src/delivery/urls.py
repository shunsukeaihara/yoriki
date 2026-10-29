from rest_framework.routers import DefaultRouter

from .views import AppDestinationViewSet, AppTaskDeliveryViewSet, DestinationRevisionViewSet, DestinationViewSet

router = DefaultRouter()

router.register("app/v1/destinations", AppDestinationViewSet, basename="app_destination_viewset")
router.register("app/v1/tasks/(?P<task>[^/]+)/deliveries", AppTaskDeliveryViewSet, basename="app_task_delivery_viewset")
router.register("v1/destinations", DestinationViewSet, basename="destination_viewset")
router.register(
    "v1/destinations/(?P<destination>[^/]+)/revisions",
    DestinationRevisionViewSet,
    basename="destination_revision_viewset",
)

urlpatterns = router.urls
