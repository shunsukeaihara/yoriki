"""会話からのSkill更新（harness.md §10.9、design.md §11.6）。編集のRunの始め方、Skill管理のTool、提案の確認"""

from typing import Any

import pytest

from conversation.dispatch import MessageMode, post_message
from conversation.factories import ConversationFactory
from conversation.models import ConversationMessage, InstructionKind, MessageRole, UserInstruction
from harness.runner import Runner
from harness.sandbox import SandboxSession
from resources.factories import ResourceVersionFactory
from run.models import (
    ClarificationKind,
    ClarificationRequest,
    Run,
    RunEvent,
    RunEventKind,
    RunKind,
    RunStatus,
)
from run.serializers import RunSerializer, skill_changes
from run.tasks import claim_run
from skill.factories import SkillFactory, SkillVersionFactory
from skill.models import (
    Skill,
    SkillAssetBinding,
    SkillAssetRole,
    SkillChange,
    SkillDraft,
    SkillScope,
)
from user.factories import DepartmentFactory, DepartmentMembershipFactory, grant
from utils.permissions import AppPermission

from .fakes import FakeAdapter, calls, reply

pytestmark = pytest.mark.usefixtures("default_runtime")


class FileSession(SandboxSession):
    """Dockerを使わず、Sandboxの中のファイルを辞書で持つSandboxSession（資料の配置はWorkerのディレクトリに行う）"""

    files: dict[str, bytes] = {}

    def ensure(self):
        return None

    def helper(self, command: str, request: dict[str, Any]) -> dict[str, Any]:
        assert command == "stat"
        body = self.files.get(request["path"])
        if body is None:
            return {"ok": True, "path": request["path"], "exists": False}
        return {"ok": True, "path": request["path"], "exists": True, "type": "file", "size": len(body)}

    def read_file(self, path: str, limit: int) -> bytes:
        return self.files[path]


@pytest.fixture(autouse=True)
def fixture_workspaces(settings, tmp_path):
    settings.YORIKI_WORKSPACES_DIR = str(tmp_path / "workspaces")
    FileSession.files = {}


@pytest.fixture(name="conversation")
def fixture_conversation(app_user):
    return ConversationFactory.create(owner=app_user)


def publish(owner, name="請求書の照合", **kwargs) -> Skill:
    skill = SkillFactory.create(name=name, created_by=owner, **kwargs)
    SkillVersionFactory.create(skill=skill, tools=["read"])
    return skill


def post(conversation, body="今後はNotify Partyが空でもエラーにしない", **kwargs) -> Run:
    post_message(
        conversation, body=body, client_message_id="", mode=MessageMode.AUTO, actor=conversation.owner, **kwargs
    )
    return Run.objects.filter(conversation=conversation).latest("pk")


def execute(run: Run, adapter: FakeAdapter) -> Run:
    attempt = claim_run(run.pk)
    assert attempt is not None
    Runner(attempt, adapter_factory=adapter, sleep=lambda _s: None, session_factory=FileSession).run()
    run.refresh_from_db()
    return run


def results(run: Run, name: str) -> list[dict[str, Any]]:
    return [
        e.data
        for e in RunEvent.objects.filter(run=run, kind=RunEventKind.TOOL_RESULT).order_by("sequence")
        if e.data["name"] == name
    ]


def text_of(result: dict[str, Any]) -> str:
    return "".join(part.get("text", "") for part in result["content"])


def edit(skill: Skill) -> dict[str, str]:
    return {"id": skill.uid, "name": skill.name}


@pytest.mark.django_db
def test_edit_request_starts_a_skill_edit_run_with_the_target_fixed(conversation, app_user):
    skill = publish(app_user)
    run = post(conversation, skill_edit=edit(skill))
    adapter = FakeAdapter([reply("確認しました。")])
    run = execute(run, adapter)

    assert run.kind == RunKind.SKILL_EDIT
    assert run.manifest["fixed"]["skill_edit"] == {"skill": skill.uid, "name": skill.name}
    assert run.manifest["fixed"]["skill_catalog"] is None
    names = {t.name for t in adapter.requests[0].tools}
    assert {"get_skill", "update_skill_draft", "stage_skill_asset", "publish_skill_draft", "rollback_skill"} <= names
    # 対象が決まっているので作らない。Skillを使う・提案するToolも出さない
    assert not names & {"create_skill", "skill", "request_skill_edit", "submit_result"}
    context = next(m.text for m in adapter.requests[0].messages if m.text.startswith("<runtime_context>"))
    assert f'<skill_edit skill="{skill.name}"' in context
    assert 'your_access="publish"' in context


