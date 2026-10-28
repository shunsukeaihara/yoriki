"""配信のOutboxと送信（design.md §4.3〜§4.5）。Runの終わりで配信を作る、送る内容、署名、再試行、届いたか分からない
とき、送る直前の確かめ直し、持ち時間の切れ"""

import base64
import hashlib
import hmac
import json
import socket
from datetime import timedelta
from unittest import mock

import httpx
import pytest
from django.utils import timezone

from delivery.adapters import WebhookAdapter
from delivery.factories import DestinationFactory
from delivery.models import AttemptOutcome, Delivery, DeliveryStatus, Destination, DestinationScope
from delivery.tasks import BACKOFF_SECONDS, MAX_ATTEMPTS, reconcile_deliveries, send_delivery
from llm.factories import LLMConnectionFactory, ModelProfileFactory
from llm.models import ProviderKind
from run.models import RunEndReason, RunLaunch, RunStatus
from task.dispatch import start_task_run
from task.factories import TaskDefinitionFactory
from task.models import TaskScope
from user.factories import UserFactory, grant
from utils.permissions import AppPermission

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("default_runtime")]

PUBLIC_ADDRESS = "93.184.216.34"


@pytest.fixture(autouse=True)
def fixture_public_dns():
    info = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_ADDRESS, 443))]
    with mock.patch("delivery.network.socket.getaddrinfo", return_value=info) as getaddrinfo:
        yield getaddrinfo


class Receiver:
    """送り先の代わり。受け取った要求を残し、決めた応答を返す"""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.responses: list[httpx.Response | Exception] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses.pop(0) if self.responses else httpx.Response(200)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture(name="receiver")
def fixture_receiver():
    receiver = Receiver()
    with mock.patch.object(WebhookAdapter, "transport", httpx.MockTransport(receiver)):
        yield receiver


@pytest.fixture(name="manager")
def fixture_manager(db):
    return grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)


def _run(manager, *, events=("run.completed", "run.failed"), content="status_and_result", destination=None, **task):
    task_def = TaskDefinitionFactory.create(
        scope=TaskScope.COMPANY,
        revision__destination=destination or DestinationFactory.create(),
        revision__notify_events=list(events),
        revision__notify_content=content,
        **task,
    )
    return start_task_run(task_def, inputs={}, actor=manager, launch=RunLaunch.MANUAL)


def _finish(run, status=RunStatus.COMPLETED, **kwargs):
    run.set_status(RunStatus.RUNNING)
    if status == RunStatus.COMPLETED:
        run.result = {"ok": True, "amount": 12000}
        run.save(update_fields=["result"])
    run.set_status(status, **kwargs)
    return Delivery.objects.filter(run=run).first()


# --- 配信を作る ---


def test_a_finished_run_queues_a_delivery_in_the_same_transaction(manager, django_capture_on_commit_callbacks):
    run = _run(manager)
    with (
        mock.patch("delivery.outbox.async_task") as async_task,
        django_capture_on_commit_callbacks(execute=True) as callbacks,
    ):
        delivery = _finish(run)
    assert delivery is not None
    assert delivery.status == DeliveryStatus.PENDING
    assert delivery.destination_revision == run.destination_revision
    # commitした後にQ2へ配信のIDだけを渡す
    assert len(callbacks) >= 1
    async_task.assert_called_once_with("delivery.tasks.send_delivery", delivery.pk, task_name=f"delivery-{delivery.pk}")
    payload = delivery.payload
    assert payload["event_id"] == f"{run.uid}.{delivery.run_event.sequence}"
    assert payload["event_type"] == "run.completed"
    assert payload["run"]["id"] == run.uid
    assert payload["run"]["status"] == "completed"
    assert payload["run"]["failure_code"] is None
    assert payload["task"] == {"id": run.task.uid, "name": run.task.name, "version": 1}
    assert payload["result"] == {"ok": True, "amount": 12000}


