"""役割のpermission（backend-conventions.md §6.3）。

基盤管理者（`is_superuser`）は、業務管理者とPlugin公開者の操作もできる。
"""

from rest_framework.permissions import BasePermission, IsAuthenticated, OperandHolder


class _AppUser(BasePermission):
    def has_permission(self, request, view) -> bool:
        return request.user.is_active


class _BusinessAdmin(BasePermission):
    def has_permission(self, request, view) -> bool:
        return request.user.is_superuser or request.user.is_business_admin


class _PluginPublisher(BasePermission):
    def has_permission(self, request, view) -> bool:
        return request.user.is_superuser or request.user.is_plugin_publisher


class _PlatformAdmin(BasePermission):
    def has_permission(self, request, view) -> bool:
        return request.user.is_superuser


AppUser = IsAuthenticated & _AppUser  # 通常社員以上
BusinessAdmin = IsAuthenticated & _BusinessAdmin  # 業務管理者
PluginPublisher = IsAuthenticated & _PluginPublisher  # Plugin公開者
PlatformAdmin = IsAuthenticated & _PlatformAdmin  # 基盤管理者


def require_permission(perm: str) -> OperandHolder:
    """Djangoのpermission（"<app_label>.<codename>"）を持つログインユーザーだけを許可する"""

    class _HasPermission(BasePermission):
        def has_permission(self, request, view) -> bool:
            return request.user.has_perm(perm)

    return IsAuthenticated & _HasPermission
