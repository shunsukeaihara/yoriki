"""発言を取り込むときの扱い（harness.md §10.1・§10.2・§10.4、design.md §5.5）"""

from typing import Any, cast

import pytest

from artifact.factories import ArtifactFactory
from artifact.models import Artifact, ArtifactKind
from connector.factories import ConnectorFactory
from conversation.dispatch import CannotStart, post_message
from conversation.factories import ConversationFactory
from conversation.models import ConversationMessage, InstructionKind, InstructionState, MessageRole, UserInstruction
from harness import connectors as connectors_module
from harness import runner as runner_module
from harness import skills as skills_module
from harness.intake import Chosen
from harness.runner import Runner
from llm.factories import ModelProfileFactory
from run.models import ClarificationRequest, ClarificationStatus, Run, RunEvent, RunEventKind, RunLaunch, RunStatus
from run.tasks import claim_run
from skill.factories import SkillFactory, SkillVersionFactory
from skill.models import SkillScope
from task.dispatch import start_task_run
from task.factories import TaskDefinitionFactory
from task.models import TaskScope
from user.factories import DepartmentFactory, DepartmentMembershipFactory, UserFactory, grant
from utils.permissions import AppPermission

from .fakes import FakeAdapter, FakeSession, calls, reply

pytestmark = pytest.mark.usefixtures("default_runtime")

TABLES = [{"name": "vendors", "schema": "public", "description": "取引先", "columns": []}]
TODOS = {"todos": [{"content": "読む", "status": "in_progress"}]}
QUESTION = {
    "questions": [
        {
            "header": "拠点",
            "question": "どの拠点ですか",
            "options": [{"label": "本社", "description": "本社"}, {"label": "支店", "description": "支店"}],
            "multi_select": False,
        }
    ]
}
SCHEMA = {
    "type": "object",
    "properties": {"verdict": {"type": "string"}},
    "required": ["verdict"],
    "additionalProperties": False,
}


@pytest.fixture(name="conversation")
def fixture_conversation(app_user):
    return ConversationFactory.create(owner=app_user)


def send(conversation, body="お願いします", **kwargs):
    return post_message(conversation, body=body, client_message_id="", actor=conversation.owner, **kwargs)


def start(conversation, **kwargs) -> Run:
    send(conversation, **kwargs)
    return Run.objects.filter(conversation=conversation).latest("pk")


def execute(run: Run, adapter: FakeAdapter) -> Run:
    attempt = claim_run(run.pk)
    assert attempt is not None
    Runner(attempt, adapter_factory=adapter, sleep=lambda _s: None).run()
    run.refresh_from_db()
    return run


class DuringFirstReply(FakeAdapter):
    """1つ目の応答の途中で `action` を動かす"""

    def __init__(self, script, action):
        super().__init__(script)
        self.action = action

    def stream(self, request):
        if not self.requests:
            self.action()
        yield from super().stream(request)


def user_texts(request) -> list[str]:
    return [m.text for m in request.messages if m.role == "user"]


@pytest.mark.django_db
def test_catalogs_are_rebuilt_when_a_message_is_taken_in(conversation, app_user):
    """発言を取り込むときに今の権限で作り直し、変わった一覧を発言より先に知らせる。Toolの定義は変えない"""
    run = start(conversation)

    def change():
        ConnectorFactory.create(name="販売管理", version__tables=TABLES)
        SkillVersionFactory.create(skill__created_by=app_user, skill__name="請求書の照合")
        send(conversation, body="支店は別にして")

    adapter = DuringFirstReply([calls(("c1", "todo_write", TODOS)), reply("終わりました。")], change)
    run = execute(run, adapter)

    assert run.status == RunStatus.COMPLETED
    first, second = adapter.requests
    assert not any("請求書の照合" in t or "販売管理" in t for t in user_texts(first))
    texts = user_texts(second)
    listed = next(i for i, t in enumerate(texts) if '<skill name="請求書の照合">' in t)
    assert '<connector name="販売管理">' in texts[listed]
    assert listed < next(i for i, t in enumerate(texts) if "支店は別にして" in t)
    assert [t.name for t in first.tools] == [t.name for t in second.tools]
    assert [t.parameters for t in first.tools] == [t.parameters for t in second.tools]
    kinds = [e["kind"] for e in run.manifest["appended"]]
    assert kinds == ["skill_catalog", "connector_catalog"]


