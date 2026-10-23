from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import AppConversationArtifactViewSet, FileAPIView

router = DefaultRouter()

router.register(
    "app/v1/conversations/(?P<conversation>[^/]+)/artifacts",
    AppConversationArtifactViewSet,
    basename="app_conversation_artifact_viewset",
)

urlpatterns = [
    path("files/<str:token>/", FileAPIView.as_view()),
    *router.urls,
]
