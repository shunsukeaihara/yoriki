"""タスクの入口（Webhook。design.md §4.2、authorization.md §5.5）。入口の管理、鍵、受付、重複の判定、項目の取り出し、
payloadのファイル化、状態を確かめる口"""

import json
from urllib.parse import urlencode

import pytest
from rest_framework.exceptions import ValidationError

from artifact.models import ArtifactKind, BlobObject
from artifact.tests.test_api import body_of
from conversation.models import ConversationPurpose
from run.models import Run, RunEndReason, RunEvent, RunEventKind, RunLaunch, RunStatus, RunTrigger
from task.dispatch import WEBHOOK_NOTE, receive_webhook
from task.factories import TaskDefinitionFactory
from task.models import (
    REVISION_FIELDS,
    TaskDefinition,
    TaskScope,
    TriggerBinding,
    TriggerBindingRevision,
    TriggerReceipt,
)
from task.serializers import TriggerBindingSerializer
from task.webhook import PayloadError, extract, parse_path
from user.factories import UserFactory, grant
from user.models import SYSTEM_USERNAME, User, system_user

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("default_runtime")]

BINDINGS = "/api/v1/trigger_bindings/"
INPUTS = [
    {"name": "vendor", "label": "取引先", "type": "string", "required": True},
    {"name": "amount", "label": "金額", "type": "integer"},
]
BINDING_PERMS = [f"task.{kind}_triggerbinding" for kind in ("view", "add", "change", "delete")]


@pytest.fixture(name="integrator")
def fixture_integrator(db) -> User:
    """連携の管理者（入口のModelのpermissionだけ。タスクを管理する権限は持たない）"""
    return grant(UserFactory.create(), *BINDING_PERMS)


@pytest.fixture(name="task")
def fixture_task(db) -> TaskDefinition:
    return TaskDefinitionFactory.create(
        scope=TaskScope.COMPANY,
        name="請求書チェック",
        revision__prompt_template="{{ inputs.vendor }} の請求書を確認してください。",
        revision__inputs=INPUTS,
    )


def _create_binding(api_client, user, task, **data):
    api_client.force_login(user)
    body = {"task": task.uid, "name": "経費SaaS", **data}
    return api_client.post(BINDINGS, body, format="json")


@pytest.fixture(name="webhook")
def fixture_webhook(api_client, integrator, task) -> tuple[TriggerBinding, str]:
    res = _create_binding(
        api_client,
        integrator,
        task,
        event_id_path="$.event.id",
        inputs=[{"name": "vendor", "path": "$.data.vendor_name"}, {"name": "amount", "path": "$.data['total']"}],
    )
    assert res.status_code == 201, res.json()
    api_client.logout()
    return TriggerBinding.objects.get(uid=res.json()["id"]), res.json()["key"]


def _hook(task: TaskDefinition) -> str:
    return f"/api/hooks/v1/tasks/{task.uid}/"


def _send(api_client, task, key, payload, *, content_type="application/json", **headers):
    body = json.dumps(payload) if content_type == "application/json" and not isinstance(payload, str) else payload
    return api_client.post(_hook(task), body, content_type=content_type, HTTP_AUTHORIZATION=f"Bearer {key}", **headers)


def _payload(event_id="evt-1", vendor="ACME", total=12000):
    return {"event": {"id": event_id}, "data": {"vendor_name": vendor, "total": total, "memo": "前回と同じ"}}


# --- 入口の管理（連携の管理者） ---


def test_creating_a_webhook_needs_the_permission_and_shows_the_key_once(api_client, app_user, integrator, task):
    assert _create_binding(api_client, app_user, task).status_code == 403

    res = _create_binding(api_client, integrator, task)
    assert res.status_code == 201, res.json()
    data = res.json()
    assert data["key"].startswith("yrk_wh_")
    assert data["key"].startswith(data["key_prefix"])
    assert data["url"] == f"/api/hooks/v1/tasks/{task.uid}/"
    assert data["version"] == 1
    assert data["max_payload_bytes"] == 1024 * 1024
    assert data["task_info"]["inputs"][0]["name"] == "vendor"
    # 鍵は保存せず、一覧・詳細にも出さない
    binding = TriggerBinding.objects.get(uid=data["id"])
    assert data["key"] not in (binding.key_hash, binding.key_prefix)
    assert "key" not in api_client.get(f"{BINDINGS}{data['id']}/").json()


