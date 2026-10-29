"""出口へ送る共通の形（ResultEnvelope。design.md §4.3・§4.4）。

どの通知の内容にも、外からイベントを見分ける最小の情報（event_id・event_type・Runのidと状態・失敗のコード）を含める。
業務結果は通知の内容が `result`・`status_and_result` で、Runが完了したときだけ含める。失敗の理由の本文・ログ・会話・
Skillの原本は含めない（Webhookの送り元が状態を確かめる口と同じく、決まった失敗のコードだけ。authorization.md §7.1）。

成果物は、業務結果のSchemaのファイルの項目（harness/result_files.py）で指したものだけを `files` に入れる。保存する
内容にはファイルの情報（ID・名前・形式・大きさ・SHA-256）だけを入れ、取得用の期限付きURLは送るたびに発行して足す
（`with_file_links()`）。結果を確かめる画面へのリンクは、後の段階で足す。
"""

import logging
from typing import Any

from django.conf import settings
from django.utils.translation import gettext as _

from artifact.models import Artifact, ArtifactKind
from harness.result_files import result_files
from harness.tasks import output_schema
from run.models import Run, RunEndReason, RunEvent, RunStatus

from .models import DeliveryAttempt, FileLink, NotifyContent, NotifyEvent

logger = logging.getLogger(__name__)

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
        completed = run.status == RunStatus.COMPLETED
        payload["result"] = run.result if completed else None
        payload["files"] = result_artifacts(run) if completed else []
    return payload


def result_artifacts(run: Run) -> list[dict[str, Any]]:
    """業務結果のファイルの項目が指す成果物。Runの完了の前に回収したもの（harness/runner.py の _checkpoint()）から、
    同じパスの最新のものを選ぶ（変わっていないファイルは回収し直さないので、同じ会話の前のRunのものもある）"""
    files = []
    for field, path in result_files(output_schema(run), run.result):
        artifact = (
            Artifact.objects.select_related("blob")
            .filter(conversation_id=run.conversation_id, kind=ArtifactKind.OUTPUT, path=path, deleted=False)
            .order_by("-created", "-pk")
            .first()
        )
        if artifact is None:
            logger.warning("run %s: the result file %s (%s) was not collected", run.uid, path, field)
            continue
        files.append(
            {
                "field": field,
                "id": artifact.uid,
                "name": artifact.name,
                "content_type": artifact.content_type,
                "size": artifact.size,
                "sha256": artifact.sha256,
            }
        )
    return files


class FileLinksUnavailable(Exception):
    pass


def with_file_links(payload: dict[str, Any], attempt: DeliveryAttempt) -> dict[str, Any]:
    """送る内容に、ファイルの取得用の期限付きURL（`url`・`expires_at`）を足す。成果物が保持期限で消えていれば `url` は
    空。URLを作れない設定なら FileLinksUnavailable"""
    files = payload.get("files")
    if not files:
        return payload
    base = settings.YORIKI_PUBLIC_URL
    if not base:
        raise FileLinksUnavailable(_("ファイルのURLを作れません（YORIKI_PUBLIC_URL が未設定です）"))
    artifacts = Artifact.objects.filter(deleted=False).in_bulk([f["id"] for f in files], field_name="uid")
    linked = []
    for entry in files:
        artifact = artifacts.get(entry["id"])
        if artifact is None:
            linked.append({**entry, "url": None, "expires_at": None})
            continue
        link, token = FileLink.issue(attempt, artifact)
        linked.append({**entry, "url": f"{base}/api/files/{token}/", "expires_at": link.expires.isoformat()})
    return {**payload, "files": linked}