@pytest.mark.django_db
def test_an_unchanged_catalog_is_not_recorded_again(conversation):
    ConnectorFactory.create(name="販売管理", version__tables=TABLES)
    run = start(conversation)
    before = None

    def instruct():
        nonlocal before
        before = RunEvent.objects.filter(run=run, kind=RunEventKind.CONTEXT).count()
        send(conversation, body="支店は別にして")

    adapter = DuringFirstReply([calls(("c1", "todo_write", TODOS)), reply("終わりました。")], instruct)
    run = execute(run, adapter)

    assert run.manifest["appended"] == []
    assert RunEvent.objects.filter(run=run, kind=RunEventKind.CONTEXT).count() == before


@pytest.mark.django_db
def test_a_skill_listed_by_a_rebuild_can_be_loaded(conversation, app_user):
    """始めたときは一覧になかったSkillも、発言を取り込んだ後は読み込める。一覧にない名前は候補を添えて返す"""
    run = start(conversation)

    def change():
        SkillVersionFactory.create(skill__created_by=app_user, skill__name="請求書の照合", tools=[])
        send(conversation, body="請求書も見て")

    adapter = DuringFirstReply(
        [
            calls(("c1", "todo_write", TODOS)),
            calls(("c2", "skill", {"name": "ないSkill"}), ("c3", "skill", {"name": "請求書の照合"})),
            reply("終わりました。"),
        ],
        change,
    )
    run = execute(run, adapter)

    results = {e.data["call_id"]: e.data for e in RunEvent.objects.filter(run=run, kind=RunEventKind.TOOL_RESULT)}
    assert "Available skills: 請求書の照合" in str(results["c2"]["content"])
    assert '<skill name="請求書の照合"' in str(results["c3"]["content"])


@pytest.mark.django_db
def test_a_run_started_with_a_chosen_skill_keeps_the_whole_scope(conversation, app_user):
    """`/` は使うものを明示するだけで、ToolもSkillの一覧も絞らない（authorization.md §6）"""
    skill = SkillVersionFactory.create(skill__created_by=app_user, skill__name="請求書の照合", tools=["read"]).skill
    other = SkillVersionFactory.create(skill__created_by=app_user, skill__name="別のSkill", tools=["shell"]).skill
    run = start(conversation, skills=[skill])
    adapter = FakeAdapter([calls(("c1", "skill", {"name": other.name})), reply("終わりました。")])
    run = execute(run, adapter)

    assert {"read", "shell", "skill", "datasource_query"} <= {t.name for t in adapter.requests[0].tools}
    assert any('<skill name="別のSkill">' in t for t in user_texts(adapter.requests[0]))
    assert [e["skill"] for e in run.manifest["appended"] if e["kind"] == "skill"] == [other.uid]


def publish(owner, *, name="請求書の照合", tools=(), **kwargs):
    version = SkillVersionFactory.create(skill__created_by=owner, skill__name=name, tools=list(tools), **kwargs)
    return version.skill


@pytest.mark.django_db
def test_skill_chosen_during_a_run_is_read_by_the_parent(conversation, app_user):
    """`/` で指定した発言は親へ渡し、最新の公開版の本文を発言に入れる。Runは増えない"""
    skill = publish(app_user, instructions="照合の手順")
    run = start(conversation)
    adapter = DuringFirstReply(
        [calls(("c1", "todo_write", TODOS)), reply("照合しました。")],
        lambda: send(conversation, body="この3件も", skills=[skill]),
    )
    run = execute(run, adapter)

    assert run.status == RunStatus.COMPLETED
    assert Run.objects.filter(conversation=conversation).count() == 1
    text = next(t for t in user_texts(adapter.requests[1]) if "この3件も" in t)
    assert "照合の手順" in text
    assert next(e for e in run.manifest["appended"] if e["kind"] == "skill")["chosen"] is True
    assert RunEvent.objects.filter(run=run, kind=RunEventKind.SKILL_LOADED).count() == 1
    assert UserInstruction.objects.get(message__body="この3件も").state == InstructionState.APPLIED


