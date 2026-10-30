"""通知Skill（design.md §4.3）。タスクの版で通知Skillの版を固定し、業務のRunの中で prepare_notification が通知の
文章と渡すファイルを決める。候補は完了の通知の内容に載せ、なければ標準の形で送る"""

import io
import socket
from unittest import mock

import pytest

from artifact.models import Artifact, BlobObject
from delivery.factories import DestinationFactory
from delivery.models import Delivery, DestinationKind, Notification
from delivery.tasks import send_delivery
from harness.tests.fakes import FakeAdapter, calls, reply
from harness.tests.test_connectors import execute, results, text_of
from harness.tools.base import ToolContext
from harness.tools.prepare_notification import PREPARE_NOTIFICATION
from llm.factories import ModelProfileFactory
from llm.models import ModelProfileVersion
from llm.providers.base import ToolCall
from run.models import RunLaunch, RunStatus
from run.tasks import claim_run
from skill.factories import SkillFactory, SkillVersionFactory
from skill.models import SkillScope
from task.dispatch import start_task_run
from task.factories import TaskDefinitionFactory
from task.models import TaskScope
from user.factories import DepartmentFactory, UserFactory, grant
from utils.permissions import AppPermission

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("default_runtime")]

TASKS = "/api/app/v1/tasks/"
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
FILE_SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "report": {"type": "string", "format": "file"},
        "attachments": {"type": "array", "items": {"type": "string", "format": "file"}},
    },
    "required": ["ok"],
}


@pytest.fixture(autouse=True)
def fixture_public_dns(settings):
    settings.YORIKI_PUBLIC_URL = "https://yoriki.example.co.jp"
    info = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    with mock.patch("delivery.network.socket.getaddrinfo", return_value=info):
        yield


@pytest.fixture(name="manager")
def fixture_manager(db):
    return grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)


def _notifier(name="結果の連絡", tools=(), **version):
    skill = SkillFactory.create(scope=SkillScope.COMPANY, name=name, owner=None)
    SkillVersionFactory.create(skill=skill, instructions="1. 確認した件数を1文で書く", tools=list(tools), **version)
    return skill


def _task(*, kind=DestinationKind.IN_APP, schema=SCHEMA, notifier=None):
    url = "" if kind == DestinationKind.IN_APP else "https://hooks.example.com/yoriki"
    notifier = notifier or _notifier()
    return TaskDefinitionFactory.create(
        scope=TaskScope.COMPANY,
        revision__output_schema=schema,
        revision__destination=DestinationFactory.create(kind=kind, revision__url=url),
        revision__notify_events=["run.completed", "run.failed"],
        revision__notification_skill={"skill": notifier.uid, "version": 1},
    )


def test_the_notification_skill_prepares_the_message_sent_on_completion(manager):
    task = _task()
    run = start_task_run(task, inputs={}, actor=manager, launch=RunLaunch.MANUAL)
    adapter = FakeAdapter(
        [
            calls(("c1", "prepare_notification", {"message": "まだ確定していません"})),
            calls(("c2", "submit_result", {"ok": True})),
            calls(("c3", "prepare_notification", {"message": "  請求書3件を確認しました。差異はありません。 "})),
            reply("確認しました"),
        ]
    )
    run = execute(run, adapter)

    assert run.status == RunStatus.COMPLETED, run.error
    first = adapter.requests[0]
    assert sorted(t.name for t in first.tools) == ["prepare_notification", "submit_result"]
    # submit_result の説明と結果も、確定した後に prepare_notification を呼ぶよう伝える（ほかのToolを禁じる文と矛盾
    # させない）
    submit = next(t for t in first.tools if t.name == "submit_result")
    assert "call prepare_notification" in submit.description
    [submitted] = results(run, "submit_result")
    assert "call prepare_notification" in text_of(submitted)
    # 通知Skillの本文と、どれが通知Skillかを最初の発言で伝える
    text = "\n".join(m.text for m in first.messages if m.role == "user")
    assert "確認した件数を1文で書く" in text
    assert 'The skill "結果の連絡" is the notification skill' in text
    # 業務結果のあるRunでは、確定する前の候補は受け付けない
    before, after = results(run, "prepare_notification")
    assert before["is_error"]
    assert "submit_result first" in text_of(before)
    assert not after["is_error"], text_of(after)
    assert run.notification == {"message": "請求書3件を確認しました。差異はありません。"}

    # 候補と自動の通知を1つの配信にまとめ、画面の通知に文章を出す
    delivery = Delivery.objects.get(run=run)
    assert delivery.payload["notification"] == {"message": "請求書3件を確認しました。差異はありません。"}
    assert delivery.payload["result"] == {"ok": True}
    send_delivery(delivery.pk)
    assert Notification.objects.get(delivery=delivery).message == "請求書3件を確認しました。差異はありません。"


