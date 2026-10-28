"""出口へ送る共通の形（ResultEnvelope。design.md §4.3・§4.4）。

どの通知の内容にも、外からイベントを見分ける最小の情報（event_id・event_type・Runのidと状態・失敗のコード）を含める。
業務結果は通知の内容が `result`・`status_and_result` で、Runが完了したときだけ含める。失敗の理由の本文・ログ・会話・
Skillの原本は含めない（Webhookの送り元が状態を確かめる口と同じく、決まった失敗のコードだけ。authorization.md §7.1）。
成果物（期限付きのファイルURL）と結果を確かめる画面へのリンクは、後の段階で足す。
"""

from typing import Any

from run.models import Run, RunEndReason, RunEvent, RunStatus

from .models import NotifyContent, NotifyEvent

# 送る形の版。項目を変えるときに上げる
PAYLOAD_VERSION = 1

EVENT_TYPES: dict[int, NotifyEvent] = {
    RunStatus.COMPLETED: NotifyEvent.COMPLETED,
    RunStatus.FAILED: NotifyEvent.FAILED,
    RunStatus.CANCELLED: NotifyEvent.CANCELLED,
}


def event_id(run: Run, event: RunEvent) -> str:
    """送り直しでも変わらないイベントのID（受け手は重複の判定に使える。design.md §4.5）"""
    return f"{run.uid}.{event.sequence}"


def build_payload(run: Run, event: RunEvent, event_type: str, content: str) -> dict[str, Any]:
    failure_code = None
    if run.status in (RunStatus.FAILED, RunStatus.CANCELLED) and run.end_reason is not None:
        failure_code = RunEndReason(run.end_reason).name.lower()
    task = run.task
    revision = run.task_revision
    payload: dict[str, Any] = {
        "version": PAYLOAD_VERSION,
        "event_id": event_id(run, event),
        "event_type": event_type,
        "occurred_at": event.created.isoformat(),
        "run": {
            "id": run.uid,
            "status": RunStatus(run.status).name.lower(),
            "failure_code": failure_code,
            "created": run.created.isoformat(),
            "finished": run.finished.isoformat() if run.finished else None,
        },
        "task": {
            "id": task.uid if task else None,
            "name": task.name if task else None,
            "version": revision.number if revision else None,
        },
    }
    if content in (NotifyContent.RESULT, NotifyContent.STATUS_AND_RESULT):
        payload["result"] = run.result if run.status == RunStatus.COMPLETED else None
    return payload