@pytest.mark.django_db
def test_choosing_a_loaded_skill_again_switches_to_the_latest_version(conversation, app_user):
    skill = publish(app_user, instructions="古い手順")
    run = start(conversation)

    def republish_and_choose():
        SkillVersionFactory.create(skill=skill, tools=[], instructions="新しい手順")
        send(conversation, body="もう一度", skills=[skill])

    class Adapter(FakeAdapter):
        def stream(self, request):
            if len(self.requests) == 1:
                republish_and_choose()
            yield from super().stream(request)

    adapter = Adapter(
        [
            calls(("c1", "skill", {"name": "請求書の照合"})),
            calls(("c0", "todo_write", TODOS)),
            calls(("c2", "skill", {"name": "請求書の照合"})),
            reply("終わりました。"),
        ]
    )
    run = execute(run, adapter)

    results = {
        e.data["call_id"]: str(e.data["content"])
        for e in RunEvent.objects.filter(run=run, kind=RunEventKind.TOOL_RESULT)
    }
    assert "古い手順" in results["c1"]
    text = next(t for t in user_texts(adapter.requests[2]) if "もう一度" in t)
    assert "新しい手順" in text and "replaces version 1" in text
    # 同じRunで読み込み直すと、切り替えた版を返す
    assert "新しい手順" in results["c2"]


@pytest.mark.django_db
def test_chosen_skill_with_a_schema_writes_the_result_in_the_final_reply(conversation, app_user):
    skill = publish(app_user, output_schema=SCHEMA)
    run = start(conversation)
    adapter = DuringFirstReply(
        [calls(("c1", "todo_write", TODOS)), reply("結果です。")],
        lambda: send(conversation, body="判定して", skills=[skill]),
    )
    execute(run, adapter)

    assert "submit_result" not in [t.name for t in adapter.requests[1].tools]
    text = next(t for t in user_texts(adapter.requests[1]) if "判定して" in t)
    assert "no submit_result for this skill" in text


@pytest.mark.django_db
def test_unusable_choices_are_refused_when_the_message_is_received(conversation, app_user, other_user):
    private = publish(other_user, name="他の人のSkill")
    start(conversation)
    with pytest.raises(CannotStart, match="他の人のSkill"):
        send(conversation, body="これも", skills=[private])
    assert not ConversationMessage.objects.filter(body="これも").exists()


@pytest.mark.django_db
def test_choices_revoked_before_they_are_taken_in_stay_out_of_the_context(conversation, app_user):
    """受け付けた後に使えなくなったものは文脈に入れず、画面に示す（名前もモデルへ渡さない）"""
    skill = publish(app_user, name="止めたSkill", instructions="止めた手順")
    connector = ConnectorFactory.create(name="消したDB", version__tables=TABLES)
    run = start(conversation)

    def choose_and_revoke():
        send(conversation, body="これも", skills=[skill], connectors=[connector])
        skill.suspended = True
        skill.save(update_fields=["suspended"])
        connector.is_active = False
        connector.save(update_fields=["is_active"])

    adapter = DuringFirstReply([calls(("c1", "todo_write", TODOS)), reply("終わりました。")], choose_and_revoke)
    run = execute(run, adapter)

    text = next(t for t in user_texts(adapter.requests[1]) if "これも" in t)
    assert "止めた" not in text and "消したDB" not in text
    assert "cannot be used now" in text
    notice = ConversationMessage.objects.get(conversation=conversation, role=MessageRole.SYSTEM)
    assert "止めたSkill" in notice.body and "消したDB" in notice.body


@pytest.mark.django_db
def test_connector_chosen_during_a_run_shows_its_tables(conversation):
    connector = ConnectorFactory.create(name="販売管理", version__tables=TABLES)
    run = start(conversation)
    adapter = DuringFirstReply(
        [calls(("c1", "todo_write", TODOS)), reply("終わりました。")],
        lambda: send(conversation, body="こちらも", connectors=[connector]),
    )
    run = execute(run, adapter)

    text = next(t for t in user_texts(adapter.requests[1]) if "こちらも" in t)
    assert "public.vendors" in text
    assert [e["connector"] for e in run.manifest["appended"] if e["kind"] == "connector"] == [connector.uid]


@pytest.mark.django_db
def test_a_newly_chosen_connector_stays_in_the_catalog_over_the_limit(conversation, monkeypatch):
    monkeypatch.setattr(connectors_module, "MAX_CATALOG_CONNECTORS", 1)
    old = ConnectorFactory.create(name="前のDB", version__tables=TABLES)
    new = ConnectorFactory.create(name="今のDB", version__tables=TABLES)
    run = start(conversation, connectors=[old])
    adapter = DuringFirstReply(
        [calls(("c1", "todo_write", TODOS)), reply("終わりました。")],
        lambda: send(conversation, body="こちらも", connectors=[new]),
    )
    run = execute(run, adapter)
    catalog = run.current_catalog("connector_catalog")
    assert catalog is not None
    assert [e["name"] for e in catalog["connectors"]] == ["今のDB"]


