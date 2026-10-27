from django.contrib import admin

from utils.admin import ReadOnlyAdmin

from .models import TaskDefinition, TaskDefinitionRevision


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
