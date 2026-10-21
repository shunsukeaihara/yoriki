from django.contrib import admin

from utils.admin import ReadOnlyAdmin

from .models import Conversation, ConversationMessage, UserInstruction


@admin.register(Conversation)
class ConversationAdmin(ReadOnlyAdmin):
    """会話・メッセージは社員の画面（API）でだけ作る。調査のために見るだけにする"""

    readonly_fields = ("uid", "owner", "model_profile", "created", "updated")
    list_display = ("id", "uid", "owner", "title", "model_profile", "handles_sensitive_data", "last_posted", "deleted")
    list_filter = ("handles_sensitive_data", "deleted")
    search_fields = ("uid", "title")


@admin.register(ConversationMessage)
class ConversationMessageAdmin(ReadOnlyAdmin):
    readonly_fields = ("uid", "conversation", "run", "sender", "created")
    list_display = ("id", "uid", "conversation", "role", "run", "created")
    list_filter = ("role",)


@admin.register(UserInstruction)
class UserInstructionAdmin(ReadOnlyAdmin):
    readonly_fields = ("uid", "conversation", "message", "run", "created", "updated")
    list_display = ("id", "uid", "conversation", "kind", "state", "run", "created")
    list_filter = ("kind", "state")
