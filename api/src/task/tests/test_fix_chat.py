"""タスクの修正チャット（design.md §11.11、authorization.md §6・§7.1、harness.md §10.10）。タスクを管理できる人が
タスク専用の会話でタスクを動かし、追加の指示・回答を出す。出口へは送らず、Workspaceは実行ごとに分ける"""

import io
import json
import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from artifact.factories import ArtifactFactory
from artifact.models import Artifact, ArtifactKind, BlobObject
from connector.factories import ConnectorFactory
from conversation.factories import ConversationFactory
from conversation.models import ConversationPurpose, InstructionKind, InstructionState, MessageRole, UserInstruction
from delivery.factories import DestinationFactory
from delivery.models import Delivery
from harness import tasks
from harness.recovery import switch_candidates
from harness.sandbox import SandboxSession
from harness.tests.fakes import FakeAdapter, reply
from harness.tests.test_connectors import execute
from llm.factories import LLMConnectionFactory, ModelProfileFactory
from llm.models import ProviderKind
from run.models import (
    Checkpoint,
    ClarificationKind,
    ClarificationRequest,
    Run,
    RunEndReason,
    RunKind,
    RunLaunch,
    RunStatus,
    RunTrigger,
)
from run.tasks import claim_run
from task.dispatch import FIX_NOTE, NO_RESPONDER_NOTE
from task.factories import TaskDefinitionFactory
from task.models import TaskScope
from user.factories import DepartmentFactory, UserFactory, grant
from utils.permissions import AppPermission

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("default_runtime")]

INPUTS = [{"name": "vendor", "label": "取引先", "type": "string", "required": True}]


@pytest.fixture(name="manager")
def fixture_manager():
    """全社のタスクを管理する人"""
    return grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)


def _task(manager, **revision):
    revision.setdefault("prompt_template", "{{ inputs.vendor }} の請求書を確認してください。")
    revision.setdefault("inputs", INPUTS)
    revision.setdefault("model_profile", ModelProfileFactory.create(version__supports_tools=True))
    return TaskDefinitionFactory.create(
        scope=TaskScope.COMPANY,
        created_by=manager,
        name="請求書チェック",
        **{f"revision__{k}": v for k, v in revision.items()},
    )


def _fix(api_client, user, task, *, conversation=None, inputs=None, **params):
    api_client.force_login(user)
    params.setdefault("client_request_id", uuid.uuid4().hex)
    if conversation is not None:
        params["conversation"] = conversation.uid
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return api_client.post(
        f"/api/app/v1/tasks/{task.uid}/fix_chat/?{query}", {"inputs": inputs or {"vendor": "A商事"}}, format="json"
    )


def _finish(run: Run) -> Run:
    Run.objects.filter(pk=run.pk).update(status=RunStatus.COMPLETED)
    run.refresh_from_db()
    return run


# --- 始め方 ---


def test_fix_chat_starts_a_task_run_in_a_conversation_of_the_task(api_client, manager):
    task = _task(manager, destination=DestinationFactory.create(name="経理のZap"), notify_events=["run.completed"])
    res = _fix(api_client, manager, task, version=1)
    assert res.status_code == 201, res.json()
    run = Run.objects.get(uid=res.json()["id"])
    assert (run.trigger, run.launch, run.owner, run.task, run.kind) == (
        RunTrigger.TASK,
        RunLaunch.FIX,
        manager,
        task,
        RunKind.WORK,
    )
    conversation = run.conversation
    assert (conversation.purpose, conversation.task, conversation.owner) == (
        ConversationPurpose.TASK_FIX,
        task,
        manager,
    )
    # 出口へは送らない（通知先を固定しない）。送り先の名前は見せる
    assert run.destination_revision is None
    info = res.json()["task_info"]
    assert (info["launch"], info["delivers"], info["destination_name"], info["number"]) == (
        RunLaunch.FIX,
        False,
        "経理のZap",
        1,
    )
    # 答える人がいる
    assert run.is_interactive
    [first] = conversation.messages.all()
    assert "A商事" in first.body
    assert "の請求書を確認してください。" in first.body
    text = run.events.get(sequence=2).data["text"]
    assert FIX_NOTE in text
    assert NO_RESPONDER_NOTE not in text
    # 会話の一覧に出て、直すタスクが分かる
    listed = api_client.get("/api/app/v1/conversations/").json()["results"]
    assert [(c["id"], c["task"]["name"]) for c in listed] == [(conversation.uid, "請求書チェック")]


