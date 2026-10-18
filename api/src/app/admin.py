from django.contrib import admin
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _


class YorikiAdminSite(admin.AdminSite):
    """Django管理画面。基盤管理者だけが使える（backend-conventions.md §5）"""

    site_header = _("Yoriki 管理画面")
    site_title = _("Yoriki 管理画面")

    def has_permission(self, request: HttpRequest) -> bool:
        user = request.user
        return user.is_active and user.is_superuser
