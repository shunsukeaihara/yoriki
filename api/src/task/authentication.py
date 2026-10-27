"""Webhookの入口の認証（design.md §4.2、authorization.md §5.5）。URLを知っているだけの呼出しは受け付けない"""

from rest_framework.exceptions import NotFound
from rest_framework.permissions import BasePermission

from utils.authentication import HookKeyAuthentication

from .models import TriggerBinding, TriggerKind


class WebhookKeyAuthentication(HookKeyAuthentication):
    """入口の鍵で認証する。消した入口の鍵と、作り直す前の鍵は使えない（停止中の入口は、受付で断る）"""

    def resolve(self, key_hash: str) -> TriggerBinding | None:
        return (
            TriggerBinding.objects.alive()
            .filter(key_hash=key_hash, kind=TriggerKind.WEBHOOK)
            .select_related("task", "current_revision")
            .first()
        )


class WebhookTaskPermission(BasePermission):
    """認証した入口のタスクだけ（入口とタスクは1対1）。別のタスクのURLは、あるかどうかも見せずに404にする"""

    def has_permission(self, request, view) -> bool:
        binding = request.auth
        if not isinstance(binding, TriggerBinding):
            return False
        if binding.task.uid != view.kwargs.get("task"):
            raise NotFound()
        return True
