"""会話でSkillを使う（harness.md §10.1・§10.5）。一覧、`skill` での読込み、`/` での指定、資料の配置、業務結果"""

import pytest

from conversation.dispatch import CannotStart, MessageMode, post_message
from conversation.factories import ConversationFactory
from conversation.models import InstructionKind, UserInstruction
from harness import runner as runner_module
from harness.recovery import switch_candidates
from harness.runner import RESULT_GRACE_NOTICE, RESULT_RESET_NOTICE, Runner
from harness.sandbox import SandboxSession
from harness.skills import build_catalog
from harness.tools.base import schema_problem
from llm.factories import ModelProfileFactory
from resources.factories import ResourceVersionFactory
from run.models import Run, RunEndReason, RunEvent, RunEventKind, RunStatus
from run.tasks import claim_run
from skill.factories import SkillFactory, SkillVersionFactory
from skill.models import SkillAssetBinding, SkillAssetRole, SkillRequirement, SkillScope
from user.factories import UserFactory

from .fakes import FakeAdapter, calls, reply

pytestmark = pytest.mark.usefixtures("default_runtime")

RESULT_SCHEMA = {
    "type": "object",
    "properties": {"verdict": {"type": "string", "enum": ["ok", "ng"]}, "reason": {"type": "string"}},
    "required": ["verdict"],
    "additionalProperties": False,
}


@pytest.fixture(name="conversation")
def fixture_conversation(app_user):
    return ConversationFactory.create(owner=app_user)


def publish(owner, name="請求書の照合", **content):
    skill = SkillFactory.create(name=name, created_by=owner)
    version = SkillVersionFactory.create(skill=skill, **content)
    return skill, version


def start(conversation, body="請求書を確認して", skills=None) -> Run:
    post_message(
        conversation, body=body, client_message_id="", mode=MessageMode.AUTO, actor=conversation.owner, skills=skills
    )
    return Run.objects.filter(conversation=conversation).latest("pk")


def execute(run: Run, adapter: FakeAdapter) -> Run:
    attempt = claim_run(run.pk)
    assert attempt is not None
    Runner(attempt, adapter_factory=adapter, sleep=lambda _s: None).run()
    run.refresh_from_db()
    return run


def tool_names(request) -> list[str]:
    return [t.name for t in request.tools]


def results(run: Run, name: str) -> list[dict]:
    return [
        e.data
        for e in RunEvent.objects.filter(run=run, kind=RunEventKind.TOOL_RESULT).order_by("sequence")
        if e.data["name"] == name
    ]


@pytest.mark.django_db
def test_agent_sees_usable_skills_and_loads_one(conversation, app_user):
    skill, version = publish(app_user, description="請求書と発注を照合する", instructions="1. 発注番号で突き合わせる")
    publish(UserFactory.create(), name="他人のSkill")  # 他人の個人のSkillは見えない
    run = start(conversation)
    assert run.manifest["fixed"]["skill_catalog"]["skills"] == [
        {"skill": skill.uid, "name": "請求書の照合", "description": "請求書と発注を照合する"}
    ]
    adapter = FakeAdapter([calls(("c1", "skill", {"name": "請求書の照合"})), reply("照合しました")])
    run = execute(run, adapter)

    assert run.status == RunStatus.COMPLETED
    first = adapter.requests[0]
    skill_tool = next(t for t in first.tools if t.name == "skill")
    assert skill_tool.parameters["properties"]["name"]["enum"] == ["請求書の照合"]
    context = next(m.text for m in first.messages if m.role == "user" and "<runtime_context>" in m.text)
    assert '<skill name="請求書の照合">請求書と発注を照合する</skill>' in context
    [result] = results(run, "skill")
    assert "1. 発注番号で突き合わせる" in result["content"][0]["text"]
    assert result["value"] == {"skill": skill.uid, "version": version.number}
    appended = [e for e in run.manifest["appended"] if e["kind"] == "skill"]
    assert [(e["skill"], e["version"]) for e in appended] == [(skill.uid, version.pk)]
    assert RunEvent.objects.filter(run=run, kind=RunEventKind.SKILL_LOADED).count() == 1


