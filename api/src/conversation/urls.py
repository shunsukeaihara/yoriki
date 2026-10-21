from rest_framework.routers import DefaultRouter

from .views import AppConversationMessageViewSet, AppConversationViewSet

router = DefaultRouter()

router.register("app/v1/conversations", AppConversationViewSet, basename="app_conversation_viewset")
router.register(
    "app/v1/conversations/(?P<conversation>[^/]+)/messages",
    AppConversationMessageViewSet,
    basename="app_conversation_message_viewset",
)

urlpatterns = router.urls
