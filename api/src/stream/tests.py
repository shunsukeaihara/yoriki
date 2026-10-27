"""SSEと通知のテスト。LISTEN/NOTIFYは実際のPostgreSQLで確かめる（backend-conventions.md §9）"""

import asyncio
import json
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import StreamingHttpResponse
from django.test import RequestFactory

from conversation.factories import ConversationFactory
from run.factories import RunFactory
from run.models import RunEvent, RunEventKind, RunLaunch, RunStatus

from . import views
from .notifier import get_listener, notify, notify_delta
from .views import run_stream


async def _receive(channel: str, send) -> dict:
    listener = get_listener()
    queue = await listener.subscribe(channel)
    try:
        await sync_to_async(send)()
        return await asyncio.wait_for(queue.get(), timeout=5)
    finally:
        await listener.unsubscribe(channel, queue)
        await listener.close()


@pytest.mark.django_db(transaction=True)
def test_notify_reaches_the_listener():
    payload = asyncio.run(_receive("run_test", lambda: notify("run_test", {"run": "test", "sequence": 3})))
    assert payload == {"run": "test", "sequence": 3}


@pytest.mark.django_db(transaction=True)
def test_large_delta_is_split():
    received = asyncio.run(_receive("run_big", lambda: notify_delta("big", 1, "あ" * 5000)))
    assert received["type"] == "run.delta"
    assert len(received["text"].encode()) < 8000


@pytest.mark.django_db(transaction=True)
def test_recording_an_event_notifies_the_run_and_the_owner():
    run = RunFactory.create(conversation=ConversationFactory.create())
    owner_uid = run.owner.uid

    def change():
        run.set_status(RunStatus.RUNNING)

    payload = asyncio.run(_receive(f"user_{owner_uid}", change))
    assert payload["type"] == "run.status"
    assert payload["run"] == run.uid
    assert payload["status"] == RunStatus.RUNNING


@pytest.mark.django_db(transaction=True)
def test_run_stream_sends_saved_events_after_last_event_id():
    run = RunFactory.create(conversation=ConversationFactory.create())
    run.set_status(RunStatus.RUNNING)
    RunEvent.record(run, RunEventKind.ASSISTANT_MESSAGE, {"text": "確認します", "tool_calls": []})
    request = RequestFactory().get(f"/api/app/v1/runs/{run.uid}/stream/", HTTP_LAST_EVENT_ID="1")

    async def auser():
        return run.owner

    request.auser = auser

    async def read() -> list[str]:
        response = await run_stream(request, run.uid)
        assert response["Content-Type"] == "text/event-stream"
        assert isinstance(response, StreamingHttpResponse)
        chunks: list[str] = []
        stream: Any = response.streaming_content.__aiter__()  # type: ignore[union-attr]
        try:
            for _ in range(2):
                chunk = await asyncio.wait_for(stream.__anext__(), timeout=5)
                chunks.append(chunk if isinstance(chunk, str) else chunk.decode())
        finally:
            await stream.aclose()
            await get_listener().close()
        return chunks

    retry, event = asyncio.run(read())
    assert retry.startswith("retry:")
    lines = dict(line.split(": ", 1) for line in event.strip().splitlines())
    assert lines["id"] == "2"
    assert lines["event"] == "run.message"
    assert json.loads(lines["data"])["text"] == "確認します"


@pytest.mark.django_db
def test_other_users_run_stream_is_not_found(app_user):
    run = RunFactory.create(conversation=ConversationFactory.create())
    request = RequestFactory().get(f"/api/app/v1/runs/{run.uid}/stream/")

    async def auser():
        return app_user

    request.auser = auser
    response = asyncio.run(run_stream(request, run.uid))
    assert response.status_code == 404


@pytest.mark.django_db(transaction=True)
def test_run_stream_reads_a_long_backlog_to_the_end():
    run = RunFactory.create(conversation=ConversationFactory.create())
    RunEvent.objects.bulk_create(
        RunEvent(run=run, sequence=n, kind=RunEventKind.ASSISTANT_MESSAGE, data={"text": f"{n}", "tool_calls": []})
        for n in range(1, 701)
    )
    request = RequestFactory().get(f"/api/app/v1/runs/{run.uid}/stream/")

    async def auser():
        return run.owner

    request.auser = auser

    async def last_id() -> str:
        response = await run_stream(request, run.uid)
        assert isinstance(response, StreamingHttpResponse)
        stream: Any = response.streaming_content.__aiter__()  # type: ignore[union-attr]
        try:
            chunk = ""
            for _ in range(701):
                chunk = await asyncio.wait_for(stream.__anext__(), timeout=5)
            text = chunk if isinstance(chunk, str) else chunk.decode()
            return text.splitlines()[0]
        finally:
            await stream.aclose()
            await get_listener().close()

    assert asyncio.run(last_id()) == "id: 700"


@pytest.mark.django_db(transaction=True)
def test_run_stream_ends_when_the_viewer_loses_the_permission(monkeypatch, default_runtime):
    """タスクの管理の権限を外された人には、開いたままの接続からもRunのログを送らない（authorization.md §7.1）"""
    from task.dispatch import start_task_run  # noqa: PLC0415
    from task.factories import TaskDefinitionFactory  # noqa: PLC0415
    from task.models import TaskScope  # noqa: PLC0415
    from user.factories import UserFactory, grant  # noqa: PLC0415
    from utils.permissions import AppPermission  # noqa: PLC0415

    monkeypatch.setattr(views, "KEEPALIVE_SECONDS", 0.2)
    monkeypatch.setattr(views, "RECHECK_SECONDS", 0.2)
    admin = grant(UserFactory.create(), AppPermission.MANAGE_SYSTEM_TASKS)
    task = TaskDefinitionFactory.create(scope=TaskScope.SYSTEM, created_by=admin)
    run = start_task_run(task, inputs={}, actor=admin, launch=RunLaunch.MANUAL)
    request = RequestFactory().get(f"/api/app/v1/runs/{run.uid}/stream/")

    async def auser():
        return admin

    request.auser = auser

    async def read() -> list[str]:
        response = await run_stream(request, run.uid)
        assert isinstance(response, StreamingHttpResponse)
        stream: Any = response.streaming_content.__aiter__()  # type: ignore[union-attr]
        chunks: list[str] = []
        try:
            chunks.append(await asyncio.wait_for(stream.__anext__(), timeout=5))
            await sync_to_async(admin.user_permissions.clear)()
            while True:
                chunks.append(await asyncio.wait_for(stream.__anext__(), timeout=5))
        except StopAsyncIteration:
            return chunks
        finally:
            await stream.aclose()
            await get_listener().close()

    chunks = [c if isinstance(c, str) else c.decode() for c in asyncio.run(read())]
    assert chunks[0].startswith("retry:")
    assert (
        chunks[1:] == []
    )  # 権限を外した後は、記録済みのイベントも送らずに接続を終える  # 権限を外した後のイベントは送らない