@pytest.mark.django_db
def test_skill_loaded_twice_keeps_the_first_version(conversation, app_user):
    skill, _version = publish(app_user, instructions="版1の手順")
    run = start(conversation)

    def republish_then_load():
        SkillVersionFactory.create(skill=skill, instructions="版2の手順")
        return calls(("c2", "skill", {"name": skill.name}))

    class Script(FakeAdapter):
        def stream(self, request):
            if len(self.requests) == 1:
                self.script.insert(0, republish_then_load())
            yield from super().stream(request)

    adapter = Script([calls(("c1", "skill", {"name": skill.name})), reply("終わりました")])
    run = execute(run, adapter)
    first, second = results(run, "skill")
    assert "版1の手順" in first["content"][0]["text"]
    assert "版1の手順" in second["content"][0]["text"]
    assert len([e for e in run.manifest["appended"] if e["kind"] == "skill"]) == 1


@pytest.mark.django_db
def test_suspended_skill_cannot_be_loaded(conversation, app_user):
    skill, _version = publish(app_user)
    run = start(conversation)
    skill.set_suspended(True, actor=app_user)
    adapter = FakeAdapter([calls(("c1", "skill", {"name": skill.name})), reply("Skillなしで進めます")])
    run = execute(run, adapter)
    [result] = results(run, "skill")
    assert result["is_error"]
    assert "cannot be used" in result["content"][0]["text"]
    assert not [e for e in run.manifest["appended"] if e["kind"] == "skill"]


@pytest.mark.django_db
def test_skill_needing_tools_missing_in_this_run_is_not_loaded(conversation, app_user):
    skill, _version = publish(app_user, tools=["shell"])
    run = start(conversation)
    Run.objects.filter(pk=run.pk).update(runtime_version=None)  # Sandboxを使わないRun
    adapter = FakeAdapter([calls(("c1", "skill", {"name": skill.name})), reply("できませんでした")])
    run = execute(run, adapter)
    [result] = results(run, "skill")
    assert result["is_error"]
    assert "shell" in result["content"][0]["text"]


@pytest.mark.django_db
def test_catalog_excludes_skills_the_model_cannot_run(app_user):
    conversation = ConversationFactory.create(owner=app_user)
    version = conversation.model_profile.current_version
    assert version is not None
    publish(app_user, name="画像のSkill", requires=[SkillRequirement.IMAGE_INPUT])
    publish(app_user, name="文章のSkill")
    names = [e["name"] for e in build_catalog(app_user, version)["skills"]]
    assert names == ["文章のSkill"]


@pytest.mark.django_db
def test_catalog_separates_same_names_and_shortens_to_the_budget(app_user):
    conversation = ConversationFactory.create(owner=app_user)
    version = conversation.model_profile.current_version
    assert version is not None
    publish(app_user, name="名寄せ")
    company = SkillFactory.create(name="名寄せ", scope=SkillScope.COMPANY, owner=None, created_by=app_user)
    SkillVersionFactory.create(skill=company)
    names = [e["name"] for e in build_catalog(app_user, version)["skills"]]
    assert names == ["名寄せ (個人)", "名寄せ (全社)"]
    publish(app_user, name="名寄せ (個人)")  # 範囲を添えた名前と同じ名前のSkill
    names = [e["name"] for e in build_catalog(app_user, version)["skills"]]
    assert sorted(names) == ["名寄せ (個人)", "名寄せ (個人) (2)", "名寄せ (全社)"]

    for n in range(39):
        publish(app_user, name=f"Skill{n:02d}", description="説明" * 200)
    version.max_input_tokens = 50_000  # 一覧は1000文字まで
    catalog = build_catalog(app_user, version)
    assert catalog["omitted"] > 0
    assert all(len(e["description"]) <= 40 for e in catalog["skills"])
    assert len(catalog["skills"]) + catalog["omitted"] == 42


@pytest.mark.django_db
def test_chosen_skill_is_given_with_the_first_message_and_limits_the_tools(conversation, app_user):
    skill, version = publish(app_user, instructions="手順の本文", tools=["read", "todo_write"])
    run = start(conversation, body="この3件を確認して", skills=[skill])
    assert run.manifest["fixed"]["skills"] == [
        {"skill": skill.uid, "name": skill.name, "version": version.pk, "number": version.number}
    ]
    assert run.manifest["fixed"]["skill_catalog"] is None
    adapter = FakeAdapter([reply("確認しました")])
    run = execute(run, adapter)

    request = adapter.requests[0]
    assert tool_names(request) == ["read", "todo_write"]
    message = next(m.text for m in request.messages if m.role == "user" and m.text.startswith("この3件を確認して"))
    assert "手順の本文" in message
    assert f'<skill name="{skill.name}" version="{version.number}">' in message
    context = next(m.text for m in request.messages if "<runtime_context>" in m.text)
    assert "<skills>" not in context


