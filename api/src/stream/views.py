"""SSEのストリーム（realtime.md §3・§4）。DRFのViewSetにせず、Djangoの非同期viewで返す"""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from asgiref.sync import sync_to_async
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpRequest, HttpResponse, HttpResponseBase, StreamingHttpResponse

from run.models import RunEvent
from run.views import my_runs
from user.models import User

from .notifier import get_listener, run_channel, user_channel
from .serializers import serialize_event

# 接続を保つためのコメントを送る間隔（秒）。nginxの proxy_read_timeout より短くする
KEEPALIVE_SECONDS = 15
RETRY_MILLISECONDS = 3000


def _format(event: str, data: dict[str, Any], event_id: int | None = None) -> str:
    lines = [f"id: {event_id}"] if event_id is not None else []
    lines += [f"event: {event}", f"data: {json.dumps(data, cls=DjangoJSONEncoder, ensure_ascii=False)}"]
    return "\n".join(lines) + "\n\n"


def _headers() -> dict[str, str]:
    return {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


async def run_stream(request: HttpRequest, run: str) -> HttpResponseBase:
    """1つのRunの詳細イベント。`Last-Event-ID`（RunEventのsequence）より後から送る"""
    user = await request.auser()
    if not user.is_authenticated:
        return HttpResponse(status=401)
    # 見られるRunの条件はpermission・所属を読むので（同期のDBの処理）、threadで組み立てて読む
    target = await sync_to_async(lambda: my_runs(user).filter(uid=run).values("pk", "uid").first())()
    if target is None:
        return HttpResponse(status=404)
    after = _last_event_id(request)
    return StreamingHttpResponse(
        _run_events(target["pk"], target["uid"], after, user.pk), content_type="text/event-stream", headers=_headers()
    )


async def user_stream(request: HttpRequest) -> HttpResponseBase:
    """ログインユーザー宛ての通知（自分のRunの状態の変更）"""
    user = await request.auser()
    if not user.is_authenticated:
        return HttpResponse(status=401)
    return StreamingHttpResponse(_user_events(user.uid), content_type="text/event-stream", headers=_headers())


def _last_event_id(request: HttpRequest) -> int:
    value = request.headers.get("Last-Event-ID") or request.GET.get("after") or "0"
    return int(value) if value.isdigit() else 0


# DBから一度に読むイベントの数。読み切るまで続けて読む
PAGE_SIZE = 500


# 接続の間も、Runを見られるかを確かめ直す間隔（秒）。タスクの管理の権限・所属を外された人に、開いたままの接続から
# ログを送り続けない（authorization.md §7.1）
RECHECK_SECONDS = KEEPALIVE_SECONDS


@sync_to_async
def _still_visible(user_pk: int, run_pk: int) -> bool:
    """利用者をDBから読み直して確かめる（permission・所属の判定はユーザーのオブジェクトにキャッシュされるため）"""
    user = User.objects.filter(pk=user_pk).first()
    return user is not None and my_runs(user).filter(pk=run_pk).exists()


@sync_to_async
def _events_after(run_pk: int, run_uid: str, after: int) -> list[tuple[int, str, dict[str, Any]]]:
    events = RunEvent.objects.filter(run_id=run_pk, sequence__gt=after).order_by("sequence")[:PAGE_SIZE]
    return [(e.sequence, *_typed(serialize_event(e, run_uid))) for e in events]


def _typed(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    return data["type"], data


async def _run_events(run_pk: int, run_uid: str, after: int, user_pk: int) -> AsyncIterator[str]:
    loop = asyncio.get_running_loop()
    listener = get_listener()
    channel = run_channel(run_uid)
    # 先に購読してから、DBの記録を読む。読んだ後に保存されたイベントは通知で必ず届く
    queue = await listener.subscribe(channel)
    try:
        yield f"retry: {RETRY_MILLISECONDS}\n\n"
        last = after
        checked = loop.time()
        while True:
            # 読み切るまで続けて読む（再接続で多くのイベントが溜まっていても止まらない）。ページごとに、見られるかを
            # 確かめ直す
            while True:
                if not await _still_visible(user_pk, run_pk):
                    return
                checked = loop.time()
                page = await _events_after(run_pk, run_uid, last)
                for sequence, kind, data in page:
                    yield _format(kind, data, sequence)
                    last = sequence
                if len(page) < PAGE_SIZE:
                    break
            # 新しいイベントの通知か、LISTENの接続のやり直し（rescan）を待つ。しばらく何もなければ、
            # 通知を取りこぼした場合に備えてDBを読み直す
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    break
                if payload.get("type") == "run.delta":
                    if loop.time() - checked >= RECHECK_SECONDS:
                        if not await _still_visible(user_pk, run_pk):
                            return
                        checked = loop.time()
                    yield _format("run.delta", payload)
                    continue
                if payload.get("rescan") or payload.get("sequence", 0) > last:
                    break
    finally:
        await listener.unsubscribe(channel, queue)


async def _user_events(user_uid: str) -> AsyncIterator[str]:
    listener = get_listener()
    channel = user_channel(user_uid)
    queue = await listener.subscribe(channel)
    try:
        yield f"retry: {RETRY_MILLISECONDS}\n\n"
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            if payload.get("rescan"):
                continue  # この通知は保存していないので、読み直すものがない
            yield _format(payload.get("type", "notification"), payload)
    finally:
        await listener.unsubscribe(channel, queue)
