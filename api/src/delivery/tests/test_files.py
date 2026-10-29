"""出口へ渡す成果物（design.md §4.4、authorization.md §7.1）。業務結果のファイルの項目で指したものだけを渡し、URLは
送るたびに発行する。取得のたびに期限・回数・出口とタスクの状態を確かめ、汎用のWebhookでは出口の鍵も求める"""

import io
import json
import socket
from datetime import timedelta
from unittest import mock

import httpx
import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from artifact.models import Artifact, BlobObject
from artifact.storage import LocalStorage
from artifact.tests.test_api import body_of
from delivery.adapters import WebhookAdapter
from delivery.factories import DestinationFactory
from delivery.models import FILE_LINK_MAX_FETCHES, Delivery, DeliveryStatus, DestinationKind, FileFetch, FileLink
from delivery.tasks import send_delivery
from run.models import RunLaunch, RunStatus
from task.dispatch import start_task_run
from task.factories import TaskDefinitionFactory
from task.models import TaskScope
from user.factories import UserFactory, grant
from utils.permissions import AppPermission

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("default_runtime")]

SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "report": {"type": "string", "format": "file"},
        "attachments": {"type": "array", "items": {"type": "string", "format": "file"}},
    },
    "required": ["ok"],
}
ZAPIER_URL = "https://hooks.zapier.com/hooks/catch/1/abc/"


@pytest.fixture(autouse=True)
def fixture_public_dns(settings):
    settings.YORIKI_PUBLIC_URL = "https://yoriki.example.co.jp"
    info = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    with mock.patch("delivery.network.socket.getaddrinfo", return_value=info):
        yield


@pytest.fixture(name="sent")
def fixture_sent():
    sent: list[httpx.Request] = []

    def receive(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200)

    with mock.patch.object(WebhookAdapter, "transport", httpx.MockTransport(receive)):
        yield sent


@pytest.fixture(name="manager")
def fixture_manager(db):
    return grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)


def _delivery(manager, *, kind=DestinationKind.WEBHOOK, result=None, outputs=None, content="status_and_result"):
    url = ZAPIER_URL if kind == DestinationKind.ZAPIER else "https://hooks.example.com/yoriki"
    task = TaskDefinitionFactory.create(
        scope=TaskScope.COMPANY,
        revision__destination=DestinationFactory.create(kind=kind, revision__url=url),
        revision__notify_events=["run.completed"],
        revision__notify_content=content,
        revision__output_schema=SCHEMA,
    )
    run = start_task_run(task, inputs={}, actor=manager, launch=RunLaunch.MANUAL)
    run.set_status(RunStatus.RUNNING)
    for path, body in (outputs or {"report.xlsx": b"xlsx", "a/b.csv": b"csv", "unused.txt": b"x"}).items():
        Artifact.create_output(run, path, BlobObject.put(io.BytesIO(body)))
    run.result = result or {"ok": True, "report": "/workspace/output/report.xlsx", "attachments": ["a/b.csv"]}
    run.save(update_fields=["result"])
    run.set_status(RunStatus.COMPLETED)
    return Delivery.objects.get(run=run)


def _sent_files(sent):
    return json.loads(sent[-1].content)["files"]


