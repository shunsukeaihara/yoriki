"""修正チャットからタスクの定義を直す（design.md §11.11、harness.md §10.10）。タスクのRunが動いていないときの
メッセージと、request_task_edit の承認は、タスクの編集のRunになる。直せるのは文章だけで、画面の保存と同じ検査を通す"""

import io
import uuid

import pytest
from django.utils import timezone

from artifact.models import Artifact, ArtifactKind, BlobObject
from conversation.models import ConversationMessage, InstructionKind, InstructionState, MessageRole, UserInstruction
from harness.sandbox import SandboxSession
from harness.tests.fakes import FakeAdapter, calls, reply
from harness.tests.test_connectors import execute, results
from llm.factories import ModelProfileFactory
from run.models import Checkpoint, ClarificationKind, ClarificationRequest, Run, RunEndReason, RunKind, RunStatus
from run.serializers import RunSerializer
from run.tasks import claim_run
from task.factories import TaskDefinitionFactory
from task.models import TaskDefinitionRevision, TaskScope
from user.factories import UserFactory, grant
from utils.permissions import AppPermission

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("default_runtime")]

INPUTS = [{"name": "vendor", "label": "取引先", "type": "string", "required": True, "description": ""}]
PROMPT = "{{ inputs.vendor }} の請求書を確認してください。"


@pytest.fixture(name="manager")
def fixture_manager():
    return grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)


def _task(manager, **revision):
    revision.setdefault("prompt_template", PROMPT)
    revision.setdefault("inputs", INPUTS)
    revision.setdefault("model_profile", ModelProfileFactory.create(version__supports_tools=True))
    return TaskDefinitionFactory.create(
        scope=TaskScope.COMPANY,
        created_by=manager,
        name="請求書チェック",
        **{f"revision__{k}": v for k, v in revision.items()},
    )


def _fix(api_client, user, task, *, conversation=None) -> Run:
    api_client.force_login(user)
    query = f"client_request_id={uuid.uuid4().hex}"
    if conversation is not None:
        query += f"&conversation={conversation.uid}"
    res = api_client.post(
        f"/api/app/v1/tasks/{task.uid}/fix_chat/?{query}", {"inputs": {"vendor": "A商事"}}, format="json"
    )
    assert res.status_code == 201, res.json()
    return Run.objects.get(uid=res.json()["id"])


def _post(api_client, conversation, body="今後はインボイス番号も確かめるように直して", **data):
    return api_client.post(
        f"/api/app/v1/conversations/{conversation.uid}/messages/", {"body": body, **data}, format="json"
    )


def _finished_fix(api_client, manager, task) -> Run:
    run = _fix(api_client, manager, task)
    run.set_status(RunStatus.CANCELLED)
    return run


def _edit(api_client, manager, task, body="今後はインボイス番号も確かめるように直して") -> Run:
    run = _finished_fix(api_client, manager, task)
    res = _post(api_client, run.conversation, body)
    assert res.status_code == 201, res.json()
    message = ConversationMessage.objects.get(uid=res.json()["id"])
    assert message.run is not None
    return message.run


# --- 始め方 ---


def test_message_when_no_task_runs_starts_an_edit_run(api_client, manager):
    task = _task(manager)
    task_run = _finished_fix(api_client, manager, task)
    res = _post(api_client, task_run.conversation)
    assert res.status_code == 201, res.json()
    run = ConversationMessage.objects.get(uid=res.json()["id"]).run
    assert run is not None
    assert (run.kind, run.parent, run.owner, run.task) == (RunKind.TASK_EDIT, task_run, manager, None)
    assert run.manifest["fixed"]["task_edit"] == {"task": task.uid, "name": task.name}
    # モデルは修正チャットのモデル設定（始めたときはタスクのもの）
    assert run.model_version == task.current_revision.model_profile.current_version
    adapter = FakeAdapter([reply("前回の結果は一致でした")])
    run = execute(run, adapter)
    assert run.status == RunStatus.COMPLETED, run.error
    request = adapter.requests[0]
    names = {t.name for t in request.tools}
    assert {"get_task", "update_task", "ask_user", "todo_write"} <= names
    assert not names & {"skill", "submit_result", "request_task_edit", "request_skill_edit", "prepare_notification"}
    assert not any(name.startswith("datasource") for name in names)
    context = next(m.text for m in request.messages if m.role == "user" and "<runtime_context>" in m.text)
    assert '<task_edit task="請求書チェック" version="1" />' in context
    # 直前のタスクのRunの文脈を引き継ぐ
    assert any("A商事" in m.text for m in request.messages if m.role == "user")


