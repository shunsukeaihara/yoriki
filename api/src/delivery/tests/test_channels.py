"""対話の出口（Slack。design.md §4.6・§4.7）。スレッドでの質問・回答・再開・完了の報告、受信の認証と照合、ボタン、
Webとの競合、スレッドの親メッセージを送ったか分からないとき"""

import hashlib
import hmac
import json
import time
from datetime import timedelta
from typing import Any
from unittest import mock
from urllib.parse import urlencode

import httpx
import pytest
from django.utils import timezone

from delivery import cleanup
from delivery.channels import inbound
from delivery.channels.slack import SlackAdapter
from delivery.factories import ChannelConnectionFactory, DestinationFactory, channel_destination
from delivery.models import (
    ChannelConnection,
    ChannelEvent,
    ChannelMessageReceipt,
    Delivery,
    DeliveryStatus,
    ExternalThreadBinding,
    ReceiptResult,
    ThreadStatus,
)
from delivery.tasks import send_delivery
from harness.tests.fakes import FakeAdapter, calls, reply
from harness.tests.test_connectors import execute
from llm.factories import ModelProfileFactory
from run.models import (
    ClarificationRequest,
    ClarificationStatus,
    Run,
    RunEvent,
    RunEventKind,
    RunLaunch,
    RunStatus,
)
from task.dispatch import start_task_run
from task.factories import TaskDefinitionFactory
from task.models import TaskScope
from task.serializers import TaskRunSerializer
from user.factories import UserFactory, grant
from utils.permissions import AppPermission

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("default_runtime")]

SIGNING_SECRET = "test-signing-secret"
QUESTION = {
    "header": "取引先",
    "question": "どちらの取引先として扱いますか？",
    "options": [{"label": "A社"}, {"label": "B社", "description": "子会社として扱う"}],
}


class FakeSlack:
    """SlackのWeb APIの代わり。呼ばれたメソッドと本文を残し、決めた応答を返す"""

    def __init__(self):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses: dict[str, list[httpx.Response | Exception]] = {}
        self.ts = 1000

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content) if request.content else dict(request.url.params)
        self.calls.append((method, body))
        queued = self.responses.get(method)
        if queued:
            response = queued.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        return httpx.Response(200, json={"ok": True, **self._data(method, body)})

    def _data(self, method: str, body: dict[str, Any]) -> dict[str, Any]:
        if method == "chat.postMessage":
            self.ts += 1
            return {"ts": f"{self.ts}.0001", "channel": body["channel"]}
        if method == "chat.getPermalink":
            ts = body["message_ts"].replace(".", "")
            return {"permalink": f"https://test.slack.com/archives/C001/p{ts}"}
        fixed: dict[str, dict[str, Any]] = {
            "users.info": {"user": {"name": "sato", "profile": {"display_name": "佐藤"}}},
            "conversations.info": {"channel": {"id": body.get("channel"), "name": "経理", "is_member": True}},
            "auth.test": {"team_id": "T001", "team": "テスト", "user_id": "UBOT"},
        }
        return fixed.get(method, {})

    def posted(self) -> list[dict[str, Any]]:
        return [body for method, body in self.calls if method == "chat.postMessage"]

    def updated(self) -> list[dict[str, Any]]:
        return [body for method, body in self.calls if method == "chat.update"]


@pytest.fixture(name="slack")
def fixture_slack():
    slack = FakeSlack()
    with mock.patch.object(SlackAdapter, "transport", httpx.MockTransport(slack)):
        yield slack


@pytest.fixture(name="manager")
def fixture_manager(db):
    return grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)


@pytest.fixture(autouse=True)
def fixture_no_q():
    # 配信と受信の処理はテストの中で呼ぶ
    with mock.patch("delivery.outbox.async_task"), mock.patch("delivery.channels.inbound.async_task"):
        yield


def _task(**revision):
    return TaskDefinitionFactory.create(
        scope=TaskScope.COMPANY,
        revision__interaction_destination=channel_destination(),
        revision__model_profile=ModelProfileFactory.create(version__supports_tools=True),
        **{f"revision__{k}": v for k, v in revision.items()},
    )


def _send_all(run):
    for delivery in Delivery.objects.filter(run=run, status=DeliveryStatus.PENDING).order_by("pk"):
        send_delivery(delivery.pk)


