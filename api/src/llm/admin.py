from django.contrib import admin

from utils.admin import ReadOnlyAdmin

from .models import LLMConnection, ModelProfile, ModelProfileVersion


@admin.register(LLMConnection)
class LLMConnectionAdmin(ReadOnlyAdmin):
    """API keyは EncryptedSecretField（editable=False）なので管理画面のフォームに出ず、ここで作ると
    API keyのない接続ができてしまう。種別ごとの検証も LLMConnectionSerializer にある
    """

    readonly_fields = ("uid", "created_by", "created", "updated")
    list_display = ("id", "uid", "name", "kind", "base_url", "is_active", "deleted", "updated")
    list_filter = ("kind", "is_active", "deleted")
    search_fields = ("uid", "name")


@admin.register(ModelProfile)
class ModelProfileAdmin(ReadOnlyAdmin):
    """ここで作ると版のないモデル設定ができてしまう。作成と版の追加は ModelProfileSerializer で行う"""

    readonly_fields = ("uid", "current_version", "created_by", "created", "updated")
    list_display = ("id", "uid", "name", "current_version", "is_active", "deleted", "updated")
    list_filter = ("is_active", "deleted")
    search_fields = ("uid", "name")


@admin.register(ModelProfileVersion)
class ModelProfileVersionAdmin(ReadOnlyAdmin):
    """版は ModelProfile.revise() でだけ作る（管理画面から番号や作成者を自由に入れた版を履歴に混ぜない）"""

    readonly_fields = ("uid", "profile", "connection", "created_by", "created")
    list_display = ("id", "profile", "number", "connection", "model_name", "created")