@pytest.mark.django_db
def test_connector_is_accepted_whatever_tools_the_chosen_skill_of_the_run_declares(conversation, app_user):
    skill = publish(app_user, tools=["read"])
    connector = ConnectorFactory.create(name="販売管理", version__tables=TABLES)
    start(conversation, skills=[skill])
    message = send(conversation, body="こちらも", connectors=[connector])
    assert UserInstruction.objects.get(message=message).kind == InstructionKind.CURRENT


@pytest.mark.django_db
def test_choices_revoked_while_placing_assets_are_not_recorded(conversation, app_user, monkeypatch):
    """資料を置く間に失効したものは、記録の直前の確かめで止める"""
    skill = publish(app_user, name="止めたSkill", instructions="止めた手順")
    run = start(conversation)
    original = runner_module.take_chosen
    taken: list[Chosen] = []

    def take_then_revoke(*args):
        chosen = original(*args)
        if not taken:
            taken.append(chosen)
            skill.suspended = True
            skill.save(update_fields=["suspended"])
        return chosen

    monkeypatch.setattr(runner_module, "take_chosen", take_then_revoke)
    adapter = DuringFirstReply(
        [calls(("c1", "todo_write", TODOS)), reply("終わりました。")],
        lambda: send(conversation, body="これも", skills=[skill]),
    )
    run = execute(run, adapter)

    assert taken[0].skills  # 最初の確かめでは使えた
    assert not [e for e in run.manifest["appended"] if e["kind"] == "skill"]
    text = next(t for t in user_texts(adapter.requests[1]) if "これも" in t)
    assert "止めた手順" not in text and "cannot be used now" in text


@pytest.mark.django_db
def test_answer_with_a_skill_answers_and_loads_it_on_resume(conversation, app_user):
    """確認待ちの発言は回答にし、指定したものは再開のときに取り込む（本文を二重に渡さない）"""
    skill = publish(app_user, instructions="照合の手順")
    run = start(conversation)
    run = execute(run, FakeAdapter([calls(("q1", "ask_user", QUESTION))]))
    assert run.status == RunStatus.WAITING_USER

    send(conversation, body="大阪支店です", skills=[skill])

    clarification = ClarificationRequest.objects.get(run=run)
    assert clarification.status == ClarificationStatus.ANSWERED
    assert clarification.comment == "大阪支店です"
    instruction = UserInstruction.objects.get(message__body="大阪支店です")
    assert (instruction.kind, instruction.is_answer) == (InstructionKind.CURRENT, True)

    adapter = FakeAdapter([reply("終わりました。")])
    run = execute(run, adapter)

    assert run.status == RunStatus.COMPLETED
    request = adapter.requests[0]
    assert any("照合の手順" in t for t in user_texts(request))
    assert not any("大阪支店です" in t for t in user_texts(request))
    assert any("大阪支店です" in m.text for m in request.messages if m.role == "tool")


@pytest.mark.django_db
def test_an_answer_needs_a_body_when_a_skill_is_chosen(conversation, app_user):
    skill = publish(app_user)
    run = start(conversation)
    run = execute(run, FakeAdapter([calls(("q1", "ask_user", QUESTION))]))

    with pytest.raises(CannotStart, match="回答を入力"):
        send(conversation, body="", skills=[skill])
    assert ClarificationRequest.objects.get(run=run).status == ClarificationStatus.OPEN


@pytest.mark.django_db
def test_choices_with_an_answer_do_not_become_a_new_request_when_the_run_ends(conversation, app_user):
    """再開の前にRunが終わったら、回答の本文を新しい依頼にしない"""
    skill = publish(app_user)
    run = start(conversation)
    run = execute(run, FakeAdapter([calls(("q1", "ask_user", QUESTION))]))
    send(conversation, body="大阪支店です", skills=[skill])

    run.refresh_from_db()
    run.request_cancel("止める", app_user)

    instruction = UserInstruction.objects.get(message__body="大阪支店です")
    assert instruction.state == InstructionState.DISCARDED
    assert Run.objects.filter(conversation=conversation).count() == 1