def _token(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[1]


def _bearer(delivery: Delivery) -> str:
    return f"Bearer {delivery.destination_revision.destination.signing_secret.reveal()}"


def test_only_the_files_in_the_result_are_delivered(manager):
    delivery = _delivery(manager)
    files = delivery.payload["files"]
    assert [(f["field"], f["name"]) for f in files] == [("report", "report.xlsx"), ("attachments", "b.csv")]
    report = Artifact.objects.get(path="report.xlsx")
    assert files[0] == {
        "field": "report",
        "id": report.uid,
        "name": "report.xlsx",
        "content_type": report.content_type,
        "size": 4,
        "sha256": report.sha256,
    }
    # 保存する内容にはURLを入れない（送るたびに発行する）
    assert "url" not in json.dumps(delivery.payload)


def test_files_are_left_out_without_the_result(manager):
    assert "files" not in _delivery(manager, content="status").payload
    # 回収していないファイル（symlink等）は渡さない
    delivery = _delivery(manager, result={"ok": True, "report": "missing.xlsx"}, outputs={})
    assert delivery.payload["files"] == []


def test_each_attempt_issues_new_links(manager, sent):
    delivery = _delivery(manager)
    send_delivery(delivery.pk)
    first = _sent_files(sent)
    assert first[0]["url"].startswith("https://yoriki.example.co.jp/api/files/")
    assert first[0]["expires_at"]
    Delivery.objects.filter(pk=delivery.pk).update(status=DeliveryStatus.FAILED)
    Delivery.objects.get(pk=delivery.pk).retry()
    send_delivery(delivery.pk)
    second = _sent_files(sent)
    assert second[0]["url"] != first[0]["url"]
    # event_id と業務結果は変えない
    assert json.loads(sent[0].content)["event_id"] == json.loads(sent[1].content)["event_id"]
    assert FileLink.objects.filter(attempt__delivery=delivery).count() == 4
    # tokenはハッシュだけを保存する
    token = _token(second[0]["url"])
    assert not FileLink.objects.filter(token_hash=token).exists()
    assert FileLink.objects.filter(token_hash=FileLink.hash_token(token)).exists()


def test_no_public_url_fails_the_delivery(manager, sent, settings):
    settings.YORIKI_PUBLIC_URL = ""
    delivery = _delivery(manager)
    send_delivery(delivery.pk)
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.FAILED
    assert "YORIKI_PUBLIC_URL" in delivery.last_error
    assert sent == []


def test_a_zapier_link_gives_the_file_without_login(manager, sent):
    delivery = _delivery(manager, kind=DestinationKind.ZAPIER)
    send_delivery(delivery.pk)
    url = _sent_files(sent)[0]["url"]
    res = APIClient().get(f"/api/files/{_token(url)}/", HTTP_USER_AGENT="Zapier", REMOTE_ADDR="203.0.113.5")
    assert res.status_code == 200
    assert body_of(res) == b"xlsx"
    assert res["Content-Disposition"].startswith("attachment")
    fetch = FileFetch.objects.get()
    assert (fetch.served, fetch.ip_address, fetch.user_agent) == (True, "203.0.113.5", "Zapier")


def test_a_webhook_link_needs_the_destination_key(manager, sent):
    delivery = _delivery(manager)
    send_delivery(delivery.pk)
    token = _token(_sent_files(sent)[0]["url"])
    res = APIClient().get(f"/api/files/{token}/")
    assert res.status_code == 401
    assert res["WWW-Authenticate"] == "Bearer"
    assert APIClient().get(f"/api/files/{token}/", HTTP_AUTHORIZATION="Bearer whsec_wrong").status_code == 401
    assert APIClient().get(f"/api/files/{token}/", HTTP_AUTHORIZATION=_bearer(delivery)).status_code == 200
    # 鍵のない取得は回数に数えない
    assert FileLink.objects.get(token_hash=FileLink.hash_token(token)).fetch_count == 1


def test_a_link_has_a_limited_number_of_fetches(manager, sent):
    delivery = _delivery(manager, kind=DestinationKind.ZAPIER)
    send_delivery(delivery.pk)
    token = _token(_sent_files(sent)[0]["url"])
    for _ in range(FILE_LINK_MAX_FETCHES):
        assert APIClient().get(f"/api/files/{token}/").status_code == 200
    assert APIClient().get(f"/api/files/{token}/").status_code == 404
    assert FileFetch.objects.filter(served=False).get().refusal


def test_expired_unknown_or_deleted(manager, sent):
    delivery = _delivery(manager, kind=DestinationKind.ZAPIER)
    send_delivery(delivery.pk)
    report, attachment = (_token(f["url"]) for f in _sent_files(sent))
    assert APIClient().get("/api/files/unknown/").status_code == 404
    FileLink.objects.filter(token_hash=FileLink.hash_token(report)).update(
        expires=timezone.now() - timedelta(seconds=1)
    )
    assert APIClient().get(f"/api/files/{report}/").status_code == 404
    Artifact.objects.filter(path="a/b.csv").update(deleted=True)
    assert APIClient().get(f"/api/files/{attachment}/").status_code == 404


@pytest.mark.parametrize("stop", ["destination", "task", "sensitive"])
def test_stopping_the_destination_or_the_task_revokes_unused_links(manager, sent, stop):
    delivery = _delivery(manager, kind=DestinationKind.ZAPIER)
    send_delivery(delivery.pk)
    token = _token(_sent_files(sent)[0]["url"])
    destination = delivery.destination_revision.destination
    if stop == "destination":
        destination.suspended = True
        destination.save(update_fields=["suspended"])
    elif stop == "task":
        delivery.run.task.suspended = True
        delivery.run.task.save(update_fields=["suspended"])
    else:
        type(delivery.run).objects.filter(pk=delivery.run_id).update(handles_sensitive_data=True)
    assert APIClient().get(f"/api/files/{token}/").status_code == 404


def test_s3_redirects_to_a_short_signed_url(manager, sent):
    delivery = _delivery(manager, kind=DestinationKind.ZAPIER)
    send_delivery(delivery.pk)
    token = _token(_sent_files(sent)[0]["url"])
    with mock.patch.object(LocalStorage, "signed_url", return_value="https://s3.example.com/signed") as signed:
        res = APIClient().get(f"/api/files/{token}/")
    assert res.status_code == 302
    assert res["Location"] == "https://s3.example.com/signed"
    assert res["Cache-Control"] == "no-store"
    assert signed.call_args.kwargs["name"] == "report.xlsx"