@pytest.mark.django_db
def test_edit_run_updates_stages_and_publishes_the_skill(conversation, app_user):
    skill = publish(app_user)
    master = ResourceVersionFactory.create(name="vendor-master.csv", content=b"code,name\n001,A\n")
    assert skill.current_version is not None
    SkillAssetBinding.objects.create(
        version=skill.current_version, key="vendor-master", role=SkillAssetRole.MASTER, resource_version=master
    )
    FileSession.files = {"/workspace/input/new-master.csv": b"code,name\n001,A\n002,B\n"}
    run = post(conversation, "添付のCSVを今後の名寄せマスターにして", skill_edit=edit(skill))
    adapter = FakeAdapter(
        [
            calls(("c1", "get_skill", {})),
            calls(
                (
                    "c2",
                    "update_skill_draft",
                    {"expected_revision": 0, "instructions": "1. 請求書を読む\n2. Notify Partyは空でもよい"},
                )
            ),
            calls(
                (
                    "c3",
                    "stage_skill_asset",
                    {"expected_revision": 2, "key": "vendor-master", "path": "/workspace/input/new-master.csv"},
                )
            ),
            calls(("c4", "publish_skill_draft", {"expected_revision": 3, "change_reason": "マスターを更新"})),
            reply("第2版を公開しました。"),
        ]
    )
    run = execute(run, adapter)

    assert run.status == RunStatus.COMPLETED
    got = text_of(results(run, "get_skill")[0])
    assert f"/workspace/master/skills/{skill.uid}/vendor-master/vendor-master.csv" in got
    assert "<draft>none" in got
    skill.refresh_from_db()
    version = skill.current_version
    assert version is not None and version.number == 2
    assert "Notify Party" in version.instructions
    binding = version.assets.get(key="vendor-master")
    # 添付の原本のbytesのまま、同じ資料の新しい版として置き換える
    assert binding.resource_version.resource_id == master.resource_id
    assert binding.resource_version.number == master.number + 1
    with binding.resource_version.blob.open() as src:
        assert src.read() == b"code,name\n001,A\n002,B\n"
    change = SkillChange.objects.get(skill=skill, version=version)
    assert change.run == run
    assert change.reason == "マスターを更新"
    assert not SkillDraft.objects.filter(skill=skill).exists()
    [summary] = skill_changes(run)
    assert summary["state"] == "published"
    assert summary["version"] == 2
    assert summary["summary"]["fields"] == ["instructions"]
    assert summary["summary"]["assets_replaced"] == ["vendor-master"]


@pytest.mark.django_db
def test_staged_files_are_checked_against_the_published_version(conversation, app_user):
    skill = publish(app_user)
    master = ResourceVersionFactory.create(name="vendor-master.csv", content=b"code,name\n001,A\n002,B\n")
    assert skill.current_version is not None
    SkillAssetBinding.objects.create(
        version=skill.current_version, key="vendor-master", role=SkillAssetRole.MASTER, resource_version=master
    )
    FileSession.files = {
        # 表計算ソフトで保存して先頭の0が消えたもの
        "/workspace/work/broken.csv": b"code,name\n1,A\n2,Bee\n",
        "/workspace/work/vendor-master.csv": b"code,name\n001,A\n002,Bee\n",
    }
    run = post(conversation, "マスターの002の名前をBeeにして", skill_edit=edit(skill))
    stage = {"expected_revision": 0, "key": "vendor-master", "path": "/workspace/work/broken.csv"}
    adapter = FakeAdapter(
        [
            calls(("c1", "stage_skill_asset", stage)),
            calls(("c2", "publish_skill_draft", {"expected_revision": 2, "change_reason": "名前を直す"})),
            calls(
                (
                    "c3",
                    "stage_skill_asset",
                    {
                        "expected_revision": 2,
                        "key": "vendor-master",
                        "path": "/workspace/work/vendor-master.csv",
                        "checks": {"key_columns": ["code"]},
                    },
                )
            ),
            calls(("c4", "publish_skill_draft", {"expected_revision": 3, "change_reason": "名前を直す"})),
            reply("第2版を公開しました。"),
        ]
    )
    run = execute(run, adapter)

    first, fixed = results(run, "stage_skill_asset")
    assert "cannot be published" in text_of(first)
    assert "先頭の0が消えています" in text_of(first)
    blocked = results(run, "publish_skill_draft")[0]
    assert blocked["is_error"] and "検証の誤り" in text_of(blocked)
    # キーの列を決めると、行ごとの差分をエージェントに見せる
    assert "changed 002 name: 'B' -> 'Bee'" in text_of(fixed)
    skill.refresh_from_db()
    assert skill.current_version is not None
    binding = skill.current_version.assets.get(key="vendor-master")
    assert binding.checks["key_columns"] == ["code"]
    assert binding.validation["edited"] is True
    [summary] = skill_changes(run)
    assert summary["state"] == "published"
    assert summary["asset_diffs"] == {"vendor-master": {"rows_added": 0, "rows_removed": 0, "rows_changed": 1}}
    [shown] = RunSerializer(run).data["skill_changes"]
    assert shown["asset_diffs"] == [{"key": "vendor-master", "rows_added": 0, "rows_removed": 0, "rows_changed": 1}]