@pytest.mark.django_db
def test_attachments_of_an_answer_with_choices_stay_inputs(conversation, app_user):
    """回答に添えた添付は回答として渡している。Runが先に終わって指定を捨てても、次の依頼から読める"""
    skill = publish(app_user)
    run = start(conversation)
    run = execute(run, FakeAdapter([calls(("q1", "ask_user", QUESTION))]))
    attachment = ArtifactFactory.create(owner=app_user, conversation=conversation, kind=ArtifactKind.ATTACHMENT)
    send(conversation, body="大阪支店です", skills=[skill], attachments=[attachment])

    assert attachment in Artifact.objects.sent_inputs(conversation.pk)
    run.refresh_from_db()
    run.request_cancel("止める", app_user)
    assert attachment in Artifact.objects.sent_inputs(conversation.pk)


@pytest.mark.django_db
def test_the_skill_edit_request_still_waits_for_the_run_to_end(conversation, app_user):
    skill = publish(app_user)
    start(conversation)
    message = post_message(
        conversation,
        body="説明を直して",
        client_message_id="",
        actor=app_user,
        skill_edit={"id": skill.uid, "name": skill.name},
    )
    assert UserInstruction.objects.get(message=message).kind == InstructionKind.NEXT


@pytest.mark.django_db
def test_what_the_catalog_omits_is_still_usable_in_a_conversation(conversation, app_user, monkeypatch):
    """一覧の量の上限はアクセス範囲ではない（harness.md §10.1・§10.2）"""
    monkeypatch.setattr(connectors_module, "MAX_CATALOG_CONNECTORS", 0)
    monkeypatch.setattr(skills_module, "CATALOG_CONTEXT_RATIO", 0)
    ConnectorFactory.create(name="販売管理", version__tables=TABLES)
    publish(app_user, name="請求書の照合")
    run = start(conversation)
    adapter = FakeAdapter(
        [
            calls(("c1", "skill", {"name": "請求書の照合"})),
            calls(("c2", "datasource_query", {"connector": "ないDB", "sql": "SELECT 1"})),
            reply("終わりました。"),
        ]
    )
    run = execute(run, adapter)
    results = {e.data["call_id"]: e.data for e in RunEvent.objects.filter(run=run, kind=RunEventKind.TOOL_RESULT)}
    assert not results["c1"]["is_error"]
    assert "not listed" in " ".join(user_texts(adapter.requests[0]))
    assert 'No connector named "ないDB"' in str(results["c2"]["content"])


class PlacingSession(FakeSession):
    """動いているSandboxの代わり。Skillごとに、今置いている版を覚える"""

    @property
    def started(self) -> bool:
        return True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.placed: dict[str, int] = {}

    def place_skill_assets(self, skill, version) -> None:
        self.placed[skill.uid] = version.pk

    def has_skill_assets(self, skill, version) -> bool:
        return self.placed.get(skill.uid) == version.pk

    def sync_skill_assets(self) -> None:
        pass

    def sync_inputs(self) -> None:
        pass

    def serve_script_requests(self) -> None:
        pass

    def finished_unreported(self) -> list:
        return []

    def running_jobs(self, *, refresh: bool = False) -> list:
        return []


@pytest.mark.django_db
def test_two_messages_choosing_a_skill_in_different_versions_are_both_taken_in(conversation, app_user):
    """同じ区切りで、間に公開された同じSkillを指定した発言が並んでも、前の発言の指定を失わない"""
    skill = publish(app_user, instructions="第1版の手順")
    run = start(conversation)

    def choose_twice():
        send(conversation, body="1つ目", skills=[skill])
        SkillVersionFactory.create(skill=skill, tools=[], instructions="第2版の手順")
        skill.refresh_from_db()
        send(conversation, body="2つ目", skills=[skill])

    adapter = DuringFirstReply([calls(("c1", "todo_write", TODOS)), reply("終わりました。")], choose_twice)
    attempt = claim_run(run.pk)
    assert attempt is not None
    Runner(attempt, adapter_factory=adapter, sleep=lambda _s: None, session_factory=cast(Any, PlacingSession)).run()
    run.refresh_from_db()

    assert run.status == RunStatus.COMPLETED, run.error
    # どちらの発言も、取り込むときの最新の公開版を読む
    texts = user_texts(adapter.requests[1])
    assert "第2版の手順" in next(t for t in texts if "1つ目" in t)
    assert "第2版の手順" in next(t for t in texts if "2つ目" in t)
    assert [e["number"] for e in run.manifest["appended"] if e["kind"] == "skill"] == [2, 2]
    assert not ConversationMessage.objects.filter(conversation=conversation, role=MessageRole.SYSTEM).exists()