def test_a_failure_carries_only_the_failure_code(manager):
    delivery = _finish(
        _run(manager), RunStatus.FAILED, end_reason=RunEndReason.STEP_LIMIT, error="社内の情報を含む理由"
    )
    assert delivery.payload["run"]["failure_code"] == "step_limit"
    assert delivery.payload["result"] is None
    assert "社内の情報" not in json.dumps(delivery.payload, ensure_ascii=False)


def test_only_the_chosen_events_and_content_are_sent(manager):
    assert _finish(_run(manager, events=["run.failed"])) is None
    delivery = _finish(_run(manager, content="status"))
    assert "result" not in delivery.payload
    # 通知のないタスク
    task = TaskDefinitionFactory.create(scope=TaskScope.COMPANY)
    run = start_task_run(task, inputs={}, actor=manager, launch=RunLaunch.MANUAL)
    assert run.destination_revision is None
    assert _finish(run) is None


# --- 送る ---


def test_sending_signs_the_body_and_connects_to_the_checked_address(manager, receiver):
    delivery = _finish(_run(manager))
    send_delivery(delivery.pk)
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.DELIVERED
    assert delivery.delivered is not None
    assert delivery.attempts.get().outcome == AttemptOutcome.ACCEPTED

    request = receiver.requests[0]
    # 名前を引いて確かめたアドレスへつなぎ、Hostは元のホスト名
    assert request.url.host == PUBLIC_ADDRESS
    assert request.headers["Host"] == "hooks.example.com"
    assert request.extensions["sni_hostname"] == "hooks.example.com"
    body = request.content
    assert json.loads(body) == delivery.payload
    # Standard Webhooksの署名を、出口の鍵で確かめられる
    secret = Destination.objects.get(pk=delivery.destination_revision.destination_id).signing_secret.reveal()
    key = base64.b64decode(secret.removeprefix("whsec_"))
    message = f"{request.headers['webhook-id']}.{request.headers['webhook-timestamp']}.".encode() + body
    expected = "v1," + base64.b64encode(hmac.new(key, message, hashlib.sha256).digest()).decode()
    assert request.headers["webhook-signature"] == expected
    assert request.headers["webhook-id"] == delivery.payload["event_id"]

    # 送り終えたものは送らない
    send_delivery(delivery.pk)
    assert len(receiver.requests) == 1


def test_temporary_failures_are_retried_with_backoff_then_give_up(manager, receiver):
    delivery = _finish(_run(manager))
    receiver.responses = [httpx.Response(503, headers={"Retry-After": "7200"}), httpx.ConnectError("refused")]
    send_delivery(delivery.pk)
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.RETRY_WAIT
    # 送り先の Retry-After が長ければそれに従う
    assert delivery.next_attempt - timezone.now() > timedelta(seconds=7000)
    assert delivery.attempts.get().http_status == 503

    # 時刻の前は送らない
    send_delivery(delivery.pk)
    assert len(receiver.requests) == 1
    Delivery.objects.filter(pk=delivery.pk).update(next_attempt=timezone.now())
    send_delivery(delivery.pk)
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.RETRY_WAIT
    assert timedelta(seconds=BACKOFF_SECONDS[1] - 5) < delivery.next_attempt - timezone.now()

    receiver.responses = [httpx.Response(500)] * MAX_ATTEMPTS
    for _ in range(MAX_ATTEMPTS - 2):
        Delivery.objects.filter(pk=delivery.pk).update(next_attempt=timezone.now())
        send_delivery(delivery.pk)
    delivery.refresh_from_db()
    assert (delivery.status, delivery.attempt_count) == (DeliveryStatus.FAILED, MAX_ATTEMPTS)
    assert "HTTP 500" in delivery.last_error


@pytest.mark.parametrize(
    ("response", "status"),
    [
        (httpx.Response(400), DeliveryStatus.FAILED),
        (httpx.Response(302, headers={"Location": "http://169.254.169.254/"}), DeliveryStatus.FAILED),
        (httpx.ReadTimeout("timed out"), DeliveryStatus.UNKNOWN),
        (httpx.RemoteProtocolError("closed"), DeliveryStatus.UNKNOWN),
    ],
)
def test_permanent_failures_and_unknown_outcomes_are_not_retried(manager, receiver, response, status):
    delivery = _finish(_run(manager))
    receiver.responses = [response]
    send_delivery(delivery.pk)
    delivery.refresh_from_db()
    assert delivery.status == status
    assert delivery.next_attempt is None
    assert len(receiver.requests) == 1  # リダイレクトは辿らない


