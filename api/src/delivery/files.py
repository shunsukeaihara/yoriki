"""出口へ渡した成果物の取得（`/api/files/<token>/`。design.md §4.4、authorization.md §7.1）。

セッションを使わず、tokenで取得させる。取得のたびに、期限・回数と、配信の出口とタスクが今も使えるかを確かめ直す
（出口・タスクを止めたら、期限の前でも取得できない）。汎用のWebhookの出口では、出口の署名の鍵も
`Authorization: Bearer` で求める（URLだけが受け手のログ等から漏れても取得させない。ZapierはURLだけで取得するので
求めない）。S3に保存したものは、確かめた後にごく短い期限の署名付きURLへリダイレクトする。
"""

import hmac
import logging

from django.db.models import F
from django.http import HttpResponse, HttpResponseRedirect, StreamingHttpResponse
from django.utils import timezone
from django.utils.translation import gettext as _

from artifact.download import file_response
from artifact.storage import StorageError, get_storage
from utils.backends import client_ip

from .checks import send_problem
from .models import FILE_LINK_MAX_FETCHES, DestinationKind, FileFetch, FileLink

logger = logging.getLogger(__name__)


class FileRefused(Exception):
    def __init__(self, reason: str, *, unauthorized: bool = False, unavailable: bool = False):
        super().__init__(reason)
        self.unauthorized = unauthorized
        # 渡してよいが、保存先から読めなかった（受け手は後で取り直せる）
        self.unavailable = unavailable


def fetch_file(token: str, request) -> HttpResponse | StreamingHttpResponse:
    """tokenの成果物を返す。返せなければ FileRefused（理由は記録に残し、取得した側には見せない）。知らないtokenは
    FileRefused（記録はない）"""
    link = (
        FileLink.objects.select_related(
            "artifact__blob",
            "attempt__delivery__run__task__department",
            "attempt__delivery__run__task__owner",
            "attempt__delivery__destination_revision__destination",
        )
        .filter(token_hash=FileLink.hash_token(token))
        .first()
    )
    if link is None:
        raise FileRefused("unknown")
    try:
        _check(link, request)
    except FileRefused as exc:
        _record(link, request, refusal=str(exc))
        raise
    try:
        response = _response(link.artifact)
    except (StorageError, OSError, IndexError) as exc:
        # 返せなかった取得は回数に数えず、返せなかったとして記録する
        logger.warning("file link %s: cannot read the file: %s", link.pk, exc)
        FileLink.objects.filter(pk=link.pk).update(fetch_count=F("fetch_count") - 1)
        _record(link, request, refusal=_("保存先からファイルを読めませんでした"))
        raise FileRefused("storage", unavailable=True) from exc
    _record(link, request)
    return response


def _response(artifact) -> HttpResponse | StreamingHttpResponse:
    location = artifact.blob.location()
    url = get_storage(location.backend).signed_url(location.key, name=artifact.name, content_type=artifact.content_type)
    if url is None:
        return file_response(artifact)
    response = HttpResponseRedirect(url)
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


def _check(link: FileLink, request) -> None:
    delivery = link.attempt.delivery
    destination = delivery.destination_revision.destination
    if destination.kind == DestinationKind.WEBHOOK:
        sent = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not sent or not hmac.compare_digest(sent.encode(), destination.signing_secret.reveal().encode()):
            raise FileRefused(_("出口の署名の鍵がありません・違います"), unauthorized=True)
    if link.expires <= timezone.now():
        raise FileRefused(_("期限が切れています"))
    if link.artifact.deleted:
        raise FileRefused(_("成果物が削除されています"))
    if problem := send_problem(delivery):
        raise FileRefused(problem)
    # 回数は最後に数える（ほかの理由で断ったものは数えない）。同時の取得で上限を超えないよう、返す前に数えておく
    if not FileLink.objects.filter(pk=link.pk, fetch_count__lt=FILE_LINK_MAX_FETCHES).update(
        fetch_count=F("fetch_count") + 1
    ):
        raise FileRefused(_("取得できる回数（%(count)s回）を超えました") % {"count": FILE_LINK_MAX_FETCHES})


def _record(link: FileLink, request, *, refusal: str = "") -> None:
    FileFetch.objects.create(
        link=link,
        served=not refusal,
        refusal=refusal[:200],
        ip_address=client_ip(request),
        user_agent=request.headers.get("User-Agent", "")[:300],
    )
    if refusal:
        logger.info("file link %s refused: %s", link.pk, refusal)