def test_a_task_has_at_most_one_webhook_and_personal_tasks_have_none(api_client, integrator, task):
    assert _create_binding(api_client, integrator, task).status_code == 201
    res = _create_binding(api_client, integrator, task)
    assert res.status_code == 400
    assert "task" in res.json()

    personal = TaskDefinitionFactory.create(scope=TaskScope.PERSONAL)
    assert _create_binding(api_client, integrator, personal).status_code == 400

    # 候補のタスクには、入口のあるタスクと個人のタスクを出さない
    other = TaskDefinitionFactory.create(scope=TaskScope.SYSTEM, name="突合")
    names = [t["name"] for t in api_client.get(f"{BINDINGS}tasks/").json()["results"]]
    assert names == [other.name]


def test_the_mapping_must_name_task_inputs_and_valid_paths(api_client, integrator, task):
    res = _create_binding(api_client, integrator, task, inputs=[{"name": "unknown", "path": "$.a"}])
    assert res.status_code == 400
    assert "unknown" in str(res.json()["inputs"])

    res = _create_binding(api_client, integrator, task, inputs=[{"name": "vendor", "path": "data.vendor"}])
    assert res.status_code == 400
    res = _create_binding(api_client, integrator, task, event_id_path="$.event[")
    assert res.status_code == 400
    res = _create_binding(api_client, integrator, task, max_payload_bytes=11 * 1024 * 1024)
    assert res.status_code == 400


def test_saving_makes_a_new_version_and_detects_conflicts(api_client, integrator, webhook):
    binding, _key = webhook
    api_client.force_login(integrator)
    url = f"{BINDINGS}{binding.uid}/"
    res = api_client.patch(url, {"version": 1, "max_payload_bytes": 2048}, format="json")
    assert res.status_code == 200, res.json()
    assert (res.json()["version"], res.json()["max_payload_bytes"], res.json()["event_id_path"]) == (
        2,
        2048,
        "$.event.id",
    )
    # 編集を始めた後に他の人が版を作っていたら409
    assert api_client.patch(url, {"version": 1, "name": "別名"}, format="json").status_code == 409
    # 名前だけの変更では版を作らない
    res = api_client.patch(url, {"version": 2, "name": "別名"}, format="json")
    assert (res.json()["version"], res.json()["name"]) == (2, "別名")
    numbers = [r["number"] for r in api_client.get(f"{url}revisions/").json()["results"]]
    assert numbers == [2, 1]
    assert TriggerBindingRevision.objects.filter(binding=binding).count() == 2


def test_rotating_the_key_disables_the_old_one(api_client, integrator, task, webhook):
    binding, old_key = webhook
    api_client.force_login(integrator)
    res = api_client.post(f"{BINDINGS}{binding.uid}/rotate_key/")
    assert res.status_code == 200
    new_key = res.json()["key"]
    assert new_key != old_key
    api_client.logout()
    assert _send(api_client, task, old_key, _payload()).status_code == 401
    assert _send(api_client, task, new_key, _payload()).status_code == 202


def test_suspended_or_deleted_webhooks_do_not_start_runs(api_client, integrator, task, webhook):
    binding, key = webhook
    api_client.force_login(integrator)
    api_client.post(f"{BINDINGS}{binding.uid}/suspend/")
    api_client.logout()
    res = _send(api_client, task, key, _payload())
    assert (res.status_code, res.json()["code"]) == (403, "trigger_suspended")

    api_client.force_login(integrator)
    api_client.post(f"{BINDINGS}{binding.uid}/resume/")
    assert api_client.delete(f"{BINDINGS}{binding.uid}/").status_code == 204
    api_client.logout()
    assert _send(api_client, task, key, _payload()).status_code == 401
    assert not Run.objects.exists()