def test_an_address_changed_to_internal_is_not_sent(manager, receiver, fixture_public_dns):
    delivery = _finish(_run(manager))
    fixture_public_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443))]
    send_delivery(delivery.pk)
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.FAILED
    assert "社内のアドレス" in delivery.last_error
    assert receiver.requests == []


# --- 送る直前の確かめ直し（authorization.md §7.2） ---


def test_a_suspended_or_deleted_destination_is_not_sent(manager, receiver):
    for change in ({"suspended": True}, {"deleted": True}):
        delivery = _finish(_run(manager))
        Destination.objects.filter(pk=delivery.destination_revision.destination_id).update(**change)
        send_delivery(delivery.pk)
        delivery.refresh_from_db()
        assert delivery.status == DeliveryStatus.CANCELLED
        assert "出口" in delivery.last_error
    assert receiver.requests == []


def test_a_sensitive_result_is_not_sent_after_the_flag_is_removed(manager, receiver):
    local = ModelProfileFactory.create(
        version__connection=LLMConnectionFactory.create(kind=ProviderKind.LOCAL), version__supports_tools=True
    )
    destination = DestinationFactory.create(revision__allows_sensitive_results=True)
    run = _run(manager, destination=destination, revision__handles_sensitive_data=True, revision__model_profile=local)
    delivery = _finish(run)
    destination.revise(
        actor=manager, expected_version=1, url="https://hooks.example.com/yoriki", allows_sensitive_results=False
    )
    send_delivery(delivery.pk)
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.CANCELLED
    assert "機密" in delivery.last_error
    assert receiver.requests == []


def test_a_destination_moved_out_of_the_task_scope_is_not_sent(manager, receiver):
    delivery = _finish(_run(manager))
    Destination.objects.filter(pk=delivery.destination_revision.destination_id).update(scope=DestinationScope.SYSTEM)
    send_delivery(delivery.pk)
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.CANCELLED


# --- 送り直しの取りこぼし ---


def test_reconcile_sends_left_over_deliveries_and_expires_stuck_ones(manager, receiver):
    left = _finish(_run(manager))
    due = _finish(_run(manager))
    stuck = _finish(_run(manager))
    now = timezone.now()
    Delivery.objects.filter(pk=left.pk).update(created=now - timedelta(minutes=5))
    Delivery.objects.filter(pk=due.pk).update(status=DeliveryStatus.RETRY_WAIT, next_attempt=now)
    Delivery.objects.filter(pk=stuck.pk).update(
        status=DeliveryStatus.SENDING, attempt_count=1, lease_expires=now - timedelta(seconds=1)
    )
    stuck.attempts.create(number=1)
    with mock.patch("delivery.tasks.send_later") as send_later:
        reconcile_deliveries()
    assert sorted(c.args[0] for c in send_later.call_args_list) == sorted([left.pk, due.pk])
    stuck.refresh_from_db()
    # 送信の途中でWorkerが止まった。届いたか分からないので、自動では送り直さない
    assert stuck.status == DeliveryStatus.UNKNOWN
    assert stuck.attempts.get().outcome == AttemptOutcome.UNKNOWN


def test_a_late_result_after_the_lease_expired_is_only_recorded(manager, receiver):
    delivery = _finish(_run(manager))

    def expire_while_sending(request):
        Delivery.objects.filter(pk=delivery.pk).update(status=DeliveryStatus.UNKNOWN, lease_expires=None)
        return httpx.Response(200)

    receiver.responses = []
    with mock.patch.object(WebhookAdapter, "transport", httpx.MockTransport(expire_while_sending)):
        send_delivery(delivery.pk)
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.UNKNOWN
    assert delivery.attempts.get().outcome == AttemptOutcome.ACCEPTED
