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
        "is_business_admin",
        "is_plugin_publisher",
        "is_superuser",
        "is_active",
    )
    list_filter = ("is_business_admin", "is_plugin_publisher", "is_superuser", "is_active")
    search_fields = ("username", "email", "display_name", "uid")
    fieldsets = (
        (None, {"fields": ("uid", "username", "password")}),
        (_("個人情報"), {"fields": ("display_name", "email", "first_name", "last_name")}),
        (_("役割"), {"fields": ("is_active", "is_business_admin", "is_plugin_publisher", "is_superuser", "is_staff")}),
        (_("権限"), {"fields": ("groups", "user_permissions")}),
        (_("日時"), {"fields": ("last_login", "date_joined", "created", "updated")}),
    )
