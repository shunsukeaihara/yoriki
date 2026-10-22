from django.contrib import admin

from utils.admin import ReadOnlyAdmin

from .models import RuntimeProfile, RuntimeProfileVersion


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