def test_task_managers_see_the_webhook_of_their_task(api_client, platform_admin, webhook, task):
    binding, _key = webhook
    api_client.force_login(platform_admin)
    triggers = api_client.get(f"/api/app/v1/tasks/{task.uid}/").json()["triggers"]
    assert [(t["id"], t["key_prefix"]) for t in triggers] == [(binding.uid, binding.key_prefix)]


def test_deleting_the_task_deletes_its_webhook(api_client, task, webhook):
    binding, key = webhook
    task.soft_delete()
    binding.refresh_from_db()
    assert binding.deleted
    assert _send(api_client, task, key, _payload()).status_code == 401


# --- 受付 ---


def test_a_webhook_starts_a_task_run_with_the_payload_as_a_file(api_client, task, webhook):
    binding, key = webhook
    res = _send(api_client, task, key, _payload())
    assert res.status_code == 202, res.json()
    assert res.json() == {"run_id": res.json()["run_id"], "status": "queued", "failure_code": None, "duplicate": False}

    run = Run.objects.get(uid=res.json()["run_id"])
    owner = system_user()
    assert (run.trigger, run.launch, run.task, run.owner) == (RunTrigger.TASK, RunLaunch.WEBHOOK, task, owner)
    assert run.conversation.purpose == ConversationPurpose.TASK
    assert run.manifest["fixed"]["task"]["inputs"] == {"vendor": "ACME", "amount": 12000}
    receipt = TriggerReceipt.objects.get(run=run)
    assert (receipt.binding, receipt.dedup_key, receipt.binding_revision) == (
        binding,
        "event:evt-1",
        binding.current_revision,
    )

    # payloadはそのままファイルにし、/workspace/input に置く
    message = run.conversation.messages.get()
    artifact = message.attachments.get()
    assert (artifact.kind, artifact.name, artifact.path) == (
        ArtifactKind.ATTACHMENT,
        "webhook_payload.json",
        "webhook_payload.json",
    )
    with artifact.blob.open() as f:
        assert json.loads(f.read()) == _payload()
    event = RunEvent.objects.get(run=run, kind=RunEventKind.USER_MESSAGE)
    assert event.data["attachments"][0]["path"] == "/workspace/input/webhook_payload.json"
    assert WEBHOOK_NOTE in event.data["text"]
    # 入力の値は信頼しない内容として囲む
    assert '<untrusted_content source="input:vendor">' in event.data["text"]


def test_inputs_are_checked_with_the_task_definition(api_client, task, webhook):
    _binding, key = webhook
    res = _send(api_client, task, key, {"event": {"id": "e"}, "data": {"total": 100}})
    assert res.status_code == 400
    assert res.json()["code"] == "invalid_inputs"
    assert set(res.json()["inputs"]) == {"vendor"}

    res = _send(api_client, task, key, _payload(total="12.5"))
    assert (res.status_code, set(res.json()["inputs"])) == (400, {"amount"})
    assert not Run.objects.exists()


def test_same_event_returns_the_same_run_and_a_changed_one_is_refused(api_client, task, webhook):
    _binding, key = webhook
    first = _send(api_client, task, key, _payload())
    # 並び順と空白が違っても同じ入力
    again = _send(api_client, task, key, json.dumps(_payload(), indent=2, sort_keys=True))
    assert again.status_code == 200
    assert again.json()["run_id"] == first.json()["run_id"]
    assert again.json()["duplicate"] is True

    changed = _send(api_client, task, key, _payload(vendor="別の会社"))
    assert (changed.status_code, changed.json()["code"]) == (409, "event_id_reused")
    # 別のイベントは新しいRun
    assert _send(api_client, task, key, _payload(event_id="evt-2")).status_code == 202
    assert Run.objects.count() == 2


