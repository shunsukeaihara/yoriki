from django.contrib import admin

from utils.admin import ReadOnlyAdmin

from .models import Connector, ConnectorPermission, ConnectorVersion, DatabaseConnection


@admin.register(DatabaseConnection)
class DatabaseConnectionAdmin(ReadOnlyAdmin):
    """パスワードは EncryptedSecretField（editable=False）なので管理画面のフォームに出ない。作成と変更は
    DatabaseConnectionSerializer で行う（接続先を変えるときにパスワードを入れ直させる検証がある）
    """

    readonly_fields = ("uid", "created_by", "created", "updated")
    list_display = ("id", "uid", "name", "kind", "host", "port", "database", "is_active", "deleted", "updated")
    list_filter = ("kind", "is_active", "deleted")
    search_fields = ("uid", "name", "host")


@admin.register(Connector)
class ConnectorAdmin(ReadOnlyAdmin):
    """作成と版の追加は ConnectorSerializer で行う（ここで作ると、版のないConnectorができてしまう）"""

    readonly_fields = ("uid", "connection", "current_version", "created_by", "created", "updated")
    list_display = ("id", "uid", "name", "connection", "current_version", "is_active", "deleted", "updated")
    list_filter = ("is_active", "deleted")
    search_fields = ("uid", "name", "connection__name", "connection__host")


@admin.register(ConnectorVersion)
class ConnectorVersionAdmin(ReadOnlyAdmin):
    """版は Connector.revise() でだけ作る"""

    readonly_fields = ("uid", "connector", "created_by", "created")
    list_display = ("id", "connector", "number", "is_sensitive", "created")


@admin.register(ConnectorPermission)
class ConnectorPermissionAdmin(ReadOnlyAdmin):
    """利用の許可は ConnectorSerializer で保存する（役割か部門のどちらか一方の検証がある）"""

    readonly_fields = ("uid", "connector", "group", "department", "created", "updated")
    list_display = ("id", "connector", "group", "department", "operations", "updated")