def test_resent_request_returns_the_run_started_before(api_client, manager):
    """応答が失われた後の再送で、二重に動かさない（新しい修正チャットでも、動かし直しでも）"""
    task = _task(manager)
    first = _fix(api_client, manager, task, client_request_id="req-1")
    assert first.status_code == 201, first.json()
    again = _fix(api_client, manager, task, client_request_id="req-1", inputs={"vendor": "B物産"})
    assert (again.status_code, again.json()["id"]) == (200, first.json()["id"])
    assert Run.objects.filter(task=task).count() == 1
    run = _finish(Run.objects.get(uid=first.json()["id"]))
    rerun = _fix(api_client, manager, task, conversation=run.conversation, client_request_id="req-2")
    assert rerun.status_code == 201, rerun.json()
    resent = _fix(api_client, manager, task, conversation=run.conversation, client_request_id="req-2")
    assert (resent.status_code, resent.json()["id"]) == (200, rerun.json()["id"])
    # 追加の指示のメッセージのIDと重なれば、再送とせずに断る
    rerun_run = Run.objects.get(uid=rerun.json()["id"])
    api_client.force_login(manager)
    sent = _post(api_client, rerun_run.conversation, client_message_id="msg-1")
    assert sent.status_code == 201, sent.json()
    assert _fix(api_client, manager, task, client_request_id="msg-1").status_code == 409
    # 同じIDでも、ほかの人の要求とは重ならない
    colleague = grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)
    assert _fix(api_client, colleague, task, client_request_id="req-1").status_code == 201
    # IDは必須
    api_client.force_login(manager)
    res = api_client.post(f"/api/app/v1/tasks/{task.uid}/fix_chat/", {"inputs": {"vendor": "A商事"}}, format="json")
    assert res.status_code == 400


def test_only_managers_start_a_fix_chat(api_client, manager):
    task = _task(manager)
    assert _fix(api_client, UserFactory.create(), task).status_code == 404
    # チャットから起動できる人でも、管理できなければ使えない
    task.set_chat_launchable(True)
    assert _fix(api_client, UserFactory.create(), task).status_code == 404


def test_suspended_task_runs_in_the_fix_chat(api_client, manager):
    task = _task(manager)
    task.set_suspended(True)
    api_client.force_login(manager)
    manual = api_client.post(f"/api/app/v1/tasks/{task.uid}/run/", {"inputs": {"vendor": "A商事"}}, format="json")
    assert manual.status_code == 400
    res = _fix(api_client, manager, task)
    assert res.status_code == 201, res.json()
    run = Run.objects.get(uid=res.json()["id"])
    # 停止中かは見ないので、区切りでも止めない
    assert tasks.revoked_task(run) == ""


def test_rerun_in_the_same_fix_chat_starts_afresh(api_client, manager):
    task = _task(manager)
    first = Run.objects.get(uid=_fix(api_client, manager, task).json()["id"])
    conversation = first.conversation
    # 動いている間は動かせない
    res = _fix(api_client, manager, task, conversation=conversation)
    assert res.status_code == 400
    _finish(first)
    res = _fix(api_client, manager, task, conversation=conversation, inputs={"vendor": "B物産"})
    assert res.status_code == 201, res.json()
    second = Run.objects.get(uid=res.json()["id"])
    assert second.conversation == conversation
    # 前の実行の文脈を引き継がない
    assert second.parent is None


def test_fix_chat_of_another_task_or_person_is_not_used(api_client, manager):
    task = _task(manager)
    conversation = Run.objects.get(uid=_fix(api_client, manager, task).json()["id"]).conversation
    other = TaskDefinitionFactory.create(scope=TaskScope.COMPANY, created_by=manager, revision__inputs=INPUTS)
    assert _fix(api_client, manager, other, conversation=conversation).status_code == 404
    colleague = grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)
    assert _fix(api_client, colleague, task, conversation=conversation).status_code == 404
    chat = ConversationFactory.create(owner=manager)
    assert _fix(api_client, manager, task, conversation=chat).status_code == 404


def test_rerun_places_files_under_the_same_names(api_client, manager):
    """前の実行の添付と同じ名前のファイルも、動かし直したRunでは同じパスに置く（Runごとに /workspace/input を分ける）"""
    inputs = [*INPUTS, {"name": "invoice", "label": "請求書", "type": "file", "required": True}]
    task = _task(manager, inputs=inputs)
    api_client.force_login(manager)

    def send(conversation=None):
        query = f"client_request_id={uuid.uuid4().hex}" + (f"&conversation={conversation.uid}" if conversation else "")
        body = {"inputs": json.dumps({"vendor": "A商事"}), "invoice": SimpleUploadedFile("請求書.pdf", b"%PDF")}
        res = api_client.post(f"/api/app/v1/tasks/{task.uid}/fix_chat/?{query}", body)
        assert res.status_code == 201, res.json()
        return Run.objects.get(uid=res.json()["id"])

    first = _finish(send())
    second = send(first.conversation)
    path = "/workspace/input/請求書.pdf"
    assert first.manifest["fixed"]["task"]["inputs"]["invoice"] == path
    assert second.manifest["fixed"]["task"]["inputs"]["invoice"] == path


