from django.contrib import admin

from .models import LLMConnection, ModelProfile, ModelProfileVersion


@admin.register(LLMConnection)
class LLMConnectionAdmin(admin.ModelAdmin):
    # API keyは管理画面に出さない（暗号化した値も出さない）。変更はYorikiの管理画面（/manage）で行う
    exclude = ("api_key_encrypted",)
    raw_id_fields = ("created_by",)
    readonly_fields = ("uid", "api_key_hint", "created", "updated")
    list_display = ("id", "uid", "name", "kind", "base_url", "is_active", "deleted", "updated")
    list_filter = ("kind", "is_active", "deleted")
    search_fields = ("uid", "name")

    def get_readonly_fields(self, request, obj=None):
        # 接続先は変えさせない。API keyを入れ直す欄がないので、登録済みのAPI keyが別のサーバーへ送られてしまう。
        # 変更はYorikiの管理画面（API keyの入れ直しと、参照中の接続の制限がある）で行う
        fields = super().get_readonly_fields(request, obj)
        return (*fields, "kind", "base_url") if obj else fields


@admin.register(ModelProfile)
class ModelProfileAdmin(admin.ModelAdmin):
    raw_id_fields = ("current_version", "created_by")
    readonly_fields = ("uid", "current_version", "created", "updated")
    list_display = ("id", "uid", "name", "current_version", "is_active", "deleted", "updated")
    list_filter = ("is_active", "deleted")
    search_fields = ("uid", "name")


@admin.register(ModelProfileVersion)
class ModelProfileVersionAdmin(admin.ModelAdmin):
    raw_id_fields = ("profile", "connection", "created_by")
    readonly_fields = ("uid", "created")
    list_display = ("id", "profile", "number", "connection", "model_name", "data_classification", "created")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
