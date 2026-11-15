from django.contrib import admin

from utils.admin import ReadOnlyAdmin

from .models import (
    EnvironmentRevision,
    PlatformSettings,
    PythonPackage,
    PythonWheel,
    RuntimeProfile,
    RuntimeProfileVersion,
)


@admin.register(RuntimeProfile)
class RuntimeProfileAdmin(ReadOnlyAdmin):
    """ここで作ると、版のない・イメージを確かめていないRuntime Profileができてしまう。
    作成は RuntimeProfileSerializer で行う
    """

    readonly_fields = ("uid", "current_version", "created_by", "created", "updated")
    list_display = ("id", "uid", "name", "current_version", "is_active", "is_default", "deleted", "updated")
    list_filter = ("is_active", "is_default", "deleted")
    search_fields = ("uid", "name")


@admin.register(RuntimeProfileVersion)
class RuntimeProfileVersionAdmin(ReadOnlyAdmin):
    list_display = ("id", "uid", "profile", "number", "image_repository", "contract", "created")
    search_fields = ("uid", "image_repository", "image_digest")


@admin.register(PlatformSettings)
class PlatformSettingsAdmin(ReadOnlyAdmin):
    list_display = ("id", "workspace_retention_days", "updated")


@admin.register(PythonPackage)
class PythonPackageAdmin(ReadOnlyAdmin):
    list_display = ("id", "uid", "name", "specifier", "is_active", "deleted", "updated")
    list_filter = ("is_active", "deleted")
    search_fields = ("uid", "name")


@admin.register(PythonWheel)
class PythonWheelAdmin(ReadOnlyAdmin):
    list_display = ("id", "filename", "sha256", "size", "created")
    search_fields = ("filename", "sha256")


@admin.register(EnvironmentRevision)
class EnvironmentRevisionAdmin(ReadOnlyAdmin):
    list_display = ("id", "uid", "conversation", "run", "subagent", "platform", "python_version", "created")
    search_fields = ("uid",)
