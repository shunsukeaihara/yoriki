from django.contrib import admin

from utils.admin import ReadOnlyAdmin

from .models import Artifact, BlobLocation, BlobObject


class BlobLocationInline(admin.TabularInline):
    model = BlobLocation
    extra = 0
    readonly_fields = ("backend", "key", "created")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(BlobObject)
class BlobObjectAdmin(ReadOnlyAdmin):
    list_display = ("id", "uid", "size", "sha256", "created")
    search_fields = ("uid", "sha256")
    inlines = [BlobLocationInline]


@admin.register(Artifact)
class ArtifactAdmin(ReadOnlyAdmin):
    raw_id_fields = ("owner", "conversation", "run", "blob")
    list_display = ("id", "uid", "kind", "name", "content_type", "owner", "created", "deleted")
    list_filter = ("kind", "deleted")
    search_fields = ("uid", "name")