def test_edit_run_needs_a_manager_when_it_starts(api_client, manager):
    """承認した編集の依頼も、始めるときに依頼した人がタスクを管理できるかを確かめる"""
    task = _task(manager)
    run = _fix(api_client, manager, task)
    res = _post(api_client, run.conversation, mode="next")
    assert res.status_code == 201, res.json()
    manager.user_permissions.clear()
    run.set_status(RunStatus.CANCELLED)
    instruction = UserInstruction.objects.get(message__uid=res.json()["id"])
    assert instruction.state == InstructionState.DISCARDED
    notice = run.conversation.messages.get(role=MessageRole.SYSTEM)
    assert "管理できなくなりました" in notice.body


def test_task_that_became_sensitive_is_not_edited_in_a_plain_chat(api_client, manager):
    task = _task(manager)
    run = _finished_fix(api_client, manager, task)
    # 版の中身は変えないが、ここでは機密になった版の代わりに今の版の印を変える
    TaskDefinitionRevision.objects.filter(pk=task.current_revision_id).update(handles_sensitive_data=True)
    res = _post(api_client, run.conversation)
    assert res.status_code == 400
    assert "新しい修正チャット" in str(res.json())


def test_edit_run_stops_when_the_manager_loses_the_task(api_client, manager):
    task = _task(manager)
    run = _edit(api_client, manager, task)
    manager.user_permissions.clear()
    run = execute(run, FakeAdapter([]))
    assert (run.status, run.end_reason) == (RunStatus.FAILED, RunEndReason.TASK)
    assert "管理できなくなりました" in run.error


# --- 直す ---


def test_update_task_saves_a_new_version(api_client, manager):
    task = _task(manager)
    run = _edit(api_client, manager, task)
    new_prompt = PROMPT + "\nインボイス番号も確かめてください。"
    adapter = FakeAdapter(
        [
            calls(("c1", "get_task", {})),
            calls(
                (
                    "c2",
                    "update_task",
                    {
                        "expected_version": 1,
                        "change_reason": "インボイス番号の確認を足した",
                        "prompt_template": new_prompt,
                        "input_descriptions": {"vendor": "請求書の発行元"},
                    },
                )
            ),
            reply("第2版を保存しました"),
        ]
    )
    run = execute(run, adapter)
    assert run.status == RunStatus.COMPLETED, run.error
    [read] = results(run, "get_task")
    assert "<prompt_template>" in read["content"][0]["text"]
    assert '"name": "vendor"' in read["content"][0]["text"]
    task.refresh_from_db()
    revision = task.current_revision
    assert (revision.number, revision.prompt_template, revision.created_by) == (2, new_prompt, manager)
    assert revision.inputs[0]["description"] == "請求書の発行元"
    assert revision.change_reason == "インボイス番号の確認を足した"
    # 結果には、タスクの名前・版・変えた項目の変更の前後を出す
    [change] = RunSerializer(run).data["task_changes"]
    assert (change["task"], change["name"], change["version"]) == (task.uid, "請求書チェック", 2)
    assert [(c["field"], c["input"], c["before"], c["after"]) for c in change["changes"]] == [
        ("prompt_template", "", PROMPT, new_prompt),
        ("input_description", "取引先", "", "請求書の発行元"),
    ]


def _update(run: Run, arguments: dict) -> dict:
    run = execute(run, FakeAdapter([calls(("c1", "update_task", arguments)), reply("終わりました")]))
    [result] = results(run, "update_task")
    return result


def test_update_task_uses_the_checks_of_the_screen(api_client, manager):
    task = _task(manager)
    run = _edit(api_client, manager, task)
    result = _update(
        run, {"expected_version": 1, "change_reason": "x", "prompt_template": "{{ inputs.amount }} を確かめて"}
    )
    assert result["is_error"]
    assert "入力の項目にない名前" in result["content"][0]["text"]
    task.refresh_from_db()
    assert task.version_number == 1