def test_sensitivity_of_a_fix_chat_is_fixed(api_client, manager):
    task = _task(manager)
    conversation = Run.objects.get(uid=_fix(api_client, manager, task).json()["id"]).conversation
    res = api_client.patch(
        f"/api/app/v1/conversations/{conversation.uid}/", {"handles_sensitive_data": True}, format="json"
    )
    assert res.status_code == 400
    conversation.refresh_from_db()
    assert not conversation.handles_sensitive_data


def test_task_that_became_sensitive_needs_a_new_fix_chat(api_client, manager):
    task = _task(manager)
    run = _finish(Run.objects.get(uid=_fix(api_client, manager, task).json()["id"]))
    revision = task.current_revision
    local = ModelProfileFactory.create(
        version__connection=LLMConnectionFactory.create(kind=ProviderKind.LOCAL), version__supports_tools=True
    )
    content = {name: getattr(revision, name) for name in ("description", "prompt_template", "skills", "connectors")}
    task.revise(
        actor=manager,
        expected_version=1,
        **content,
        model_profile=local,
        effort="",
        handles_sensitive_data=True,
        inputs=revision.inputs,
        output_schema=None,
        destination=None,
        notify_events=[],
        notify_content="status_and_result",
        notification_instructions="",
    )
    res = _fix(api_client, manager, task, conversation=run.conversation)
    assert res.status_code == 400
    assert "新しい修正チャット" in str(res.json())
    # 新しい修正チャットは機密の会話になる
    res = _fix(api_client, manager, task)
    assert res.status_code == 201, res.json()
    assert Run.objects.get(uid=res.json()["id"]).conversation.handles_sensitive_data


def test_tasks_are_not_launched_from_a_fix_chat(api_client, manager):
    task = _task(manager)
    task.set_chat_launchable(True)
    conversation = Run.objects.get(uid=_fix(api_client, manager, task).json()["id"]).conversation
    res = api_client.post(
        f"/api/app/v1/launchable_tasks/{task.uid}/run/?conversation={conversation.uid}&client_request_id=x",
        {"inputs": {"vendor": "A商事"}},
        format="json",
    )
    assert res.status_code == 404


# --- 会話でのやり取り ---


def _post(api_client, conversation, body="インボイス番号も確かめて", **data):
    return api_client.post(
        f"/api/app/v1/conversations/{conversation.uid}/messages/", {"body": body, **data}, format="json"
    )


def test_messages_are_instructions_to_the_running_task(api_client, manager):
    task = _task(manager)
    run = Run.objects.get(uid=_fix(api_client, manager, task).json()["id"])
    res = _post(api_client, run.conversation)
    assert res.status_code == 201, res.json()
    instruction = UserInstruction.objects.get(message__uid=res.json()["id"])
    assert (instruction.kind, instruction.run) == (InstructionKind.CURRENT, run)
    # 添付はタスクのRunのものにする（Runの画面で取得でき、/workspace/input にはこのRunのものだけを置く）
    attachment = ArtifactFactory.create(owner=manager, conversation=run.conversation, kind=ArtifactKind.ATTACHMENT)
    res = _post(api_client, run.conversation, "この表も使って", attachments=[attachment.uid])
    assert res.status_code == 201, res.json()
    attachment.refresh_from_db()
    assert attachment.run == run
    # 次の依頼は、タスクのRunが終わった後のタスクの編集の依頼になる
    res = _post(api_client, run.conversation, "今後もインボイス番号を確かめるようにして", mode="next")
    assert res.status_code == 201, res.json()
    instruction = UserInstruction.objects.get(message__uid=res.json()["id"])
    assert (instruction.kind, instruction.state) == (InstructionKind.NEXT, InstructionState.RECEIVED)
    # 追加の指示はRunnerが取り込んだものとする
    UserInstruction.objects.filter(kind=InstructionKind.CURRENT).update(state=InstructionState.APPLIED)
    run.set_status(RunStatus.CANCELLED)
    instruction.refresh_from_db()
    assert instruction.state == InstructionState.STARTED
    assert (instruction.run.kind, instruction.run.parent) == (RunKind.TASK_EDIT, run)


