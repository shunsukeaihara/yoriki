from django.contrib import admin

from utils.admin import ReadOnlyAdmin

from .models import Skill, SkillAssetBinding, SkillChange, SkillDraft, SkillDraftAsset, SkillVersion


@admin.register(Skill)
class SkillAdmin(ReadOnlyAdmin):
    """作成・公開・切戻し・停止はYorikiの画面（API）で行う（公開範囲の権限と版の記録をそこで扱う）"""

    raw_id_fields = ("owner", "department", "current_version", "created_by")
    list_display = ("id", "uid", "name", "scope", "owner", "department", "current_version", "suspended", "deleted")
    list_filter = ("scope", "suspended", "deleted")
    search_fields = ("uid", "name")


class SkillAssetBindingInline(admin.TabularInline):
    model = SkillAssetBinding
    extra = 0
    fields = ("key", "role", "description", "resource_version")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(SkillVersion)
class SkillVersionAdmin(ReadOnlyAdmin):
    raw_id_fields = ("skill", "rolled_back_from", "created_by")
    list_display = ("id", "uid", "skill", "number", "created_by", "created")
    search_fields = ("uid", "skill__name")
    inlines = [SkillAssetBindingInline]


class SkillDraftAssetInline(admin.TabularInline):
    model = SkillDraftAsset
    extra = 0
    fields = ("key", "role", "description", "resource_version")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(SkillDraft)
class SkillDraftAdmin(ReadOnlyAdmin):
    raw_id_fields = ("skill", "base_version", "created_by", "updated_by")
    list_display = ("id", "uid", "skill", "base_version", "revision", "updated_by", "updated")
    search_fields = ("uid", "skill__name")
    inlines = [SkillDraftAssetInline]


@admin.register(SkillChange)
class SkillChangeAdmin(ReadOnlyAdmin):
    raw_id_fields = ("skill", "version", "previous_version", "actor")
    list_display = ("id", "uid", "skill", "kind", "version", "actor", "created")
    list_filter = ("kind",)
    search_fields = ("uid", "skill__name")