def _signed(body: bytes, content_type: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    digest = hmac.new(SIGNING_SECRET.encode(), b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256).hexdigest()
    return {
        "content_type": content_type,
        "HTTP_X_SLACK_REQUEST_TIMESTAMP": timestamp,
        "HTTP_X_SLACK_SIGNATURE": f"v0={digest}",
    }


def _post_event(client, connection, event: dict[str, Any], event_id: str = "Ev1"):
    body = json.dumps({"type": "event_callback", "team_id": "T001", "event_id": event_id, "event": event}).encode()
    url = f"/api/hooks/channels/slack/{connection.uid}/"
    return client.post(url, data=body, **_signed(body, "application/json"))


def _reply(client, connection, thread: ExternalThreadBinding, text: str, *, user="U123", event_id="Ev1"):
    event = {
        "type": "message",
        "channel": thread.channel_id,
        "user": user,
        "text": text,
        "ts": "2000.0001",
        "thread_ts": thread.thread_ref,
    }
    return _post_event(client, connection, event, event_id)


def _press(client, thread, message_ts: str, action_id: str, value: str, *, user="U123", action_ts="1"):
    payload = {
        "type": "block_actions",
        "team": {"id": "T001"},
        "user": {"id": user, "name": "sato"},
        "channel": {"id": thread.channel_id},
        "container": {"message_ts": message_ts},
        "message": {"ts": message_ts, "thread_ts": thread.thread_ref},
        "actions": [{"action_id": action_id, "value": value, "action_ts": action_ts}],
    }
    body = urlencode({"payload": json.dumps(payload)}).encode()
    url = f"/api/hooks/channels/slack/{thread.connection.uid}/"
    return client.post(url, data=body, **_signed(body, "application/x-www-form-urlencoded"))


def _process():
    for pk in ChannelMessageReceipt.objects.filter(result=ReceiptResult.RECEIVED).values_list("pk", flat=True):
        inbound.process_receipt(pk)


def _waiting_run(manager, slack, questions=(QUESTION,)):
    """ask_user で確認待ちにして、質問をスレッドへ送ったRun"""
    run = start_task_run(_task(), inputs={}, actor=manager, launch=RunLaunch.MANUAL)
    run = execute(run, FakeAdapter([calls(("q1", "ask_user", {"questions": list(questions)}))]))
    assert run.status == RunStatus.WAITING_USER, run.error
    _send_all(run)
    return run


# --- 一通りの流れ（design.md §12 の受入条件：質問・回答受付・再質問・完了を同じスレッドで） ---


def test_question_answer_and_report_in_the_same_thread(client, manager, slack, settings):
    settings.YORIKI_PUBLIC_URL = "https://yoriki.example.com"
    run = start_task_run(_task(), inputs={}, actor=manager, launch=RunLaunch.MANUAL)
    assert run.is_interactive and run.has_channel
    adapter = FakeAdapter([calls(("q1", "ask_user", {"questions": [QUESTION]}))])
    run = execute(run, adapter)
    # 対話の出口のあるタスクのRunでは ask_user を出し、Skill・タスクの編集の確認は出さない
    names = {t.name for t in adapter.requests[0].tools}
    assert "ask_user" in names
    assert not names & {"request_skill_edit", "request_task_edit"}
    first = adapter.requests[0].messages
    assert any("People in the chat channel" in m.text for m in first)
    assert any('<mode responder="true">task</mode>' in m.text for m in first)
    assert run.status == RunStatus.WAITING_USER

    question = Delivery.objects.get(run=run, event_type=ChannelEvent.QUESTION)
    _send_all(run)
    question.refresh_from_db()
    assert question.status == DeliveryStatus.DELIVERED
    # 親メッセージを投稿してから、そのスレッドへ質問を投稿する
    parent, posted = slack.posted()
    assert "thread_ts" not in parent and parent["channel"] == "C001"
    thread = ExternalThreadBinding.objects.get(run=run)
    assert thread.status == ThreadStatus.CREATED
    assert posted["thread_ts"] == thread.thread_ref
    assert question.external_ref == f"{slack.ts}.0001"
    blocks = json.dumps(posted["blocks"], ensure_ascii=False)
    assert "どちらの取引先" in blocks and "子会社として扱う" in blocks
    assert "yoriki_select_0_1" in blocks
    assert "yoriki_submit" not in blocks  # 1問で単一選択はボタンで確定する

    # スレッドへの普通の返信で回答する
    connection = thread.connection
    response = _reply(client, connection, thread, "B社でお願いします")
    assert response.status_code == 200
    _process()
    receipt = ChannelMessageReceipt.objects.get()
    assert receipt.result == ReceiptResult.APPLIED, receipt.reason
    assert receipt.user_name == "佐藤"
    clarification = ClarificationRequest.objects.get(run=run)
    assert clarification.status == ClarificationStatus.ANSWERED
    assert clarification.answered_by is None
    assert clarification.answered_external == {"provider": "slack", "user_id": "U123", "name": "佐藤"}
    assert clarification.comment == "B社でお願いします"
    run.refresh_from_db()
    assert run.status == RunStatus.QUEUED

    # 質問のメッセージを回答の形に書き換える（ボタンを消す）
    _send_all(run)
    update = slack.updated()[-1]
    assert update["ts"] == question.external_ref
    text = json.dumps(update["blocks"], ensure_ascii=False)
    assert "佐藤さんがSlackで回答しました" in text and "yoriki_select" not in text

    # 再開したRunがもう一度尋ね、同じスレッドへ投稿する
    run = execute(run, FakeAdapter([calls(("q2", "ask_user", {"questions": [QUESTION]}))]))
    assert run.status == RunStatus.WAITING_USER
    _send_all(run)
    assert slack.posted()[-1]["thread_ts"] == thread.thread_ref
    assert ExternalThreadBinding.objects.filter(run=run).count() == 1

    _reply(client, connection, thread, "A社です", event_id="Ev2")
    _process()
    run.refresh_from_db()
    adapter = FakeAdapter([reply("A社として処理しました。差異はありません。")])
    run = execute(run, adapter)
    assert run.status == RunStatus.COMPLETED, run.error
    assert any("佐藤 answered" in m.text for m in adapter.requests[0].messages if m.role == "tool")

    # 完了をスレッドへ報告する（状態、最後の応答、Runの画面へのリンク）
    _send_all(run)
    report = slack.posted()[-1]
    assert report["thread_ts"] == thread.thread_ref
    text = json.dumps(report["blocks"], ensure_ascii=False)
    assert "完了しました" in text and "差異はありません" in text
    assert run.task is not None
    assert f"/tasks/{run.task.uid}/runs/{run.uid}" in text


def test_report_creates_the_thread_when_there_was_no_question(manager, slack):
    run = start_task_run(_task(), inputs={}, actor=manager, launch=RunLaunch.MANUAL)
    run = execute(run, FakeAdapter([reply("終わりました")]))
    assert run.status == RunStatus.COMPLETED
    _send_all(run)
    parent, report = slack.posted()
    assert "thread_ts" not in parent
    assert report["thread_ts"] == ExternalThreadBinding.objects.get(run=run).thread_ref
    assert "終わりました" in json.dumps(report["blocks"], ensure_ascii=False)


# --- Runの画面のスレッド（design.md §4.7） ---


def test_run_page_shows_the_thread(api_client, manager, slack):
    run = start_task_run(_task(), inputs={}, actor=manager, launch=RunLaunch.MANUAL)
    assert run.task is not None
    url = f"/api/app/v1/tasks/{run.task.uid}/runs/{run.uid}/"
    api_client.force_authenticate(manager)
    thread = api_client.get(url).json()["task_info"]["thread"]
    # スレッドは最初の質問（なければ報告）のときに作る
    assert run.interaction_revision is not None
    assert thread == {
        "destination_name": run.interaction_revision.destination.name,
        "channel_name": "#経理",
        "extension": "yoriki.channel.slack",
        "status": "none",
        "url": "",
    }

    run = execute(run, FakeAdapter([calls(("q1", "ask_user", {"questions": [QUESTION]}))]))
    _send_all(run)
    binding = ExternalThreadBinding.objects.get(run=run)
    permalink = f"https://test.slack.com/archives/C001/p{binding.thread_ref.replace('.', '')}"
    assert binding.thread_url == permalink
    thread = api_client.get(url).json()["task_info"]["thread"]
    assert thread["status"] == "created" and thread["url"] == permalink


def test_thread_without_a_link_is_still_used(manager, slack):
    # リンクを得られなくてもスレッドは作れているので、質問は送る
    slack.responses["chat.getPermalink"] = [httpx.Response(200, json={"ok": False, "error": "channel_not_found"})]
    run = _waiting_run(manager, slack)
    binding = ExternalThreadBinding.objects.get(run=run)
    assert binding.status == ThreadStatus.CREATED and binding.thread_url == ""
    question = Delivery.objects.get(run=run, event_type=ChannelEvent.QUESTION)
    assert question.status == DeliveryStatus.DELIVERED


def test_thread_is_shown_to_the_starter_who_cannot_see_the_logs(manager, slack):
    run = _waiting_run(manager, slack)
    run = Run.objects.get(pk=run.pk)
    # チャットから起動した本人と同じ（結果だけを見る）
    run.logs_visible = False  # type: ignore[attr-defined]
    data = TaskRunSerializer(run).data
    assert data["task_info"]["destination_name"] == ""
    assert data["task_info"]["thread"]["status"] == "created"
    assert data["task_info"]["thread"]["url"].startswith("https://test.slack.com/")


# --- ボタン ---


def test_single_question_is_answered_by_a_button(client, manager, slack):
    run = _waiting_run(manager, slack)
    thread = ExternalThreadBinding.objects.get(run=run)
    clarification = ClarificationRequest.objects.get(run=run)
    question_ts = Delivery.objects.get(run=run, event_type=ChannelEvent.QUESTION).external_ref
    _press(client, thread, question_ts, "yoriki_select_0_1", f"{clarification.uid}:0:1")
    _process()
    clarification.refresh_from_db()
    assert clarification.status == ClarificationStatus.ANSWERED
    assert clarification.answers == [{"selected": ["B社"], "text": ""}]


def test_multiple_questions_are_selected_then_submitted(client, manager, slack):
    second = {
        "header": "税区分",
        "question": "どの税区分ですか？",
        "options": [{"label": "課税"}, {"label": "非課税"}, {"label": "免税"}],
        "multi_select": True,
    }
    run = _waiting_run(manager, slack, questions=(QUESTION, second))
    thread = ExternalThreadBinding.objects.get(run=run)
    connection = thread.connection
    clarification = ClarificationRequest.objects.get(run=run)
    question_ts = Delivery.objects.get(run=run, event_type=ChannelEvent.QUESTION).external_ref
    assert "yoriki_submit" in json.dumps(slack.posted()[-1]["blocks"])

    # 何も選ばずに送ると、本人だけに知らせて受け付けない
    _press(client, thread, question_ts, "yoriki_submit", clarification.uid, action_ts="1")
    _process()
    assert slack.calls[-1][0] == "chat.postEphemeral"
    assert ClarificationRequest.objects.get(pk=clarification.pk).status == ClarificationStatus.OPEN

    value = clarification.uid
    _press(client, thread, question_ts, "yoriki_select_0_0", f"{value}:0:0", action_ts="2")
    _press(client, thread, question_ts, "yoriki_select_1_0", f"{value}:1:0", action_ts="3")
    _press(client, thread, question_ts, "yoriki_select_1_2", f"{value}:1:2", action_ts="4")
    _press(client, thread, question_ts, "yoriki_select_1_0", f"{value}:1:0", action_ts="5")  # 外す
    _process()
    thread.refresh_from_db()
    assert thread.selections[clarification.uid] == [["A社"], ["免税"]]
    # 選んだ状態をメッセージに出す
    assert "✓ 免税" in json.dumps(slack.updated()[-1]["blocks"], ensure_ascii=False)
    assert ClarificationRequest.objects.get(pk=clarification.pk).status == ClarificationStatus.OPEN

    # 返信の文は、選んだ分に補足として添えて確定する
    _reply(client, connection, thread, "請求書の注記も確認済みです")
    _process()
    clarification.refresh_from_db()
    assert clarification.status == ClarificationStatus.ANSWERED
    assert clarification.answers == [{"selected": ["A社"], "text": ""}, {"selected": ["免税"], "text": ""}]
    assert clarification.comment == "請求書の注記も確認済みです"
    thread.refresh_from_db()
    assert clarification.uid not in thread.selections


# --- 受け付けないもの ---


def test_late_and_unrelated_inputs_are_not_applied(client, manager, slack):
    run = _waiting_run(manager, slack)
    thread = ExternalThreadBinding.objects.get(run=run)
    connection = thread.connection
    clarification = ClarificationRequest.objects.get(run=run)
    question_ts = Delivery.objects.get(run=run, event_type=ChannelEvent.QUESTION).external_ref

    _reply(client, connection, thread, "A社", event_id="Ev1")
    _reply(client, connection, thread, "A社", event_id="Ev1")  # 再送は1回だけ記録する
    _reply(client, connection, thread, "やっぱりB社", user="U999", event_id="Ev2")  # 確定の後
    _press(client, thread, question_ts, "yoriki_select_0_1", f"{clarification.uid}:0:1", action_ts="9")
    _reply(client, connection, thread, "botの投稿", user="UBOT", event_id="Ev3")  # bot自身
    other = {"type": "message", "channel": "C001", "user": "U1", "text": "x", "ts": "3.0", "thread_ts": "9.0"}
    _post_event(client, connection, other, "Ev4")  # 知らないスレッド
    _process()
    results = dict(ChannelMessageReceipt.objects.values_list("dedup_key", "result"))
    assert results == {
        "event:Ev1": ReceiptResult.APPLIED,
        "event:Ev2": ReceiptResult.REJECTED,
        "action:9:U123:yoriki_select_0_1": ReceiptResult.REJECTED,
        "event:Ev3": ReceiptResult.IGNORED,
        "event:Ev4": ReceiptResult.UNMATCHED,
    }
    clarification.refresh_from_db()
    assert clarification.comment == "A社"
    # 受け付けなかった人には、その人だけに見える形で知らせる
    ephemeral = [body for method, body in slack.calls if method == "chat.postEphemeral"]
    assert {b["user"] for b in ephemeral} == {"U999", "U123"}
    # Runのスレッドの受信は、取り込まないものもRunに結び付けて残す。結び付かない受信は中身を残さない
    bot = ChannelMessageReceipt.objects.get(dedup_key="event:Ev3")
    assert (bot.run_id, bot.text) == (run.pk, "botの投稿")
    unrelated = ChannelMessageReceipt.objects.get(dedup_key="event:Ev4")
    assert (unrelated.run_id, unrelated.text, unrelated.user_name, unrelated.action) == (None, "", "", {})
    assert unrelated.user_id == "U1"


def test_suspended_connection_keeps_the_receipt_bound_to_the_run(client, manager, slack):
    run = _waiting_run(manager, slack)
    thread = ExternalThreadBinding.objects.get(run=run)
    _reply(client, thread.connection, thread, "A社", event_id="Ev1")
    ChannelConnection.objects.filter(pk=thread.connection_id).update(suspended=True)  # 受けた後、処理する前に停止
    _process()
    receipt = ChannelMessageReceipt.objects.get()
    assert (receipt.result, receipt.run_id, receipt.text) == (ReceiptResult.IGNORED, run.pk, "A社")


# --- スレッドからの停止（design.md §4.6） ---


def _parent_blocks(slack, thread) -> str:
    """親メッセージの今の見せ方（最後の書き換え、なければ投稿）"""
    updated = [b for b in slack.updated() if b["ts"] == thread.thread_ref]
    body = updated[-1] if updated else next(b for b in slack.posted() if "thread_ts" not in b)
    return json.dumps(body["blocks"], ensure_ascii=False)


def test_stop_from_the_thread(client, manager, slack):
    run = _waiting_run(manager, slack)
    thread = ExternalThreadBinding.objects.get(run=run)
    # 親メッセージに、確認のダイアログつきの「停止」のボタンを置く
    parent = json.loads(_parent_blocks(slack, thread))
    stop = next(e for b in parent if b["type"] == "actions" for e in b["elements"])
    assert stop["action_id"] == "yoriki_stop" and stop["style"] == "danger" and "confirm" in stop

    response = _press(client, thread, thread.thread_ref, "yoriki_stop", "stop", action_ts="5")
    assert response.status_code == 200
    _process()
    receipt = ChannelMessageReceipt.objects.get()
    assert (receipt.kind, receipt.result, receipt.run_id) == ("stop", ReceiptResult.STOPPED, run.pk), receipt.reason
    run.refresh_from_db()
    assert run.status == RunStatus.CANCELLED  # ジョブが動いていない確認待ちは、すぐに止まる
    assert ClarificationRequest.objects.get(run=run).status == ClarificationStatus.CANCELLED
    # 止めた人はチャットツールのユーザーとして記録する（Yorikiのユーザーには結び付けない）
    event = RunEvent.objects.filter(run=run, kind=RunEventKind.STATUS).order_by("-sequence").first()
    assert event is not None and event.actor_id is None
    assert event.data["external"] == {"provider": "slack", "user_id": "U123", "name": "佐藤"}
    assert "佐藤さんがスレッドから停止を依頼しました" in event.comment
    # スレッドに止めた人を知らせる
    notice = slack.posted()[-1]
    assert notice["thread_ts"] == thread.thread_ref and "佐藤さん" in json.dumps(notice["blocks"], ensure_ascii=False)

    # 報告を送ったら、親メッセージのボタンを消す。質問のボタンも閉じる
    _send_all(run)
    assert "停止しました" in json.dumps(slack.posted()[-1]["blocks"], ensure_ascii=False)
    assert "yoriki_stop" not in _parent_blocks(slack, thread)
    assert "yoriki_select" not in json.dumps(slack.updated()[-2]["blocks"], ensure_ascii=False)


def test_stop_is_refused_when_the_run_cannot_be_stopped(client, manager, slack):
    run = _waiting_run(manager, slack)
    thread = ExternalThreadBinding.objects.get(run=run)
    Run.objects.filter(pk=run.pk).update(status=RunStatus.CANCELLING)
    _press(client, thread, thread.thread_ref, "yoriki_stop", "stop", action_ts="1")
    _process()
    Run.objects.filter(pk=run.pk).update(status=RunStatus.COMPLETED)
    _press(client, thread, thread.thread_ref, "yoriki_stop", "stop", action_ts="2")
    _process()
    results = list(ChannelMessageReceipt.objects.order_by("pk").values_list("result", "reason"))
    assert results == [(ReceiptResult.REJECTED, "cancelling"), (ReceiptResult.REJECTED, "finished")]
    # 押した本人だけに知らせ、スレッドには書かない
    ephemeral = [body["text"] for method, body in slack.calls if method == "chat.postEphemeral"]
    assert ephemeral == ["停止はすでに依頼されています。", "この実行はもう終わっているので、停止できません。"]
    assert not any("停止を依頼しました" in json.dumps(b, ensure_ascii=False) for b in slack.posted())


def test_stop_is_not_taken_while_the_connection_is_suspended(client, manager, slack):
    run = _waiting_run(manager, slack)
    thread = ExternalThreadBinding.objects.get(run=run)
    _press(client, thread, thread.thread_ref, "yoriki_stop", "stop")
    ChannelConnection.objects.filter(pk=thread.connection_id).update(suspended=True)
    _process()
    assert ChannelMessageReceipt.objects.get().result == ReceiptResult.IGNORED
    run.refresh_from_db()
    assert run.status == RunStatus.WAITING_USER


def test_report_without_a_running_run_has_no_stop_button(manager, slack):
    """スレッドを報告のときに作るなら、Runはもう終わっているので停止のボタンを出さない"""
    run = start_task_run(_task(), inputs={}, actor=manager, launch=RunLaunch.MANUAL)
    run = execute(run, FakeAdapter([reply("終わりました")]))
    _send_all(run)
    parent = slack.posted()[0]
    assert "yoriki_stop" not in json.dumps(parent["blocks"])


# --- 受信の記録の清掃（design.md §8） ---


def test_cleanup_removes_only_old_unbound_receipts(client, manager, slack):
    run = _waiting_run(manager, slack)
    thread = ExternalThreadBinding.objects.get(run=run)
    connection = thread.connection
    _reply(client, connection, thread, "A社", event_id="Ev1")
    _reply(client, connection, thread, "botの投稿", user="UBOT", event_id="Ev2")
    other = {"type": "message", "channel": "C001", "user": "U1", "text": "x", "ts": "3.0", "thread_ts": "9.0"}
    _post_event(client, connection, other, "Ev3")
    _post_event(client, connection, {**other, "ts": "4.0"}, "Ev4")
    _process()
    _post_event(client, connection, {**other, "ts": "5.0"}, "Ev5")  # まだ処理していない
    old = timezone.now() - timedelta(days=cleanup.UNBOUND_RECEIPT_DAYS, minutes=1)
    ChannelMessageReceipt.objects.exclude(dedup_key="event:Ev4").update(created=old)

    assert cleanup.cleanup_receipts(dry_run=True) == 1
    assert ChannelMessageReceipt.objects.count() == 5
    assert cleanup.cleanup_receipts() == 1
    remaining = set(ChannelMessageReceipt.objects.values_list("dedup_key", flat=True))
    assert remaining == {"event:Ev1", "event:Ev2", "event:Ev4", "event:Ev5"}


def test_request_must_be_signed_and_url_verification_is_answered(client, slack):
    connection = ChannelConnectionFactory.create()
    url = f"/api/hooks/channels/slack/{connection.uid}/"
    body = json.dumps({"type": "url_verification", "challenge": "abc"}).encode()
    assert client.post(url, data=body, content_type="application/json").status_code == 401
    headers = _signed(body, "application/json")
    headers["HTTP_X_SLACK_SIGNATURE"] = "v0=" + "0" * 64
    assert client.post(url, data=body, **headers).status_code == 401
    old = _signed(body, "application/json")
    with mock.patch.object(SlackAdapter, "clock", staticmethod(lambda: time.time() + 600)):
        assert client.post(url, data=body, **old).status_code == 401
    response = client.post(url, data=body, **_signed(body, "application/json"))
    assert response.status_code == 200
    assert response.json() == {"challenge": "abc"}
    assert (
        client.post(
            f"/api/hooks/channels/teams/{connection.uid}/", data=body, **_signed(body, "application/json")
        ).status_code
        == 404
    )


# --- Webでの回答との競合 ---


def test_web_answer_updates_the_slack_message_and_late_slack_reply_is_refused(api_client, client, manager, slack):
    run = _waiting_run(manager, slack)
    thread = ExternalThreadBinding.objects.get(run=run)
    clarification = ClarificationRequest.objects.get(run=run)
    api_client.force_authenticate(manager)
    response = api_client.post(
        f"/api/app/v1/runs/{run.uid}/clarifications/{clarification.uid}/answer/",
        {"answers": [{"selected": ["A社"], "text": ""}], "comment": ""},
        format="json",
    )
    assert response.status_code == 200, response.json()
    assert response.json()["answered_via"] == "web"
    _send_all(run)
    text = json.dumps(slack.updated()[-1]["blocks"], ensure_ascii=False)
    assert "Webで回答しました" in text

    _reply(client, thread.connection, thread, "B社")
    _process()
    assert ChannelMessageReceipt.objects.get().result == ReceiptResult.REJECTED


def test_people_who_cannot_see_the_run_cannot_answer_on_the_web(api_client, manager, slack):
    run = _waiting_run(manager, slack)
    other = UserFactory.create()
    api_client.force_authenticate(other)
    clarification = ClarificationRequest.objects.get(run=run)
    url = f"/api/app/v1/runs/{run.uid}/clarifications/{clarification.uid}/answer/"
    assert api_client.post(url, {"comment": "A社"}, format="json").status_code == 404


# --- スレッドの親メッセージ ---


def test_unknown_parent_post_is_not_retried_automatically(manager, slack):
    slack.responses["chat.postMessage"] = [httpx.Response(500)]
    run = _waiting_run(manager, slack)
    thread = ExternalThreadBinding.objects.get(run=run)
    assert thread.status == ThreadStatus.UNKNOWN
    question = Delivery.objects.get(run=run, event_type=ChannelEvent.QUESTION)
    assert question.status == DeliveryStatus.UNKNOWN
    send_delivery(question.pk)
    assert len(slack.posted()) == 1  # 作り直さない
    # 人が確かめて送り直すと、作り直してよいことにする
    assert question.retry() == ""
    send_delivery(question.pk)
    thread.refresh_from_db()
    assert thread.status == ThreadStatus.CREATED
    assert len(slack.posted()) == 3


def test_suspended_destination_cancels_the_question(manager, slack):
    run = start_task_run(_task(), inputs={}, actor=manager, launch=RunLaunch.MANUAL)
    run = execute(run, FakeAdapter([calls(("q1", "ask_user", {"questions": [QUESTION]}))]))
    assert run.interaction_revision is not None
    destination = run.interaction_revision.destination
    destination.suspended = True
    destination.save(update_fields=["suspended"])
    _send_all(run)
    assert Delivery.objects.get(run=run).status == DeliveryStatus.CANCELLED
    assert slack.posted() == []


# --- タスクの定義 ---


def test_task_definition_rejects_a_channel_as_the_result_destination(api_client, manager, slack):
    api_client.force_authenticate(manager)
    channel = channel_destination()
    profile = ModelProfileFactory.create()
    base = {
        "scope": TaskScope.COMPANY,
        "name": "請求書",
        "prompt_template": "確認して",
        "model_profile": profile.uid,
    }
    res = api_client.post(
        "/api/app/v1/tasks/", {**base, "destination": channel.uid, "notify_events": ["run.completed"]}, format="json"
    )
    assert res.status_code == 400
    assert "対話の出口" in str(res.json()["destination"])
    webhook = DestinationFactory.create()
    res = api_client.post("/api/app/v1/tasks/", {**base, "interaction_destination": webhook.uid}, format="json")
    assert res.status_code == 400
    assert "チャネル" in str(res.json()["interaction_destination"])
    res = api_client.post("/api/app/v1/tasks/", {**base, "interaction_destination": channel.uid}, format="json")
    assert res.status_code == 201, res.json()
    assert res.json()["interaction_destination_info"]["is_channel"] is True
    choices = api_client.get("/api/app/v1/destinations/?scope=3&channel=true").json()["results"]
    assert [c["id"] for c in choices] == [channel.uid]


# --- 連携の接続と対話の出口の登録（連携の管理者） ---


@pytest.fixture(name="integrator")
def fixture_integrator(db):
    from django.contrib.auth.models import Permission  # noqa: PLC0415

    user = UserFactory.create()
    user.user_permissions.add(
        *Permission.objects.filter(
            content_type__app_label="delivery",
            codename__in=[
                "view_channelconnection",
                "add_channelconnection",
                "change_channelconnection",
                "delete_channelconnection",
                "view_destination",
                "add_destination",
                "change_destination",
            ],
        )
    )
    return UserFactory._meta.model.objects.get(pk=user.pk)


def test_connection_is_checked_and_secrets_are_not_returned(api_client, integrator, slack):
    api_client.force_authenticate(integrator)
    res = api_client.post(
        "/api/v1/channel_connections/",
        {"extension": "yoriki.channel.slack", "name": "社内Slack", "bot_token": "xoxb-1", "signing_secret": "s"},
        format="json",
    )
    assert res.status_code == 201, res.json()
    data = res.json()
    assert (data["workspace_id"], data["bot_user_id"]) == ("T001", "UBOT")
    assert "bot_token" not in data and "signing_secret" not in data
    assert data["receive_url"].endswith(f"/api/hooks/channels/slack/{data['id']}/")
    # 別のWorkspaceの資格情報には変えられない
    slack.responses["auth.test"] = [httpx.Response(200, json={"ok": True, "team_id": "T999", "user_id": "U"})]
    res = api_client.patch(f"/api/v1/channel_connections/{data['id']}/", {"bot_token": "xoxb-2"}, format="json")
    assert res.status_code == 400
    # 資格情報の誤りは、Slackの理由を見せる
    slack.responses["auth.test"] = [httpx.Response(200, json={"ok": False, "error": "invalid_auth"})]
    res = api_client.post(
        "/api/v1/channel_connections/",
        {"extension": "yoriki.channel.slack", "name": "x", "bot_token": "xoxb-3", "signing_secret": "s"},
        format="json",
    )
    assert res.status_code == 400
    assert "資格情報" in str(res.json()["bot_token"])


def test_channel_destination_checks_the_channel(api_client, integrator, slack):
    api_client.force_authenticate(integrator)
    connection = ChannelConnectionFactory.create()
    body = {
        "extension": "yoriki.channel.slack",
        "scope": TaskScope.COMPANY,
        "name": "経理の確認",
        "connection": connection.uid,
        "channel_id": "C777",
    }
    slack.responses["conversations.info"] = [
        httpx.Response(200, json={"ok": True, "channel": {"id": "C777", "name": "経理", "is_member": False}})
    ]
    res = api_client.post("/api/v1/destinations/", body, format="json")
    assert res.status_code == 400
    assert "参加していません" in str(res.json()["channel_id"])
    res = api_client.post("/api/v1/destinations/", body, format="json")
    assert res.status_code == 201, res.json()
    data = res.json()
    assert (data["is_channel"], data["channel_name"], data["connection_name"]) == (True, "#経理", connection.name)
    # 使っている接続は消せない
    res = api_client.delete(f"/api/v1/channel_connections/{connection.uid}/")
    assert res.status_code == 400


def test_private_channel_is_refused(api_client, integrator, slack):
    api_client.force_authenticate(integrator)
    connection = ChannelConnectionFactory.create()
    slack.responses["conversations.info"] = [
        httpx.Response(
            200, json={"ok": True, "channel": {"id": "G1", "name": "秘密", "is_member": True, "is_private": True}}
        )
    ]
    body = {
        "extension": "yoriki.channel.slack",
        "scope": TaskScope.COMPANY,
        "name": "x",
        "connection": connection.uid,
        "channel_id": "G1",
    }
    res = api_client.post("/api/v1/destinations/", body, format="json")
    assert res.status_code == 400
    assert "公開チャネル" in str(res.json()["channel_id"])


def _channels_page(channels: list[dict[str, Any]], next_cursor: str = "") -> httpx.Response:
    return httpx.Response(
        200, json={"ok": True, "channels": channels, "response_metadata": {"next_cursor": next_cursor}}
    )


def test_channels_are_listed_with_the_reason_they_cannot_be_chosen(api_client, integrator, slack):
    api_client.force_authenticate(integrator)
    connection = ChannelConnectionFactory.create()
    slack.responses["conversations.list"] = [
        _channels_page(
            [
                {"id": "C3", "name": "総務", "is_member": False},
                {"id": "C1", "name": "経理", "is_member": True},
                {"id": "C9", "name": "取引先", "is_member": True, "is_ext_shared": True},
            ],
            next_cursor="next",
        ),
        _channels_page([{"id": "C2", "name": "営業", "is_member": True}]),
    ]
    res = api_client.get(f"/api/v1/channel_connections/{connection.uid}/channels/")
    assert res.status_code == 200, res.json()
    data = res.json()
    assert data["truncated"] is False
    assert [(c["channel_id"], c["name"], bool(c["unavailable"])) for c in data["channels"]] == [
        ("C9", "#取引先", True),
        ("C2", "#営業", False),
        ("C1", "#経理", False),
        ("C3", "#総務", True),
    ]
    assert "参加していません" in data["channels"][3]["unavailable"]
    listed = [body for method, body in slack.calls if method == "conversations.list"]
    assert listed[0]["types"] == "public_channel" and "cursor" not in listed[0]
    assert listed[1]["cursor"] == "next"


def test_channel_list_stops_at_the_page_limit(slack):
    slack.responses["conversations.list"] = [
        _channels_page([{"id": f"C{i}", "name": f"c{i}", "is_member": True}], next_cursor=str(i)) for i in range(20)
    ]
    found = SlackAdapter().list_channels({"bot_token": "xoxb-1", "signing_secret": "s"})
    assert found.truncated is True
    assert len(found.channels) == 10


def test_channel_list_reports_errors(api_client, integrator, slack):
    api_client.force_authenticate(integrator)
    connection = ChannelConnectionFactory.create()
    url = f"/api/v1/channel_connections/{connection.uid}/channels/"
    slack.responses["conversations.list"] = [
        httpx.Response(200, json={"ok": False, "error": "missing_scope"}),
        httpx.Response(200, json={"ok": False, "error": "internal_error"}),
    ]
    res = api_client.get(url)
    assert res.status_code == 400
    assert res.json()["non_field_errors"]
    res = api_client.get(url)
    assert res.status_code == 503
    connection.suspended = True
    connection.save()
    res = api_client.get(url)
    assert res.status_code == 400
    assert "使えません" in str(res.json()["non_field_errors"])


def test_channel_list_needs_the_view_permission(api_client, slack):
    connection = ChannelConnectionFactory.create()
    api_client.force_authenticate(UserFactory.create())
    res = api_client.get(f"/api/v1/channel_connections/{connection.uid}/channels/")
    assert res.status_code == 403


def test_file_only_reply_does_not_answer(client, manager, slack):
    run = _waiting_run(manager, slack)
    thread = ExternalThreadBinding.objects.get(run=run)
    event = {
        "type": "message",
        "subtype": "file_share",
        "channel": thread.channel_id,
        "user": "U123",
        "text": "",
        "ts": "2000.0001",
        "thread_ts": thread.thread_ref,
    }
    _post_event(client, thread.connection, event)
    _process()
    assert ChannelMessageReceipt.objects.get().result == ReceiptResult.REJECTED
    assert ClarificationRequest.objects.get(run=run).status == ClarificationStatus.OPEN
    assert slack.calls[-1][0] == "chat.postEphemeral"