def test_resubmitting_the_result_clears_the_candidate(manager):
    """業務結果を確定し直したら、前の結果から作った通知の文章は送らない（作り直さなければ標準の形）"""
    run = start_task_run(_task(), inputs={}, actor=manager, launch=RunLaunch.MANUAL)
    adapter = FakeAdapter(
        [
            calls(("c1", "submit_result", {"ok": True})),
            calls(("c2", "prepare_notification", {"message": "差異はありません"})),
            calls(("c3", "submit_result", {"ok": False})),
            reply("差異がありました"),
        ]
    )
    run = execute(run, adapter)
    assert run.status == RunStatus.COMPLETED, run.error
    assert run.notification is None
    payload = Delivery.objects.get(run=run).payload
    assert (payload["result"], payload["notification"]) == ({"ok": False}, None)
    # system prompt でも、確定した後に prepare_notification を呼ぶ順番を伝える
    assert "then `prepare_notification`, then write your final reply" in next(
        m.text for m in adapter.requests[0].messages if m.role == "system"
    )


def test_repeated_errors_of_the_notification_tool_do_not_fail_the_work(manager):
    run = start_task_run(_task(), inputs={}, actor=manager, launch=RunLaunch.MANUAL)
    wrong = {"message": "送ります", "files": ["report.xlsx"]}
    adapter = FakeAdapter(
        [
            calls(("c0", "submit_result", {"ok": True})),
            *[calls((f"c{i}", "prepare_notification", wrong)) for i in range(1, 6)],
            reply("確認しました"),
        ]
    )
    run = execute(run, adapter)
    assert run.status == RunStatus.COMPLETED, run.error
    *_errors, last = results(run, "prepare_notification")
    assert "Do not call it again" in text_of(last)
    assert Delivery.objects.get(run=run).payload["notification"] is None


def test_without_a_candidate_the_standard_form_is_sent(manager):
    """通知Skillが候補を作らなかった・失敗した場合も、業務は完了にし、標準の形で送る"""
    run = start_task_run(_task(), inputs={}, actor=manager, launch=RunLaunch.MANUAL)
    run = execute(run, FakeAdapter([calls(("c1", "submit_result", {"ok": True})), reply("確認しました")]))
    assert run.status == RunStatus.COMPLETED, run.error
    delivery = Delivery.objects.get(run=run)
    assert delivery.payload["notification"] is None
    send_delivery(delivery.pk)
    assert Notification.objects.get(delivery=delivery).message == ""


def test_the_candidate_is_not_sent_for_a_failure(manager):
    run = start_task_run(_task(), inputs={}, actor=manager, launch=RunLaunch.MANUAL)
    run.set_status(RunStatus.RUNNING)
    run.notification = {"message": "請求書3件を確認しました。"}
    run.save(update_fields=["notification"])
    run.set_status(RunStatus.FAILED)
    assert Delivery.objects.get(run=run).payload["notification"] is None


def _files_run(manager, result):
    run = start_task_run(
        _task(kind=DestinationKind.WEBHOOK, schema=FILE_SCHEMA), inputs={}, actor=manager, launch=RunLaunch.MANUAL
    )
    attempt = claim_run(run.pk)
    assert attempt is not None
    for path in ("report.xlsx", "a/b.csv", "unused.txt"):
        Artifact.create_output(attempt.run, path, BlobObject.put(io.BytesIO(b"x")))
    attempt.run.result = result
    attempt.run.save(update_fields=["result"])
    return attempt


def _prepare(attempt, arguments):
    context = ToolContext(run=attempt.run, attempt=attempt, call=ToolCall("c1", "prepare_notification", arguments))
    return PREPARE_NOTIFICATION.execute(arguments, context)


def test_files_are_chosen_only_from_the_result_files(manager):
    attempt = _files_run(manager, {"ok": True, "report": "/workspace/output/report.xlsx", "attachments": ["a/b.csv"]})
    # 業務結果が指していないファイルは選べない（渡すファイルを増やせない）
    for path in ("/workspace/output/unused.txt", "/workspace/input/a.csv", "../report.xlsx"):
        result = _prepare(attempt, {"message": "送ります", "files": [path]})
        assert result.is_error, path
        assert "/workspace/output/report.xlsx" in result.content[0].text
    assert attempt.run.notification is None

    result = _prepare(
        attempt, {"message": "報告書だけ送ります", "files": ["report.xlsx", "/workspace/output/report.xlsx"]}
    )
    assert not result.is_error
    run = attempt.run
    run.refresh_from_db()
    assert run.notification == {"message": "報告書だけ送ります", "files": ["report.xlsx"]}
    run.set_status(RunStatus.COMPLETED)
    payload = Delivery.objects.get(run=run).payload
    assert [f["name"] for f in payload["files"]] == ["report.xlsx"]
    assert payload["notification"] == {"message": "報告書だけ送ります"}


