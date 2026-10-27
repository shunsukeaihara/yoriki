from django.contrib import admin

from utils.admin import ReadOnlyAdmin

from .models import TaskDefinition, TaskDefinitionRevision, TriggerBinding, TriggerBindingRevision, TriggerReceipt


@admin.register(TaskDefinition)
class TaskDefinitionAdmin(ReadOnlyAdmin):
    """作成・変更・停止はYorikiの画面（API）で行う（範囲の権限と版の記録をそこで扱う）"""

    raw_id_fields = ("owner", "department", "current_revision", "created_by")
    list_display = ("id", "uid", "name", "scope", "owner", "department", "current_revision", "suspended", "deleted")
    list_filter = ("scope", "suspended", "deleted")
    search_fields = ("uid", "name")


@admin.register(TaskDefinitionRevision)
class TaskDefinitionRevisionAdmin(ReadOnlyAdmin):
    raw_id_fields = ("task", "model_profile", "created_by")
    list_display = ("id", "uid", "task", "number", "created_by", "created")
    search_fields = ("uid", "task__name")


@admin.register(TriggerBinding)
class TriggerBindingAdmin(ReadOnlyAdmin):
    """作成・変更・停止・鍵の作り直しはYorikiの画面（API）で行う（鍵は一度だけ表示し、版を記録する）"""

    raw_id_fields = ("task", "current_revision", "created_by")
    list_display = ("id", "uid", "name", "kind", "task", "key_prefix", "current_revision", "suspended", "deleted")
    list_filter = ("kind", "suspended", "deleted")
    search_fields = ("uid", "name", "key_prefix", "task__name")
    exclude = ("key_hash",)


@admin.register(TriggerBindingRevision)
class TriggerBindingRevisionAdmin(ReadOnlyAdmin):
    raw_id_fields = ("binding", "created_by")
    list_display = ("id", "uid", "binding", "number", "created_by", "created")
    search_fields = ("uid", "binding__name")


@admin.register(TriggerReceipt)
class TriggerReceiptAdmin(ReadOnlyAdmin):
    raw_id_fields = ("binding", "binding_revision", "run")
    list_display = ("id", "uid", "binding", "dedup_key", "run", "created")
    search_fields = ("uid", "dedup_key", "binding__name")