@pytest.mark.django_db
def test_an_omitted_skill_with_a_shared_name_is_found_by_its_listed_name(conversation, app_user, monkeypatch):
    monkeypatch.setattr(skills_module, "CATALOG_CONTEXT_RATIO", 0)
    publish(app_user, name="名寄せ", instructions="個人の手順")
    company = SkillFactory.create(scope=SkillScope.COMPANY, owner=None, name="名寄せ")
    SkillVersionFactory.create(skill=company, instructions="全社の手順")
    run = start(conversation)
    adapter = FakeAdapter(
        [
            calls(("c1", "skill", {"name": "名寄せ (個人)"})),
            calls(("c2", "skill", {"name": "名寄せ"})),
            reply("終わりました。"),
        ]
    )
    run = execute(run, adapter)
    results = {e.data["call_id"]: e.data for e in RunEvent.objects.filter(run=run, kind=RunEventKind.TOOL_RESULT)}
    assert "個人の手順" in str(results["c1"]["content"])
    assert results["c2"]["is_error"]  # 1つに決まらない


@pytest.mark.django_db
def test_fix_chat_accepts_choices_within_the_scope_of_the_task(api_client):
    """修正チャットでも使用の指示を受け付け、動いているタスクのRunではタスクの範囲で確かめる"""
    department = DepartmentFactory.create()
    manager = grant(UserFactory.create(), AppPermission.MANAGE_DEPARTMENT_TASKS)
    DepartmentMembershipFactory.create(department=department, user=manager)
    task = TaskDefinitionFactory.create(
        scope=TaskScope.DEPARTMENT,
        department=department,
        created_by=manager,
        revision__model_profile=ModelProfileFactory.create(version__supports_tools=True),
    )
    inside = SkillFactory.create(scope=SkillScope.COMPANY, owner=None, name="全社の手順")
    SkillVersionFactory.create(skill=inside, instructions="全社の手順の本文")
    outside = SkillFactory.create(scope=SkillScope.DEPARTMENT, department=DepartmentFactory.create(), name="他部門")
    SkillVersionFactory.create(skill=outside)
    run = start_task_run(task, inputs={}, actor=manager, launch=RunLaunch.FIX)
    with pytest.raises(CannotStart):
        post_message(run.conversation, body="これも", client_message_id="", actor=manager, skills=[outside])

    adapter = DuringFirstReply(
        [calls(("c1", "todo_write", TODOS)), reply("終わりました。")],
        lambda: post_message(run.conversation, body="これも", client_message_id="", actor=manager, skills=[inside]),
    )
    execute(run, adapter)
    assert any("全社の手順の本文" in t for t in user_texts(adapter.requests[1]))


@pytest.mark.django_db
def test_choices_with_an_answer_clear_the_submitted_result_in_a_fix_chat():
    """修正チャットの回答に添えた指定は作業の条件を変えるので、確定した業務結果を確定し直させる"""
    department = DepartmentFactory.create()
    manager = grant(UserFactory.create(), AppPermission.MANAGE_DEPARTMENT_TASKS)
    DepartmentMembershipFactory.create(department=department, user=manager)
    task = TaskDefinitionFactory.create(
        scope=TaskScope.DEPARTMENT,
        department=department,
        created_by=manager,
        revision__output_schema=SCHEMA,
        revision__model_profile=ModelProfileFactory.create(version__supports_tools=True),
    )
    added = SkillFactory.create(scope=SkillScope.COMPANY, owner=None, name="追加の基準")
    SkillVersionFactory.create(skill=added, instructions="追加の基準の本文")
    run = start_task_run(task, inputs={}, actor=manager, launch=RunLaunch.FIX)
    run = execute(
        run, FakeAdapter([calls(("c1", "submit_result", {"verdict": "ok"})), calls(("q1", "ask_user", QUESTION))])
    )
    assert run.status == RunStatus.WAITING_USER
    assert run.result == {"verdict": "ok"}

    post_message(run.conversation, body="大阪支店です", client_message_id="", actor=manager, skills=[added])
    adapter = FakeAdapter([calls(("c2", "submit_result", {"verdict": "ng"})), reply("判定し直しました。")])
    run = execute(run, adapter)

    assert any("追加の基準の本文" in t for t in user_texts(adapter.requests[0]))
    assert RunEvent.objects.filter(run=run, kind=RunEventKind.CONTEXT, data__result_reset=True).exists()
    assert run.result == {"verdict": "ng"}