def test_update_task_does_not_change_the_shape_of_inputs(api_client, manager):
    task = _task(manager)
    run = _edit(api_client, manager, task)
    result = _update(run, {"expected_version": 1, "change_reason": "x", "input_descriptions": {"amount": "金額"}})
    assert result["is_error"]
    assert "task screen" in result["content"][0]["text"]


def test_update_task_refuses_a_stale_version(api_client, manager):
    task = _task(manager)
    run = _edit(api_client, manager, task)
    result = _update(run, {"expected_version": 2, "change_reason": "x", "prompt_template": PROMPT + "。"})
    assert result["is_error"]
    assert "get_task" in result["content"][0]["text"]
    task.refresh_from_db()
    assert task.version_number == 1


def test_update_task_with_the_same_content_keeps_the_version(api_client, manager):
    task = _task(manager)
    run = _edit(api_client, manager, task)
    result = _update(run, {"expected_version": 1, "change_reason": "x", "prompt_template": PROMPT})
    assert not result["is_error"]
    assert "Nothing changed" in result["content"][0]["text"]
    task.refresh_from_db()
    assert task.version_number == 1
    assert RunSerializer(Run.objects.get(pk=run.pk)).data["task_changes"] == []


def test_update_task_needs_the_manager_on_every_call(api_client, manager):
    task = _task(manager)
    run = _edit(api_client, manager, task)
    attempt = claim_run(run.pk)
    assert attempt is not None
    manager.user_permissions.clear()
    from harness.tools.base import ToolContext  # noqa: PLC0415
    from harness.tools.task_admin import UPDATE_TASK  # noqa: PLC0415
    from llm.providers.base import ToolCall  # noqa: PLC0415

    arguments = {"expected_version": 1, "change_reason": "x", "prompt_template": PROMPT + "。"}
    result = UPDATE_TASK.execute(arguments, ToolContext(run, attempt, ToolCall("c1", "update_task", arguments)))
    assert result.is_error
    assert "管理できなくなりました" in str(result.content[0])


def test_other_fields_stay_on_the_task_screen(api_client, manager):
    """get_task は、文章のほかはタスクの画面で直すことを伝える"""
    task = _task(manager)
    run = _edit(api_client, manager, task)
    run = execute(run, FakeAdapter([calls(("c1", "get_task", {})), reply("読みました")]))
    [read] = results(run, "get_task")
    assert "task screen" in read["content"][0]["text"]


# --- タスクのRunからの提案 ---


def test_request_task_edit_asks_and_queues_an_edit_request(api_client, manager):
    task = _task(manager)
    run = _fix(api_client, manager, task)
    adapter = FakeAdapter([calls(("c1", "request_task_edit", {"summary": "インボイス番号も確かめる"}))])
    run = execute(run, adapter)
    assert "request_task_edit" in {t.name for t in adapter.requests[0].tools}
    assert run.status == RunStatus.WAITING_USER
    clarification = ClarificationRequest.objects.get(run=run)
    assert clarification.kind == ClarificationKind.TASK_EDIT
    assert "タスク「請求書チェック」の定義を直しますか？" in clarification.questions[0]["question"]
    url = f"/api/app/v1/runs/{run.uid}/clarifications/{clarification.uid}/answer/"
    res = api_client.post(url, {"answers": [{"selected": ["タスクを直す"], "text": ""}]}, format="json")
    assert res.status_code == 200, res.json()
    [result] = results(run, "request_task_edit")
    assert "approved" in result["content"][0]["text"]
    instruction = UserInstruction.objects.get(kind=InstructionKind.NEXT)
    assert instruction.message.body == "インボイス番号も確かめる"
    # タスクのRunが終わると、編集のRunが始まる
    run.refresh_from_db()
    run.set_status(RunStatus.CANCELLED)
    instruction.refresh_from_db()
    assert instruction.state == InstructionState.STARTED
    assert instruction.run is not None
    assert instruction.run.kind == RunKind.TASK_EDIT