@pytest.mark.django_db
def test_editor_without_publish_role_saves_a_draft(conversation, app_user):
    department = DepartmentFactory.create(name="経理部")
    DepartmentMembershipFactory.create(department=department, user=app_user)
    grant(app_user, AppPermission.EDIT_DEPARTMENT_SKILLS)
    skill = publish(app_user, scope=SkillScope.DEPARTMENT, department=department)
    run = post(conversation, skill_edit=edit(skill))
    adapter = FakeAdapter(
        [
            calls(("c1", "update_skill_draft", {"expected_revision": 0, "tools": ["read", "shell"]})),
            calls(("c2", "update_skill_draft", {"expected_revision": 0, "description": "B/Lを確認する"})),
            calls(("c3", "publish_skill_draft", {"expected_revision": 2, "change_reason": "説明を直す"})),
            reply("変更案を保存しました。"),
        ]
    )
    run = execute(run, adapter)

    # Toolは編集の役割では変えられない（開いたDraftも残さない）
    first = results(run, "update_skill_draft")[0]
    assert first["is_error"] and "公開の権限" in text_of(first)
    published = results(run, "publish_skill_draft")[0]
    assert not published["is_error"]
    assert "Not published" in text_of(published)
    skill.refresh_from_db()
    assert skill.version_number == 1
    draft = SkillDraft.objects.get(skill=skill)
    assert draft.description == "B/Lを確認する"
    assert draft.tools == ["read"]
    [summary] = skill_changes(run)
    assert summary["state"] == "draft_saved"
    assert summary["summary"]["fields"] == ["description"]


@pytest.mark.django_db
def test_draft_changed_by_someone_else_is_a_conflict(conversation, app_user):
    skill = publish(app_user)
    SkillDraft.open(skill, actor=app_user)
    run = post(conversation, skill_edit=edit(skill))
    adapter = FakeAdapter(
        [
            calls(("c1", "update_skill_draft", {"expected_revision": 0, "description": "x"})),
            calls(("c2", "update_skill_draft", {"expected_revision": 5, "description": "x"})),
            reply("競合しました。"),
        ]
    )
    run = execute(run, adapter)

    first, second = results(run, "update_skill_draft")
    assert first["is_error"] and "revision 1" in text_of(first)
    assert second["is_error"] and "revision 1" in text_of(second)
    assert SkillDraft.objects.get(skill=skill).revision == 1