def test_an_empty_list_sends_no_files(manager):
    attempt = _files_run(manager, {"ok": True, "report": "report.xlsx", "attachments": []})
    assert not _prepare(attempt, {"message": "ファイルは送りません", "files": []}).is_error
    run = attempt.run
    run.refresh_from_db()
    run.set_status(RunStatus.COMPLETED)
    assert Delivery.objects.get(run=run).payload["files"] == []


def test_a_task_run_without_tools_falls_back_to_the_standard_form(manager):
    """Toolを使えないモデルでは prepare_notification を出さず、通知Skillを伝えない"""
    profile = ModelProfileFactory.create(version__supports_tools=True)
    task = TaskDefinitionFactory.create(
        scope=TaskScope.COMPANY,
        revision__model_profile=profile,
        revision__destination=DestinationFactory.create(kind=DestinationKind.IN_APP, revision__url=""),
        revision__notify_events=["run.completed"],
        revision__notification_skill={"skill": _notifier(tools=["read"]).uid, "version": 1},
    )
    # 保存した後に、モデル設定の今の版がToolを使えなくなった
    ModelProfileVersion.objects.filter(profile=profile).update(supports_tools=False)
    # 通知SkillがToolを要しても、受付を断らずに、通知Skillを読み込まずに標準の形で送る
    run = start_task_run(task, inputs={}, actor=manager, launch=RunLaunch.MANUAL)
    assert run.manifest["fixed"]["task"]["notification_skill"] == ""
    assert run.manifest["fixed"]["skills"] == []


# --- タスクの保存 ---


def _create(api_client, user, **data):
    api_client.force_login(user)
    body = {
        "scope": TaskScope.COMPANY,
        "name": "請求書チェック",
        "prompt_template": "請求書を確認してください。",
        "model_profile": ModelProfileFactory.create(version__supports_tools=True).uid,
        **data,
    }
    return api_client.post(TASKS, body, format="json")


def test_a_notification_skill_needs_the_completed_notification_with_the_result(api_client, manager):
    notifier = _notifier()
    entry = {"skill": notifier.uid, "version": 1}
    destination = DestinationFactory.create().uid
    for data in (
        {},
        {"destination": destination, "notify_events": ["run.failed"]},
        {"destination": destination, "notify_events": ["run.completed"], "notify_content": "status"},
    ):
        res = _create(api_client, manager, notification_skill=entry, **data)
        assert res.status_code == 400, data
        assert "notification_skill" in res.json(), data

    res = _create(
        api_client, manager, notification_skill=entry, destination=destination, notify_events=["run.completed"]
    )
    assert res.status_code == 201, res.json()
    assert res.json()["notification_skill"] == entry


def test_a_notification_skill_is_checked_like_the_other_skills(api_client, manager):
    common = {"destination": DestinationFactory.create().uid, "notify_events": ["run.completed"]}
    other = SkillFactory.create(scope=SkillScope.DEPARTMENT, department=DepartmentFactory.create(), owner=None)
    SkillVersionFactory.create(skill=other, tools=[])
    res = _create(api_client, manager, notification_skill={"skill": other.uid, "version": 1}, **common)
    assert "このタスクでは使えません" in str(res.json()["notification_skill"])

    notifier = _notifier()
    res = _create(api_client, manager, notification_skill={"skill": notifier.uid, "version": 2}, **common)
    assert "版2" in str(res.json()["notification_skill"])

    entry = {"skill": notifier.uid, "version": 1}
    res = _create(api_client, manager, skills=[entry], notification_skill=entry, **common)
    assert "通知Skill" in str(res.json()["notification_skill"])

    # 業務結果のSchemaを持つSkillは通知Skillにできない（最初の発言に業務結果の形が2つ載らないように）
    shaped = _notifier("結果の形あり", output_schema=SCHEMA)
    res = _create(api_client, manager, notification_skill={"skill": shaped.uid, "version": 1}, **common)
    assert "業務結果のSchema" in str(res.json()["notification_skill"])

    # 通知Skillの「使ってよいTool」も、タスクのToolの一覧に使える
    reader = _notifier("報告書の要約")
    SkillVersionFactory.create(skill=reader, tools=["read"])
    res = _create(api_client, manager, notification_skill={"skill": reader.uid, "version": 2}, tools=["read"], **common)
    assert res.status_code == 201, res.json()
