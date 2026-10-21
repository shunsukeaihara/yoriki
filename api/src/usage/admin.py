from django.contrib import admin

from utils.admin import ReadOnlyAdmin

from .models import UsageLedger


@admin.register(UsageLedger)
class UsageLedgerAdmin(ReadOnlyAdmin):
    readonly_fields = ("owner", "run", "model_version", "created")
    list_display = ("id", "owner", "run", "purpose", "input_tokens", "output_tokens", "cost", "created")
    list_filter = ("purpose",)
