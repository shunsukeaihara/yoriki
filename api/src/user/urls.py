from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    AuthInvitationViewSet,
    AuthMethodsAPIView,
    AuthSessionAPIView,
    DepartmentMembershipViewSet,
    DepartmentViewSet,
    GroupViewSet,
    ManageUserViewSet,
    MyUserAPIView,
    SSOProviderViewSet,
)

router = DefaultRouter()

router.register("app/v1/auth/invitations", AuthInvitationViewSet, basename="app_auth_invitation_viewset")
router.register("v1/users", ManageUserViewSet, basename="manage_user_viewset")
router.register("v1/groups", GroupViewSet, basename="group_viewset")
router.register("v1/sso_providers", SSOProviderViewSet, basename="sso_provider_viewset")
router.register("v1/departments", DepartmentViewSet, basename="department_viewset")
router.register(
    "v1/departments/(?P<department>[^/]+)/members",
    DepartmentMembershipViewSet,
    basename="department_member_viewset",
)

urlpatterns = [
    path("app/v1/auth/session/", AuthSessionAPIView.as_view()),
    path("app/v1/auth/providers/", AuthMethodsAPIView.as_view()),
    path("app/v1/my/", MyUserAPIView.as_view()),
    *router.urls,
]