@pytest.mark.django_db
def test_chosen_skill_without_tools_gets_no_tools(conversation, app_user):
    skill, _version = publish(app_user, tools=[])
    run = start(conversation, skills=[skill])
    adapter = FakeAdapter([reply("回答案です")])
    execute(run, adapter)
    assert tool_names(adapter.requests[0]) == []


@pytest.mark.django_db
def test_unusable_chosen_skill_does_not_start_the_run(conversation, app_user):
    skill, _version = publish(app_user)
    skill.set_suspended(True, actor=app_user)
    with pytest.raises(CannotStart, match="停止中"):
        start(conversation, skills=[skill])


@pytest.mark.django_db
def test_message_with_a_skill_during_a_run_becomes_the_next_request(conversation, app_user):
    skill, _version = publish(app_user)
    start(conversation)
    post_message(
        conversation, body="次はこれ", client_message_id="", mode=MessageMode.AUTO, actor=app_user, skills=[skill]
    )
    instruction = UserInstruction.objects.get(message__body="次はこれ")
    assert instruction.kind == InstructionKind.NEXT
    assert instruction.message.skills == [{"id": skill.uid, "name": skill.name}]


@pytest.mark.django_db
def test_submit_result_records_the_business_result(conversation, app_user):
    skill, _version = publish(app_user, tools=[], output_schema=RESULT_SCHEMA)
    run = start(conversation, body="契約書を確認して", skills=[skill])
    adapter = FakeAdapter(
        [
            calls(("c1", "submit_result", {"verdict": "maybe"})),
            calls(("c2", "submit_result", {"verdict": "ng", "reason": "第3条"})),
            reply("確認しました。第3条に問題があります"),
        ]
    )
    run = execute(run, adapter)
    assert tool_names(adapter.requests[0]) == ["submit_result"]
    assert adapter.requests[0].tools[0].parameters == RESULT_SCHEMA
    invalid, recorded = results(run, "submit_result")
    assert invalid["is_error"]
    assert not recorded["is_error"]
    assert run.status == RunStatus.COMPLETED
    assert run.result == {"verdict": "ng", "reason": "第3条"}


@pytest.mark.django_db
def test_reply_without_submit_result_is_nudged_once_then_ends_with_no_result(conversation, app_user):
    skill, _version = publish(app_user, tools=[], output_schema=RESULT_SCHEMA)
    run = start(conversation, skills=[skill])
    adapter = FakeAdapter([reply("確認しました"), reply("確認しました")])
    run = execute(run, adapter)
    assert run.status == RunStatus.FAILED
    assert run.end_reason == RunEndReason.NO_RESULT
    assert run.error == "Skillが決めた形の業務結果を、エージェントが確定しませんでした。"
    notices = RunEvent.objects.filter(run=run, kind=RunEventKind.CONTEXT, data__result_required=True)
    assert notices.count() == 1


@pytest.mark.django_db
def test_loaded_skill_with_a_result_schema_asks_for_the_result_in_the_reply(conversation, app_user):
    """途中で読み込んだSkillのためにToolの一覧は変えない（submit_result は出さない）"""
    skill, _version = publish(app_user, output_schema=RESULT_SCHEMA)
    run = start(conversation)
    adapter = FakeAdapter([calls(("c1", "skill", {"name": skill.name})), reply("判定：ok")])
    run = execute(run, adapter)
    assert run.status == RunStatus.COMPLETED
    assert "submit_result" not in tool_names(adapter.requests[1])
    [result] = results(run, "skill")
    assert "submit_result is not available" in result["content"][0]["text"]


@pytest.mark.django_db
def test_two_chosen_skills_with_result_schemas_are_refused(conversation, app_user):
    first, _v1 = publish(app_user, name="A", output_schema=RESULT_SCHEMA)
    second, _v2 = publish(app_user, name="B", output_schema=RESULT_SCHEMA)
    with pytest.raises(CannotStart, match="業務結果"):
        start(conversation, skills=[first, second])


@pytest.mark.django_db
def test_skill_assets_are_placed_under_master(conversation, app_user, settings, tmp_path):
    settings.YORIKI_WORKSPACES_DIR = str(tmp_path / "workspaces")
    skill, version = publish(app_user, tools=["read"])
    resource = ResourceVersionFactory.create(name="vendor-master.csv", content=b"code,name\n001,A\n")
    SkillAssetBinding.objects.create(
        version=version, key="vendor-master", role=SkillAssetRole.MASTER, resource_version=resource
    )
    run = start(conversation, skills=[skill])
    attempt = claim_run(run.pk)
    assert attempt is not None
    session = SandboxSession(run, attempt)
    session.sync_skill_assets()

    placed = session.master_dir / "skills" / skill.uid / "vendor-master" / "vendor-master.csv"
    assert placed.read_bytes() == b"code,name\n001,A\n"
    assert oct(placed.stat().st_mode & 0o777) == "0o644"
    text = RunEvent.objects.get(run=run, kind=RunEventKind.USER_MESSAGE).data["skills_text"]
    assert f"/workspace/master/skills/{skill.uid}/vendor-master/vendor-master.csv" in text
    session.close()