def test_idempotency_key_header_and_no_key(api_client, task, webhook):
    _binding, key = webhook
    body = {"data": {"vendor_name": "ACME"}}
    first = _send(api_client, task, key, body, HTTP_IDEMPOTENCY_KEY="k-1")
    again = _send(api_client, task, key, body, HTTP_IDEMPOTENCY_KEY="k-1")
    assert again.json()["run_id"] == first.json()["run_id"]
    # キーがなければ重複を判定しない
    assert _send(api_client, task, key, body).status_code == 202
    assert _send(api_client, task, key, body).status_code == 202
    assert Run.objects.count() == 3


def test_recommended_format_needs_no_mapping(api_client, integrator, task):
    res = _create_binding(api_client, integrator, task)
    key = res.json()["key"]
    api_client.logout()
    body = {"event_id": "z-1", "message": "経理の確認用", "inputs": {"vendor": "ACME", "amount": "300"}}
    res = _send(api_client, task, key, body)
    assert res.status_code == 202, res.json()
    run = Run.objects.get(uid=res.json()["run_id"])
    assert run.manifest["fixed"]["task"]["inputs"] == {"vendor": "ACME", "amount": 300}
    assert TriggerReceipt.objects.get(run=run).dedup_key == "event:z-1"
    # message は依頼文にしない（payloadのファイルに残るだけ）
    assert "経理の確認用" not in RunEvent.objects.get(run=run, kind=RunEventKind.USER_MESSAGE).data["text"]

    res = _send(api_client, task, key, {"inputs": {"vendor": "ACME", "unknown": 1}})
    assert (res.status_code, set(res.json()["inputs"])) == (400, {"unknown"})


def test_form_values_and_text(api_client, integrator, task):
    res = _create_binding(api_client, integrator, task, inputs=[{"name": "vendor", "path": "$.vendor"}])
    key = res.json()["key"]
    api_client.logout()
    res = _send(
        api_client,
        task,
        key,
        urlencode({"vendor": "ACME", "tag": "a"}),
        content_type="application/x-www-form-urlencoded",
    )
    assert res.status_code == 202, res.json()
    run = Run.objects.get(uid=res.json()["run_id"])
    assert run.manifest["fixed"]["task"]["inputs"] == {"vendor": "ACME"}

    # テキストからは項目を取り出せないので、必須の項目があれば断る
    res = _send(api_client, task, key, "請求書が届きました", content_type="text/plain")
    assert (res.status_code, res.json()["code"]) == (400, "invalid_inputs")
    res = _send(api_client, task, key, b"<x/>", content_type="application/xml")
    assert (res.status_code, res.json()["code"]) == (415, "unsupported_media_type")
    res = _send(api_client, task, key, "{", content_type="application/json")
    assert (res.status_code, res.json()["code"]) == (400, "invalid_json")


def test_text_payload_is_kept_as_a_text_file(api_client, integrator):
    task = TaskDefinitionFactory.create(scope=TaskScope.COMPANY, revision__inputs=[])
    key = _create_binding(api_client, integrator, task).json()["key"]
    api_client.logout()
    res = _send(api_client, task, key, "請求書が届きました", content_type="text/plain; charset=utf-8")
    assert res.status_code == 202, res.json()
    run = Run.objects.get(uid=res.json()["run_id"])
    assert run.conversation.messages.get().attachments.get().name == "webhook_payload.txt"


def test_payload_size_limit(api_client, integrator, task, webhook):
    binding, key = webhook
    api_client.force_login(integrator)
    api_client.patch(f"{BINDINGS}{binding.uid}/", {"version": 1, "max_payload_bytes": 1024}, format="json")
    api_client.logout()
    res = _send(api_client, task, key, _payload(vendor="あ" * 400))
    assert (res.status_code, res.json()["code"]) == (413, "payload_too_large")


def test_authentication(api_client, task, webhook):
    _binding, key = webhook
    assert api_client.post(_hook(task), {}, format="json").status_code == 401
    assert _send(api_client, task, "yrk_wh_wrong", _payload()).status_code == 401
    # 別のタスクのURLには使えない（あるかどうかも見せない）
    other = TaskDefinitionFactory.create(scope=TaskScope.COMPANY)
    assert _send(api_client, other, key, _payload()).status_code == 404
    # セッションでは使えない
    api_client.force_login(UserFactory.create())
    assert api_client.post(_hook(task), _payload(), format="json").status_code == 401