def test_declined_task_edit_queues_nothing(api_client, manager):
    task = _task(manager)
    run = _fix(api_client, manager, task)
    run = execute(run, FakeAdapter([calls(("c1", "request_task_edit", {"summary": "インボイス番号も確かめる"}))]))
    clarification = ClarificationRequest.objects.get(run=run)
    url = f"/api/app/v1/runs/{run.uid}/clarifications/{clarification.uid}/answer/"
    res = api_client.post(url, {"answers": [{"selected": ["今回だけにする"], "text": ""}]}, format="json")
    assert res.status_code == 200, res.json()
    assert not UserInstruction.objects.filter(kind=InstructionKind.NEXT).exists()


def test_message_while_asking_answers_the_task_edit(api_client, manager):
    """確認待ちのときのメッセージは、タスクの編集の確認への自由記述の回答（承認にはしない）"""
    task = _task(manager)
    run = _fix(api_client, manager, task)
    run = execute(run, FakeAdapter([calls(("c1", "request_task_edit", {"summary": "インボイス番号も確かめる"}))]))
    res = _post(api_client, run.conversation, "今回はいいです")
    assert res.status_code == 201, res.json()
    clarification = ClarificationRequest.objects.get(run=run)
    assert clarification.comment == "今回はいいです"
    assert not UserInstruction.objects.filter(kind=InstructionKind.NEXT).exists()


# --- 作業ファイル ---


def test_edit_run_sees_the_files_of_the_last_task_run(api_client, manager, settings, tmp_path):
    settings.YORIKI_WORKSPACES_DIR = str(tmp_path / "workspaces")
    task = _task(manager)
    first = _finished_fix(api_client, manager, task)
    conversation = first.conversation
    second = _fix(api_client, manager, task, conversation=conversation)

    def attach(run: Run, name: str, content: bytes) -> None:
        artifact = Artifact.objects.create(
            owner=manager,
            conversation=conversation,
            blob=BlobObject.put(io.BytesIO(content)),
            kind=ArtifactKind.ATTACHMENT,
            name=name,
            path=name,
            run=run,
        )
        run.messages.order_by("pk")[0].attachments.add(artifact)

    # 実行ごとに /workspace/input を分けるので、同じ名前の添付がある
    attach(first, "請求書.csv", b"first")
    attach(second, "請求書.csv", b"second")
    attempt = claim_run(second.pk)
    assert attempt is not None
    Checkpoint.objects.create(
        run=second, attempt=attempt, sequence=1, storage="local:x", key="runs/b.tar", size=1, sha256="0" * 64
    )
    Run.objects.filter(pk=second.pk).update(status=RunStatus.COMPLETED)
    res = _post(api_client, conversation)
    assert res.status_code == 201, res.json()
    run = ConversationMessage.objects.get(uid=res.json()["id"]).run
    assert run is not None
    # 作業ファイルは直前のタスクのRunのCheckpointから
    checkpoint = Checkpoint.latest_for(run)
    assert checkpoint is not None
    assert checkpoint.run == second
    attempt = claim_run(run.pk)
    assert attempt is not None
    session = SandboxSession(run, attempt)
    session.sync_inputs()
    assert (session.input_dir / "請求書.csv").read_bytes() == b"second"
    session.close()


def test_edit_run_does_not_restore_files_of_earlier_runs(api_client, manager):
    """直前のタスクのRunがCheckpointを残さなければ、さらに前の実行の作業ファイルではなく空のWorkspaceから始める"""
    task = _task(manager)
    first = _fix(api_client, manager, task)
    attempt = claim_run(first.pk)
    assert attempt is not None
    Checkpoint.objects.create(
        run=first, attempt=attempt, sequence=1, storage="local:x", key="runs/a.tar", size=1, sha256="0" * 64
    )
    Run.objects.filter(pk=first.pk).update(status=RunStatus.COMPLETED)
    _finished_fix_in(api_client, manager, task, first.conversation)
    res = _post(api_client, first.conversation)
    assert res.status_code == 201, res.json()
    run = ConversationMessage.objects.get(uid=res.json()["id"]).run
    assert run is not None
    assert Checkpoint.latest_for(run) is None


