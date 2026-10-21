from django.contrib import admin

from utils.admin import ReadOnlyAdmin

from .models import ClarificationRequest, ExecutionAttempt, Run, RunEvent, ToolInvocation


@admin.register(Run)
class RunAdmin(ReadOnlyAdmin):
    """状態はWorkerとRunnerだけが進める（管理画面から変えると遷移表と記録を通らない）"""

    readonly_fields = ("uid", "conversation", "parent", "owner", "model_version", "created", "updated", "finished")
    list_display = ("id", "uid", "conversation", "status", "end_reason", "step_count", "created", "finished")
    list_filter = ("status", "end_reason", "trigger")
    search_fields = ("uid",)


@admin.register(ExecutionAttempt)
class ExecutionAttemptAdmin(ReadOnlyAdmin):
    readonly_fields = ("run",)
    list_display = ("id", "run", "number", "worker", "lease_expires", "started", "ended", "failure")


@admin.register(RunEvent)
class RunEventAdmin(ReadOnlyAdmin):
    readonly_fields = ("run", "actor", "created")
    list_display = ("id", "run", "sequence", "kind", "created")
    list_filter = ("kind",)


@admin.register(ClarificationRequest)
class ClarificationRequestAdmin(ReadOnlyAdmin):
    readonly_fields = ("uid", "run", "answered_by", "created")
    list_display = ("id", "uid", "run", "kind", "status", "answered", "expires")
    list_filter = ("kind", "status")


@admin.register(ToolInvocation)
class ToolInvocationAdmin(ReadOnlyAdmin):
    readonly_fields = ("uid", "run", "attempt", "created", "ended")
    list_display = ("id", "uid", "run", "name", "status", "created", "ended")
    list_filter = ("name", "status")