def test_task_that_cannot_run_is_refused_without_details(api_client, task, webhook):
    _binding, key = webhook
    task.set_suspended(True)
    res = _send(api_client, task, key, _payload())
    assert res.json() == {"code": "task_unavailable", "detail": "タスクを今は実行できません"}
    assert res.status_code == 409
    assert not Run.objects.exists()


# --- 状態を確かめる口 ---


def test_the_sender_sees_only_the_state_of_its_runs(api_client, integrator, task, webhook):
    _binding, key = webhook
    run_id = _send(api_client, task, key, _payload()).json()["run_id"]
    url = f"{_hook(task)}runs/{run_id}/"
    res = api_client.get(url, HTTP_AUTHORIZATION=f"Bearer {key}")
    assert res.json() == {"run_id": run_id, "status": "queued", "failure_code": None}

    Run.objects.filter(uid=run_id).update(
        status=RunStatus.FAILED, end_reason=RunEndReason.STEP_LIMIT, error="社内の情報を含む失敗の理由"
    )
    res = api_client.get(url, HTTP_AUTHORIZATION=f"Bearer {key}")
    assert res.json() == {"run_id": run_id, "status": "failed", "failure_code": "step_limit"}

    # 他の入口のRunは見えない
    other_task = TaskDefinitionFactory.create(scope=TaskScope.COMPANY, revision__inputs=[])
    other_key = _create_binding(api_client, integrator, other_task).json()["key"]
    api_client.logout()
    other_run = _send(api_client, other_task, other_key, {}).json()["run_id"]
    assert api_client.get(f"{_hook(task)}runs/{other_run}/", HTTP_AUTHORIZATION=f"Bearer {key}").status_code == 404
    assert api_client.get(url).status_code == 401


def test_the_admin_sees_receipts_without_results(api_client, integrator, task, webhook):
    binding, key = webhook
    _send(api_client, task, key, _payload())
    api_client.force_login(integrator)
    receipts = api_client.get(f"{BINDINGS}{binding.uid}/receipts/").json()["results"]
    assert [(r["dedup_key"], r["run_status"]) for r in receipts] == [("event:evt-1", RunStatus.QUEUED)]
    assert "result" not in receipts[0]
    # 連携の管理者はタスクを管理できないので、タスクのRunの結果・ログは見えない
    assert api_client.get(f"/api/app/v1/tasks/{task.uid}/runs/").status_code == 404


# --- システム用のユーザー ---


def test_system_user_cannot_log_in_and_is_hidden_from_user_admin(api_client, platform_admin):
    user = system_user()
    assert system_user() == user
    assert (user.is_system, user.is_active, user.has_usable_password()) == (True, False, False)
    api_client.force_login(platform_admin)
    emails = [u["id"] for u in api_client.get("/api/v1/users/").json()["results"]]
    assert user.uid not in emails
    assert api_client.get(f"/api/v1/users/{user.uid}/").status_code == 404


def test_a_key_rotated_or_suspended_after_authentication_cannot_start_a_run(task, webhook):
    binding, _key = webhook
    authenticated = TriggerBinding.objects.get(pk=binding.pk)  # 認証したときに読んだ入口
    binding.rotate_key()
    with pytest.raises(PayloadError) as exc:
        receive_webhook(
            authenticated, body=json.dumps(_payload()).encode(), content_type="application/json", idempotency_key=None
        )
    assert exc.value.status == 401

    authenticated = TriggerBinding.objects.get(pk=binding.pk)
    TriggerBinding.objects.filter(pk=binding.pk).update(suspended=True)
    with pytest.raises(PayloadError) as exc:
        receive_webhook(
            authenticated, body=json.dumps(_payload()).encode(), content_type="application/json", idempotency_key=None
        )
    assert exc.value.status == 403
    assert not Run.objects.exists()


