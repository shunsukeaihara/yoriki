from django.contrib import admin

from utils.admin import ReadOnlyAdmin

from .models import Delivery, DeliveryAttempt, Destination, DestinationRevision


@admin.register(Destination)
class DestinationAdmin(ReadOnlyAdmin):
    """作成・変更・停止・署名の鍵の作り直しはYorikiの画面（API）で行う（送り先を確かめ、版を記録する）"""

    raw_id_fields = ("department", "current_revision", "created_by")
    list_display = ("id", "uid", "name", "kind", "scope", "department", "current_revision", "suspended", "deleted")
    list_filter = ("kind", "scope", "suspended", "deleted")
    search_fields = ("uid", "name")


@admin.register(DestinationRevision)
class DestinationRevisionAdmin(ReadOnlyAdmin):
    raw_id_fields = ("destination", "created_by")
    list_display = ("id", "uid", "destination", "number", "allows_sensitive_results", "created_by", "created")
    search_fields = ("uid", "destination__name")


@admin.register(Delivery)
class DeliveryAdmin(ReadOnlyAdmin):
    raw_id_fields = ("run", "run_event", "destination_revision")
    list_display = ("id", "uid", "run", "event_type", "destination_revision", "status", "attempt_count", "created")
    list_filter = ("status", "event_type")
    search_fields = ("uid", "run__uid")


@admin.register(DeliveryAttempt)
class DeliveryAttemptAdmin(ReadOnlyAdmin):
    raw_id_fields = ("delivery",)
    list_display = ("id", "delivery", "number", "outcome", "http_status", "started", "finished")
    list_filter = ("outcome",)