def _finished_fix_in(api_client, manager, task, conversation) -> Run:
    run = _fix(api_client, manager, task, conversation=conversation)
    run.set_status(RunStatus.CANCELLED)
    return run


def test_update_task_is_not_saved_when_the_manager_is_revoked_meanwhile(api_client, manager, monkeypatch):
    """確かめてから保存するまでの間に権限が外れたら、保存を取りやめる"""
    from harness.tools import task_admin  # noqa: PLC0415

    task = _task(manager)
    run = _edit(api_client, manager, task)
    checks = iter(["", "修正チャットの持ち主がこのタスクを管理できなくなりました"])
    monkeypatch.setattr(task_admin, "target_problem", lambda _run, _task: next(checks))
    result = _update(run, {"expected_version": 1, "change_reason": "x", "prompt_template": PROMPT + "。"})
    assert result["is_error"]
    assert "not saved" in result["content"][0]["text"]
    task.refresh_from_db()
    assert task.version_number == 1


def test_fix_chat_model_needs_tools(api_client, manager):
    task = _task(manager)
    run = _finished_fix(api_client, manager, task)
    other = ModelProfileFactory.create(version__supports_tools=False)
    res = api_client.patch(
        f"/api/app/v1/conversations/{run.conversation.uid}/", {"model_profile": other.uid}, format="json"
    )
    assert res.status_code == 400
    assert "Tool" in str(res.json())


def test_attachments_of_the_next_request_are_not_the_task_runs(api_client, manager):
    """タスクのRunの実行中に送った次の依頼（タスクの編集の依頼）の添付は、タスクのRunの添付にしない"""
    from artifact.factories import ArtifactFactory  # noqa: PLC0415

    task = _task(manager)
    run = _fix(api_client, manager, task)
    attachment = ArtifactFactory.create(owner=manager, conversation=run.conversation, kind=ArtifactKind.ATTACHMENT)
    res = _post(api_client, run.conversation, "この表の形に合わせて直して", mode="next", attachments=[attachment.uid])
    assert res.status_code == 201, res.json()
    attachment.refresh_from_db()
    assert attachment.run is None


def test_update_task_is_not_saved_by_an_attempt_that_lost_the_lease(api_client, manager):
    from harness.tools.base import ToolContext  # noqa: PLC0415
    from harness.tools.task_admin import UPDATE_TASK  # noqa: PLC0415
    from llm.providers.base import ToolCall  # noqa: PLC0415
    from run.errors import LeaseLost  # noqa: PLC0415
    from run.models import ExecutionAttempt  # noqa: PLC0415

    task = _task(manager)
    run = _edit(api_client, manager, task)
    attempt = claim_run(run.pk)
    assert attempt is not None
    # 別のAttemptが引き継いだ（このAttemptは終わった）
    ExecutionAttempt.objects.filter(pk=attempt.pk).update(ended=timezone.now())
    arguments = {"expected_version": 1, "change_reason": "x", "prompt_template": PROMPT + "。"}
    with pytest.raises(LeaseLost):
        UPDATE_TASK.execute(arguments, ToolContext(run, attempt, ToolCall("c1", "update_task", arguments)))
    task.refresh_from_db()
    assert task.version_number == 1


def test_edit_run_collects_outputs_like_those_of_earlier_runs(api_client, manager, settings, tmp_path):
    """前のタスクのRunと同じパス・中身の成果物も、編集のRunの成果物として回収する"""
    settings.YORIKI_WORKSPACES_DIR = str(tmp_path / "workspaces")
    task = _task(manager)
    first = _fix(api_client, manager, task)
    Artifact.create_output(first, "report.txt", BlobObject.put(io.BytesIO(b"c")))
    Run.objects.filter(pk=first.pk).update(status=RunStatus.COMPLETED)
    second = _finished_fix_in(api_client, manager, task, first.conversation)
    res = _post(api_client, first.conversation)
    assert res.status_code == 201, res.json()
    run = ConversationMessage.objects.get(uid=res.json()["id"]).run
    assert run is not None
    attempt = claim_run(run.pk)
    assert attempt is not None
    session = SandboxSession(run, attempt)
    assert session._known_outputs() == {}
    session.close()
    assert second.pk < run.pk
