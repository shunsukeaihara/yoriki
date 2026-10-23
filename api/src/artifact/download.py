"""Artifactの取得（design.md §4.4、§7.4、§7.8、deployment.md §4.1）。

権限はViewで確かめてから、ここでファイルを返す。Local保存でnginxの内部locationが設定されていれば、
`X-Accel-Redirect` でnginxに返させる。それ以外は、保存先から少しずつ読んで返す（ASGIでは非同期のiteratorにしないと、
Djangoがファイル全体をメモリに読み込むため）。

エージェントが作ったHTML等を、アプリと同じoriginで開かせない。いつも添付（ダウンロード）として返し、
`nosniff` と `sandbox` のCSPを付ける。
"""

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import IO, Protocol

from asgiref.sync import sync_to_async
from django.core import signing
from django.http import HttpResponse, StreamingHttpResponse
from django.utils import timezone
from django.utils.http import content_disposition_header

from .models import Artifact, BlobObject
from .storage import CHUNK, get_storage

FILE_TOKEN_SALT = "yoriki.artifact.file"


class StoredFile(Protocol):
    """ファイルとして返せるもの（Artifact、Skillの資料の版）"""

    blob: BlobObject
    name: str
    content_type: str

    @property
    def size(self) -> int: ...


def file_response(artifact: StoredFile) -> HttpResponse | StreamingHttpResponse:
    location = artifact.blob.location()
    storage = get_storage(location.backend)
    response: HttpResponse | StreamingHttpResponse
    accel = storage.accel_redirect(location.key)
    if accel:
        response = HttpResponse(content_type=artifact.content_type)
        response["X-Accel-Redirect"] = accel
    else:
        response = StreamingHttpResponse(_chunks(storage.open_read(location.key)), content_type=artifact.content_type)
        response["Content-Length"] = str(artifact.size)
    response["Content-Disposition"] = content_disposition_header(True, artifact.name)
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "sandbox; default-src 'none'"
    response["Cache-Control"] = "private, no-store"
    return response


async def _chunks(src: IO[bytes]) -> AsyncIterator[bytes]:
    read = sync_to_async(src.read, thread_sensitive=False)
    try:
        while chunk := await read(CHUNK):
            yield chunk
    finally:
        await sync_to_async(src.close, thread_sensitive=False)()


def issue_file_token(artifact: Artifact, expires_in: timedelta) -> str:
    """ログインなしで取得できる、期限付きのtoken（`/api/files/<token>/`）。Zapier等の外部へ成果物を渡すときに使う。
    発行したtokenは期限まで取り消せないので、期限は配信に要る長さにする（design.md §4.4）"""
    expires = int((timezone.now() + expires_in).timestamp())
    return signing.dumps({"artifact": artifact.uid, "expires": expires}, salt=FILE_TOKEN_SALT)


def resolve_file_token(token: str) -> Artifact | None:
    """有効なtokenのArtifact。期限切れ・改ざん・削除済みならNone"""
    try:
        data = signing.loads(token, salt=FILE_TOKEN_SALT)
    except signing.BadSignature:
        return None
    if not isinstance(data, dict) or int(data.get("expires", 0)) <= timezone.now().timestamp():
        return None
    return Artifact.objects.select_related("blob").filter(uid=data.get("artifact"), deleted=False).first()
