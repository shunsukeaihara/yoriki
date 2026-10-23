from django.contrib import admin

from utils.admin import ReadOnlyAdmin

from .models import Resource, ResourceVersion


class ResourceVersionInline(admin.TabularInline):
    model = ResourceVersion
    extra = 0
    fields = ("uid", "number", "name", "content_type", "blob", "created_by", "created")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Resource)
class ResourceAdmin(ReadOnlyAdmin):
    """資料はSkillの資料の操作（API）で作る。原本の版は変えない"""

    list_display = ("id", "uid", "created_by", "created", "updated")
    search_fields = ("uid", "versions__name")
    inlines = [ResourceVersionInline]