@pytest.mark.django_db
def test_create_request_creates_one_skill_and_fixes_it_as_the_target(conversation, app_user):
    run = post(conversation, "この手順をSkillにして", skill_edit={"id": "", "name": ""})
    created = {
        "name": "B/L確認",
        "scope": "personal",
        "description": "B/Lを確認するときに使う",
        "instructions": "1. B/Lを読む",
    }
    adapter = FakeAdapter(
        [
            calls(("c1", "create_skill", created)),
            calls(("c2", "create_skill", {**created, "name": "もう1つ"})),
            calls(("c3", "publish_skill_draft", {"expected_revision": 1, "change_reason": "作成"})),
            reply("作りました。"),
        ]
    )
    run = execute(run, adapter)

    assert "create_skill" in {t.name for t in adapter.requests[0].tools}
    first, second = results(run, "create_skill")
    assert not first["is_error"]
    assert second["is_error"]
    skill = Skill.objects.get(name="B/L確認")
    assert skill.scope == SkillScope.PERSONAL and skill.owner == app_user
    assert skill.version_number == 1
    assert [e for e in run.manifest["appended"] if e["kind"] == "skill_edit"] == [
        {"kind": "skill_edit", "at": run.manifest["appended"][0]["at"], "skill": skill.uid, "name": "B/L確認"}
    ]
    assert not Skill.objects.filter(name="もう1つ").exists()
    assert [c["state"] for c in skill_changes(run)] == ["published"]


@pytest.mark.django_db
def test_create_in_a_department_needs_the_role(conversation, app_user):
    DepartmentFactory.create(name="経理部")
    run = post(conversation, "この手順をSkillにして", skill_edit={"id": "", "name": ""})
    created = {"name": "照合", "scope": "department", "department": "経理部", "description": "d", "instructions": "i"}
    adapter = FakeAdapter([calls(("c1", "create_skill", created)), reply("作れませんでした。")])
    run = execute(run, adapter)

    [result] = results(run, "create_skill")
    assert result["is_error"] and "personal" in text_of(result)
    assert not Skill.objects.exists()


@pytest.mark.django_db
def test_publish_is_not_repeated_when_the_call_is_replayed(conversation, app_user):
    skill = publish(app_user)
    run = post(conversation, skill_edit=edit(skill))
    adapter = FakeAdapter(
        [
            calls(("c1", "update_skill_draft", {"expected_revision": 0, "description": "x"})),
            calls(("c2", "publish_skill_draft", {"expected_revision": 2, "change_reason": "r"})),
            reply("公開しました。"),
        ]
    )
    run = execute(run, adapter)
    invocation = run.tool_invocations.get(call_id="c2")
    assert invocation.operation_key == f"run-tool:{invocation.uid}"

    # 同じ呼出しのやり直し（前の試みが公開を済ませた後に落ちた）では、前の結果を返す
    from harness.tools.base import ToolContext  # noqa: PLC0415
    from harness.tools.skill_admin import execute_publish_skill_draft  # noqa: PLC0415
    from llm.providers.base import ToolCall  # noqa: PLC0415

    attempt = run.attempts.latest("number")
    context = ToolContext(
        run, attempt, ToolCall("c2", "publish_skill_draft", {}), invocation=invocation.uid, session=None
    )
    result = execute_publish_skill_draft({"expected_revision": 2, "change_reason": "r"}, context)
    assert not result.is_error
    assert result.value["version"] == 2
    assert skill.versions.count() == 2


@pytest.mark.django_db
def test_request_skill_edit_asks_and_starts_an_edit_run_after_the_work(conversation, app_user):
    skill = publish(app_user)
    run = post(conversation, "今後はNotify Partyが空でもエラーにしないで。Skillも直しておいて")
    adapter = FakeAdapter(
        [
            calls(("c1", "request_skill_edit", {"skill": skill.name, "summary": "Notify Partyが空でもエラーにしない"})),
            reply("今回の確認ではNotify Partyを見ずに完了しました。"),
            reply("Skillを確認しました。"),
        ]
    )
    run = execute(run, adapter)

    assert run.status == RunStatus.WAITING_USER
    clarification = ClarificationRequest.objects.get(run=run)
    assert clarification.kind == ClarificationKind.SKILL_EDIT
    assert clarification.context["skill"] == skill.uid
    assert skill.name in clarification.questions[0]["question"]
    edit_label = clarification.questions[0]["options"][0]["label"]
    clarification.accept_answer([{"selected": [edit_label], "text": ""}], "", app_user)

    message = ConversationMessage.objects.get(conversation=conversation, skill_edit__id=skill.uid)
    assert message.role == MessageRole.USER and message.body == "Notify Partyが空でもエラーにしない"
    assert UserInstruction.objects.get(message=message).kind == InstructionKind.NEXT
    [result] = results(run, "request_skill_edit")
    assert result["value"]["approved"] is True
    assert "separate request" in text_of(result)

    run = execute(run, adapter)
    assert run.status == RunStatus.COMPLETED
    edit_run = Run.objects.get(conversation=conversation, kind=RunKind.SKILL_EDIT)
    assert edit_run.parent == run
    assert edit_run.manifest["fixed"]["skill_edit"]["skill"] == skill.uid


