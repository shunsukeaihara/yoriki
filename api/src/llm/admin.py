from django.contrib import admin

from .models import LLMConnection, ModelProfile, ModelProfileVersion


@admin.register(LLMConnection)
class LLMConnectionAdmin(admin.ModelAdmin):
    """API keyは管理画面に出さない（EncryptedSecretField は editable=False）。登録と差し替えはYorikiの管理画面で行う"""

    raw_id_fields = ("created_by",)
    readonly_fields = ("uid", "created", "updated")
    list_display = ("id", "uid", "name", "kind", "base_url", "is_active", "deleted", "updated")
    list_filter = ("kind", "is_active", "deleted")
    search_fields = ("uid", "name")

    def get_readonly_fields(self, request, obj=None):
        # 種別とbase URL（接続先）は作成した後は変えない
        fields = super().get_readonly_fields(request, obj)
        return (*fields, "kind", "base_url") if obj else fields


@admin.register(ModelProfile)
class ModelProfileAdmin(admin.ModelAdmin):
    raw_id_fields = ("created_by",)
    readonly_fields = ("uid", "current_version", "created", "updated")
    list_display = ("id", "uid", "name", "current_version", "is_active", "deleted", "updated")
    list_filter = ("is_active", "deleted")
    search_fields = ("uid", "name")


@admin.register(ModelProfileVersion)
class ModelProfileVersionAdmin(admin.ModelAdmin):
    raw_id_fields = ("profile", "connection", "created_by")
    readonly_fields = ("uid", "created")
    list_display = ("id", "profile", "number", "connection", "model_name", "created")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