def test_resending_after_changing_the_event_id_path_returns_the_same_run(api_client, integrator, task, webhook):
    binding, key = webhook
    first = _send(api_client, task, key, _payload()).json()["run_id"]
    api_client.force_login(integrator)
    api_client.patch(f"{BINDINGS}{binding.uid}/", {"version": 1, "event_id_path": "$.data.vendor_name"}, format="json")
    api_client.logout()
    res = _send(api_client, task, key, _payload())
    assert (res.status_code, res.json()["run_id"]) == (200, first)
    # 前の版の場所のキーが同じでも、入力が違えば別の出来事として受け付ける
    res = _send(api_client, task, key, _payload(vendor="別の会社"))
    assert res.status_code == 202
    assert Run.objects.count() == 2


def test_refused_requests_leave_no_payload(api_client, task, webhook):
    _binding, key = webhook
    before = BlobObject.objects.count()
    assert _send(api_client, task, key, {"data": {}}).status_code == 400
    task.set_suspended(True)
    assert _send(api_client, task, key, _payload()).status_code == 409
    assert BlobObject.objects.count() == before


def test_the_current_payload_limit_applies_after_locking(task, webhook):
    binding, _key = webhook
    authenticated = TriggerBinding.objects.get(pk=binding.pk)  # 上限が1MiBのときに認証した
    binding.revise(actor=binding.created_by, expected_version=1, **{**_content(binding), "max_payload_bytes": 1024})
    body = json.dumps(_payload(vendor="あ" * 400)).encode()
    with pytest.raises(PayloadError) as exc:
        receive_webhook(authenticated, body=body, content_type="application/json", idempotency_key=None)
    assert exc.value.status == 413


def test_creating_a_second_webhook_after_the_check_is_refused(api_client, integrator, task):
    """作成前の確かめの後に、別の要求が同じタスクに入口を作った場合も、500ではなく入力の誤りにする"""
    serializer = TriggerBindingSerializer(
        data={"task": task.uid, "name": "A"}, context={"request": type("R", (), {"user": integrator})()}
    )
    assert serializer.is_valid(), serializer.errors
    assert _create_binding(api_client, integrator, task).status_code == 201
    with pytest.raises(ValidationError):
        serializer.save(actor=integrator)
    assert TriggerBinding.objects.alive().filter(task=task).count() == 1


def _content(binding):
    revision = binding.current_revision
    return {name: getattr(revision, name) for name in ("event_id_path", "inputs", "max_payload_bytes")}


def test_form_with_too_many_fields_is_refused(api_client, integrator, settings):
    settings.DATA_UPLOAD_MAX_NUMBER_FIELDS = 3
    task = TaskDefinitionFactory.create(scope=TaskScope.COMPANY, revision__inputs=[])
    key = _create_binding(api_client, integrator, task).json()["key"]
    api_client.logout()
    body = urlencode({f"f{n}": "x" for n in range(4)})
    res = _send(api_client, task, key, body, content_type="application/x-www-form-urlencoded")
    assert (res.status_code, res.json()["code"]) == (400, "too_many_fields")
    assert not Run.objects.exists()


def test_lone_surrogates_are_refused(api_client, task, webhook):
    _binding, key = webhook
    body = json.dumps(_payload())[:-1] + ', "message": "\\ud800"}'
    res = _send(api_client, task, key, body)
    assert (res.status_code, res.json()["code"]) == (400, "invalid_json")
    assert not Run.objects.exists()


def test_task_managers_can_download_the_received_payload(api_client, platform_admin, task, webhook):
    _binding, key = webhook
    run_id = _send(api_client, task, key, _payload()).json()["run_id"]
    api_client.force_login(platform_admin)
    files = api_client.get(f"/api/app/v1/runs/{run_id}/artifacts/").json()["results"]
    assert [f["name"] for f in files] == ["webhook_payload.json"]
    res = api_client.get(f"/api/app/v1/runs/{run_id}/artifacts/{files[0]['id']}/download/")
    assert json.loads(body_of(res)) == _payload()


