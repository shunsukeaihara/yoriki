from django.contrib import admin

from utils.admin import ReadOnlyAdmin

from .models import Plugin, PluginExtension, PluginFailure


@admin.register(Plugin)
class PluginAdmin(ReadOnlyAdmin):
    """行は配備されているコードから作る。有効・停止は管理画面（/manage/plugins）で切り替える"""

    list_display = ("id", "plugin_id", "name", "version", "builtin", "enabled", "deployed", "updated")
    list_filter = ("builtin", "enabled", "deployed")
    search_fields = ("plugin_id", "name")


@admin.register(PluginExtension)
class PluginExtensionAdmin(ReadOnlyAdmin):
    list_display = ("id", "extension_id", "plugin", "extension_type", "name", "deployed")
    list_filter = ("extension_type", "deployed")
    search_fields = ("extension_id", "name")


@admin.register(PluginFailure)
class PluginFailureAdmin(ReadOnlyAdmin):
    list_display = ("id", "extension", "plugin_version", "day", "unexpected", "timeouts", "last_at")
    list_filter = ("day",)
