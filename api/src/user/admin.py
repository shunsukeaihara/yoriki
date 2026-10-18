from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    readonly_fields = ("uid", "last_login", "date_joined", "created", "updated")
    list_display = (
        "id",
        "uid",
        "username",
        "email",
        "display_name",
        "is_superuser",
        "is_active",
    )
    list_filter = ("is_superuser", "is_active", "groups")
    search_fields = ("username", "email", "display_name", "uid")
    fieldsets = (
        (None, {"fields": ("uid", "username", "password")}),
        (_("個人情報"), {"fields": ("display_name", "email", "first_name", "last_name")}),
        # 役割はGroup（user/roles.py）で割り当てる。個別のpermissionも追加できる
        (_("権限"), {"fields": ("is_active", "is_superuser", "is_staff", "groups", "user_permissions")}),
        (_("日時"), {"fields": ("last_login", "date_joined", "created", "updated")}),
    )