def test_records_stay_visible_to_managers_after_the_task_is_deleted(api_client, manager):
    """タスクを削除しても、実行したときの範囲を管理できる人には、修正チャットの記録をRunの画面と同じく見せる"""
    task = _task(manager)
    run = Run.objects.get(uid=_fix(api_client, manager, task).json()["id"])
    task.soft_delete(actor=manager)
    conversation = api_client.get(f"/api/app/v1/conversations/{run.conversation.uid}/").json()
    assert conversation["task"]["deleted"]
    messages = api_client.get(f"/api/app/v1/conversations/{run.conversation.uid}/messages/").json()["results"]
    assert [m["run"] for m in messages] == [run.uid]
    # 動かすことと指示は断る
    assert _post(api_client, run.conversation).status_code == 400


def test_instruction_left_when_the_run_ends_becomes_an_edit_request(api_client, manager):
    """終わる直前に届いて取り込めなかった追加の指示は、タスクの編集の依頼として始まる（動かし直すのは「もう一度動かす」）"""
    task = _task(manager)
    run = Run.objects.get(uid=_fix(api_client, manager, task).json()["id"])
    res = _post(api_client, run.conversation)
    assert res.status_code == 201, res.json()
    run.set_status(RunStatus.CANCELLED)
    instruction = UserInstruction.objects.get(message__uid=res.json()["id"])
    assert instruction.state == InstructionState.STARTED
    assert instruction.run.kind == RunKind.TASK_EDIT
    assert not run.conversation.messages.filter(role=MessageRole.SYSTEM).exists()


def test_messages_need_a_manager(api_client, manager):
    task = _task(manager)
    run = Run.objects.get(uid=_fix(api_client, manager, task).json()["id"])
    sent = _post(api_client, run.conversation, client_message_id="msg-1")
    assert sent.status_code == 201, sent.json()
    manager.user_permissions.clear()
    # 再送でも、前のメッセージを返す前に確かめる
    resent = _post(api_client, run.conversation, client_message_id="msg-1")
    assert resent.status_code == 400
    res = _post(api_client, run.conversation)
    assert res.status_code == 400
    assert "管理できなくなりました" in str(res.json())
    # タスクのRunのメッセージ（依頼文から作った最初の依頼）と、実行中のタスクのRunは見せない（会話は開ける）
    conversation = api_client.get(f"/api/app/v1/conversations/{run.conversation.uid}/")
    assert conversation.status_code == 200
    assert conversation.json()["active_run"] is None
    messages = api_client.get(f"/api/app/v1/conversations/{run.conversation.uid}/messages/").json()["results"]
    assert messages == []


# --- 実行 ---


def test_fix_run_can_ask_the_manager(api_client, manager):
    task = _task(manager)
    run = Run.objects.get(uid=_fix(api_client, manager, task).json()["id"])
    adapter = FakeAdapter([reply("確認しました")])
    run = execute(run, adapter)
    assert run.status == RunStatus.COMPLETED, run.error
    first = adapter.requests[0]
    # 固定したSkillがなくても ask_user は出す。Skillの編集の提案は出さない
    assert "ask_user" in [t.name for t in first.tools]
    assert "request_skill_edit" not in [t.name for t in first.tools]
    context = next(m.text for m in first.messages if m.role == "user" and "<runtime_context>" in m.text)
    assert '<mode responder="true">task</mode>' in context
    # 出口へは送らない
    assert not Delivery.objects.filter(run=run).exists()


def test_suspended_task_uses_its_connectors_in_the_fix_chat(api_client, manager):
    connector = ConnectorFactory.create(name="販売管理")
    task = _task(manager, connectors=[connector.uid])
    task.set_suspended(True)
    res = _fix(api_client, manager, task)
    assert res.status_code == 201, res.json()
    run = Run.objects.get(uid=res.json()["id"])
    assert tasks.connector_permitted(run, connector)
    # 持ち主が管理できなくなったら、次の区切りを待たずにConnectorの操作から断る
    manager.user_permissions.clear()
    assert not tasks.connector_permitted(run, connector)