def test_event_ids_and_idempotency_keys_do_not_collide(api_client, task, webhook):
    _binding, key = webhook
    first = _send(api_client, task, key, _payload(event_id="x")).json()["run_id"]
    body = {"data": {"vendor_name": "別の会社"}}
    res = _send(api_client, task, key, body, HTTP_IDEMPOTENCY_KEY="x")
    assert res.status_code == 202
    assert res.json()["run_id"] != first


def test_non_finite_numbers_are_refused(api_client, task, webhook):
    _binding, key = webhook
    for number in ("NaN", "Infinity", "1e999"):
        body = json.dumps(_payload())[:-1] + f', "score": {number}}}'
        res = _send(api_client, task, key, body)
        assert (res.status_code, res.json()["code"]) == (400, "invalid_json"), number


def test_webhook_stays_editable_after_the_task_drops_an_input(api_client, integrator, task, webhook):
    binding, _key = webhook
    revision = task.current_revision
    content = {name: getattr(revision, name) for name in REVISION_FIELDS}
    task.revise(actor=task.created_by, expected_version=1, **{**content, "inputs": INPUTS[:1]})
    api_client.force_login(integrator)
    url = f"{BINDINGS}{binding.uid}/"
    res = api_client.patch(url, {"version": 1, "max_payload_bytes": 2048}, format="json")
    assert res.status_code == 200, res.json()
    # 取り出しを送るときは、タスクにない項目を断る
    res = api_client.patch(url, {"version": 2, "inputs": [{"name": "amount", "path": "$.a"}]}, format="json")
    assert res.status_code == 400


def test_system_user_does_not_reuse_an_employee_account():
    employee = UserFactory.create(username=SYSTEM_USERNAME)
    user = system_user()
    assert user != employee
    assert (user.is_system, user.is_active) == (True, False)
    User.objects.filter(pk=user.pk).update(is_active=True)
    assert system_user().is_active is False


def test_form_with_invalid_utf8_is_refused(api_client, integrator):
    task = TaskDefinitionFactory.create(scope=TaskScope.COMPANY, revision__inputs=[])
    key = _create_binding(api_client, integrator, task, event_id_path="$.event_id").json()["key"]
    api_client.logout()
    res = _send(api_client, task, key, "event_id=%FF", content_type="application/x-www-form-urlencoded")
    assert (res.status_code, res.json()["code"]) == (400, "invalid_form")
    assert not Run.objects.exists()


def test_resending_an_old_event_after_a_new_one_collides_on_the_new_path(api_client, integrator, task, webhook):
    """前の版でイベントAを受け、場所を変えた版でイベントBを受けた後に、Aを再送しても前のRunを返す"""
    binding, key = webhook
    event_a = {"event": {"id": "a"}, "id": "shared", "data": {"vendor_name": "ACME"}}
    run_a = _send(api_client, task, key, event_a).json()["run_id"]
    api_client.force_login(integrator)
    api_client.patch(f"{BINDINGS}{binding.uid}/", {"version": 1, "event_id_path": "$.id"}, format="json")
    api_client.logout()
    event_b = {"event": {"id": "b"}, "id": "shared", "data": {"vendor_name": "別の会社"}}
    assert _send(api_client, task, key, event_b).status_code == 202
    res = _send(api_client, task, key, event_a)
    assert (res.status_code, res.json()["run_id"]) == (200, run_a)


def test_quoted_path_keys_use_json_escapes():
    assert parse_path('$.data["vendor\\u0020name"][0]') == ["data", "vendor name", 0]
    assert parse_path('$["a\\"b"]') == ['a"b']
    assert parse_path("$['it\\'s']") == ["it's"]
    assert extract({"data": {"vendor name": "ACME"}}, '$.data["vendor\\u0020name"]') == (True, "ACME")
    with pytest.raises(ValueError):
        parse_path('$["\\x"]')


def test_single_quoted_paths_allow_only_quote_and_backslash_escapes():
    with pytest.raises(ValueError):
        parse_path("$.data['a\\nb']")
    assert parse_path("$['a\\\\b']") == ["a\\b"]
