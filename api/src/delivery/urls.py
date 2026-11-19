from django.urls import path
from rest_framework.routers import DefaultRouter

from .channels import inbound
from .views import (
    AppDestinationViewSet,
    AppNotificationViewSet,
    AppTaskDeliveryViewSet,
    ChannelConnectionViewSet,
    DestinationRevisionViewSet,
    DestinationViewSet,
    FileAPIView,
)

router = DefaultRouter()

router.register("app/v1/destinations", AppDestinationViewSet, basename="app_destination_viewset")
router.register("app/v1/notifications", AppNotificationViewSet, basename="app_notification_viewset")
router.register("app/v1/tasks/(?P<task>[^/]+)/deliveries", AppTaskDeliveryViewSet, basename="app_task_delivery_viewset")
router.register("v1/channel_connections", ChannelConnectionViewSet, basename="channel_connection_viewset")
router.register("v1/destinations", DestinationViewSet, basename="destination_viewset")
router.register(
    "v1/destinations/(?P<destination>[^/]+)/revisions",
    DestinationRevisionViewSet,
    basename="destination_revision_viewset",
)

urlpatterns = [
    path("files/<str:token>/", FileAPIView.as_view()),
    # 対話の出口（Slack等）からのイベント・ボタンの受け口（design.md §4.6）
    path("hooks/channels/<str:provider>/<str:connection>/", inbound.receive),
    *router.urls,
]