def test_only_the_owner_answers_the_fix_run(api_client, manager):
    task = _task(manager)
    run = Run.objects.get(uid=_fix(api_client, manager, task).json()["id"])
    clarification = ClarificationRequest.objects.create(
        run=run,
        kind=ClarificationKind.QUESTION,
        tool_call_id="c1",
        questions=[{"header": "基準", "question": "一致としますか", "options": [], "multi_select": False}],
    )
    Run.objects.filter(pk=run.pk).update(status=RunStatus.WAITING_USER)
    url = f"/api/app/v1/runs/{run.uid}/clarifications/{clarification.uid}/answer/"
    answer = {"answers": [{"selected": [], "text": "一致とする"}]}
    # 同じタスクを管理する別の人は答えられない（停止はできる）
    colleague = grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)
    api_client.force_login(colleague)
    assert api_client.post(url, answer, format="json").status_code == 403
    api_client.force_login(manager)
    res = api_client.post(url, answer, format="json")
    assert res.status_code == 200, res.json()


def test_fix_run_stops_when_the_manager_loses_the_task(api_client, manager):
    task = _task(manager)
    run = Run.objects.get(uid=_fix(api_client, manager, task).json()["id"])
    manager.user_permissions.clear()
    run = execute(run, FakeAdapter([]))
    assert (run.status, run.end_reason) == (RunStatus.FAILED, RunEndReason.TASK)
    assert "管理できなくなりました" in run.error


def test_fix_run_keeps_the_task_model(api_client, manager):
    """会話のモデルを切り替えても、タスクのRunはタスクのモデル設定のまま。障害でも別のモデルを勧めない"""
    task = _task(manager)
    run = Run.objects.get(uid=_fix(api_client, manager, task).json()["id"])
    other = ModelProfileFactory.create(version__supports_tools=True)
    res = api_client.patch(
        f"/api/app/v1/conversations/{run.conversation.uid}/", {"model_profile": other.uid}, format="json"
    )
    assert res.status_code == 200, res.json()
    model_version = run.model_version_id
    run.refresh_from_db()
    assert run.model_version_id == model_version
    assert switch_candidates(run) == []


def test_runs_of_a_fix_chat_do_not_share_the_workspace(api_client, manager, settings, tmp_path):
    settings.YORIKI_WORKSPACES_DIR = str(tmp_path / "workspaces")
    task = _task(manager)
    first = Run.objects.get(uid=_fix(api_client, manager, task).json()["id"])
    attempt = claim_run(first.pk)
    assert attempt is not None
    Checkpoint.objects.create(
        run=first, attempt=attempt, sequence=1, storage="local:x", key="runs/a.tar", size=1, sha256="0" * 64
    )
    sent = Artifact.objects.create(
        owner=manager,
        conversation=first.conversation,
        blob=BlobObject.put(io.BytesIO(b"a")),
        kind=ArtifactKind.ATTACHMENT,
        name="前回.csv",
        path="前回.csv",
        run=first,
    )
    first.messages.get().attachments.add(sent)
    Artifact.create_connector_result(first, "connectors/c1/result.csv", "result.csv", BlobObject.put(io.BytesIO(b"b")))
    output = Artifact.create_output(first, "report.txt", BlobObject.put(io.BytesIO(b"c")))
    _finish(first)

    second = Run.objects.get(uid=_fix(api_client, manager, task, conversation=first.conversation).json()["id"])
    assert Checkpoint.latest_for(second) is None
    attempt = claim_run(second.pk)
    assert attempt is not None
    session = SandboxSession(second, attempt)
    session.sync_inputs()
    assert not any(session.input_dir.rglob("*.csv"))
    assert session._known_outputs() == {}
    session.close()
    assert Artifact.objects.latest_output(second.conversation_id, "report.txt", run=second) is None
    assert Artifact.objects.latest_output(second.conversation_id, "report.txt") == output


def test_run_page_shows_only_the_messages_of_the_run(api_client, manager):
    task = _task(manager)
    first = _finish(Run.objects.get(uid=_fix(api_client, manager, task).json()["id"]))
    second = Run.objects.get(
        uid=_fix(api_client, manager, task, conversation=first.conversation, inputs={"vendor": "B物産"}).json()["id"]
    )
    res = api_client.get(f"/api/app/v1/tasks/{task.uid}/runs/{second.uid}/messages/")
    assert res.status_code == 200
    [message] = res.json()["results"]
    assert "B物産" in message["body"]


def test_department_task_manager_needs_the_department(api_client):
    department = DepartmentFactory.create()
    manager = grant(UserFactory.create(), AppPermission.MANAGE_DEPARTMENT_TASKS)
    task = TaskDefinitionFactory.create(
        scope=TaskScope.DEPARTMENT, department=department, created_by=manager, revision__inputs=INPUTS
    )
    # 管理できる部門でなければ使えない
    assert _fix(api_client, manager, task).status_code == 404