@pytest.mark.django_db
def test_declined_skill_edit_applies_only_this_time(conversation, app_user):
    skill = publish(app_user)
    run = post(conversation)
    adapter = FakeAdapter([calls(("c1", "request_skill_edit", {"skill": skill.name, "summary": "s"}))])
    run = execute(run, adapter)
    clarification = ClarificationRequest.objects.get(run=run)
    once = clarification.questions[0]["options"][1]["label"]
    clarification.accept_answer([{"selected": [once], "text": ""}], "", app_user)

    [result] = results(run, "request_skill_edit")
    assert result["value"]["approved"] is False
    assert not ConversationMessage.objects.exclude(skill_edit={}).exists()


@pytest.mark.django_db
def test_free_text_answer_to_the_skill_edit_question_is_not_an_approval(conversation, app_user):
    skill = publish(app_user)
    run = post(conversation)
    run = execute(run, FakeAdapter([calls(("c1", "request_skill_edit", {"skill": skill.name, "summary": "s"}))]))
    post(conversation, "やっぱり今回だけでいい")

    [result] = results(run, "request_skill_edit")
    assert result["value"]["approved"] is False
    assert "やっぱり今回だけでいい" in text_of(result)


@pytest.mark.django_db
def test_request_skill_edit_names_only_skills_the_user_can_edit(conversation, app_user, other_user):
    department = DepartmentFactory.create(name="経理部")
    DepartmentMembershipFactory.create(department=department, user=app_user)
    publish(other_user, name="部門の照合", scope=SkillScope.DEPARTMENT, department=department)
    mine = publish(app_user, name="個人の照合")
    run = post(conversation)
    run = execute(
        run,
        FakeAdapter(
            [calls(("c1", "request_skill_edit", {"skill": "部門の照合", "summary": "s"})), reply("今回だけにします。")]
        ),
    )

    [result] = results(run, "request_skill_edit")
    assert result["is_error"]
    assert mine.name in text_of(result)
    assert run.status == RunStatus.COMPLETED
    assert not ClarificationRequest.objects.exists()


@pytest.mark.django_db
def test_edit_run_does_not_start_without_edit_access(conversation, app_user, other_user):
    department = DepartmentFactory.create()
    DepartmentMembershipFactory.create(department=department, user=app_user)
    grant(app_user, AppPermission.EDIT_DEPARTMENT_SKILLS)
    skill = publish(other_user, scope=SkillScope.DEPARTMENT, department=department)
    busy = post(conversation, "先に別の作業")
    post(conversation, skill_edit=edit(skill))
    app_user.user_permissions.clear()
    execute(busy, FakeAdapter([reply("終わりました。")]))

    assert not Run.objects.filter(kind=RunKind.SKILL_EDIT).exists()
    system = ConversationMessage.objects.get(conversation=conversation, role=MessageRole.SYSTEM)
    assert "編集する権限がありません" in system.body


@pytest.mark.django_db
def test_removed_asset_drops_its_earlier_diff_counts(conversation, app_user):
    skill = publish(app_user)
    run = post(conversation, skill_edit=edit(skill))
    base = {"skill": skill.uid, "name": skill.name, "scope": "個人", "version": None}
    for value in (
        {
            **base,
            "state": "draft_saved",
            "revision": 2,
            "summary": {"assets_replaced": ["a", "b"]},
            "asset_diffs": {"a": {"rows_changed": 1}, "b": {"rows_changed": 2}},
        },
        {**base, "state": "draft_saved", "revision": 3, "summary": {"assets_removed": ["a"]}, "asset_diffs": {}},
        # 公開中の版と同じ中身で置き換え直すと、差分はなくなる
        {**base, "state": "draft_saved", "revision": 4, "summary": {"assets_replaced": ["b"]}, "asset_diffs": {}},
    ):
        RunEvent.record(run, RunEventKind.TOOL_RESULT, {"name": "stage_skill_asset", "is_error": False, "value": value})
    [summary] = skill_changes(run)
    assert summary["summary"] == {"assets_replaced": ["a", "b"], "assets_removed": ["a"]}
    assert summary["asset_diffs"] == {}