def test_schema_problem_accepts_only_what_every_provider_takes():
    assert schema_problem(RESULT_SCHEMA) == ""
    assert schema_problem({"type": "array"}) == 'the root must be {"type": "object"}'
    assert "anyOf" in schema_problem({"type": "object", "properties": {"a": {"anyOf": []}}})
    assert schema_problem({"type": "object", "properties": {"a": {"enum": ["x"]}}}) == "enum needs type"
    assert "tuple" in schema_problem({"type": "object", "properties": {"a": {"type": "array", "items": [{}]}}})


@pytest.mark.django_db
def test_run_stops_when_a_skill_in_use_is_suspended(conversation, app_user):
    skill, _version = publish(app_user, tools=["todo_write"])
    run = start(conversation, skills=[skill])
    skill.set_suspended(True, actor=app_user)
    run = execute(run, FakeAdapter([]))
    assert run.status == RunStatus.FAILED
    assert run.end_reason == RunEndReason.SKILL
    assert "停止中" in run.error


@pytest.mark.django_db
def test_result_schema_needs_a_model_that_calls_tools(app_user):
    conversation = ConversationFactory.create(owner=app_user, model_profile__version__supports_tools=False)
    skill, _version = publish(app_user, tools=[], output_schema=RESULT_SCHEMA)
    with pytest.raises(CannotStart, match="Tool呼出し"):
        start(conversation, skills=[skill])


@pytest.mark.django_db
def test_model_switch_keeps_to_models_the_skills_in_use_can_run(conversation, app_user):
    skill, _version = publish(app_user, requires=[SkillRequirement.IMAGE_INPUT])
    model_version = conversation.model_profile.current_version
    assert model_version is not None
    model_version.supports_images = True
    model_version.save()
    with_images = ModelProfileFactory.create(
        name="画像あり", version__supports_tools=True, version__supports_images=True
    )
    ModelProfileFactory.create(name="画像なし", version__supports_tools=True)
    run = start(conversation, skills=[skill])
    assert [p.name for p in switch_candidates(run)] == [with_images.name]


@pytest.mark.django_db
def test_result_can_be_submitted_just_before_the_step_limit(conversation, app_user, monkeypatch):
    monkeypatch.setattr(runner_module, "MAX_STEPS", 2)
    skill, _version = publish(app_user, tools=["todo_write"], output_schema=RESULT_SCHEMA)
    run = start(conversation, skills=[skill])
    todos = {"todos": [{"content": "読む", "status": "in_progress"}]}
    adapter = FakeAdapter(
        [
            calls(("c1", "todo_write", todos)),
            calls(("c2", "submit_result", {"verdict": "ok"})),
            reply("ここまでの結果です。"),
        ]
    )
    run = execute(run, adapter)
    assert any(RESULT_GRACE_NOTICE in m.text for m in adapter.requests[1].messages)
    assert run.status == RunStatus.COMPLETED
    assert run.result == {"verdict": "ok"}


@pytest.mark.django_db
def test_instruction_after_the_result_asks_to_submit_it_again(conversation, app_user):
    skill, _version = publish(app_user, tools=[], output_schema=RESULT_SCHEMA)
    run = start(conversation, skills=[skill])

    class Script(FakeAdapter):
        def stream(self, request):
            if len(self.requests) == 2:
                post_message(
                    conversation, body="理由も書いて", client_message_id="", mode=MessageMode.AUTO, actor=app_user
                )
            yield from super().stream(request)

    adapter = Script(
        [
            reply("確認しました"),  # 促される
            calls(("c1", "submit_result", {"verdict": "ok"})),
            reply("確認しました"),  # この間に追加の指示が届く
            reply("理由を足しました"),  # 取り消された結果を確定し直すよう、もう一度促される
            calls(("c2", "submit_result", {"verdict": "ok", "reason": "問題なし"})),
            reply("確認しました。問題ありません"),
        ]
    )
    run = execute(run, adapter)
    assert any(RESULT_RESET_NOTICE in m.text for m in adapter.requests[3].messages)
    assert run.status == RunStatus.COMPLETED
    assert run.result == {"verdict": "ok", "reason": "問題なし"}
