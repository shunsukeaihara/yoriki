from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import AuthProviderViewSet, AuthSessionAPIView, MyUserAPIView

router = DefaultRouter()

router.register("app/v1/auth/providers", AuthProviderViewSet, basename="app_auth_provider_viewset")

urlpatterns = [
    path("app/v1/auth/session/", AuthSessionAPIView.as_view()),
    path("app/v1/my/", MyUserAPIView.as_view()),
    *router.urls,
]
