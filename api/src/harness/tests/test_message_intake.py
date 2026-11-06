"""発言を取り込むときの扱い（harness.md §10.1・§10.2・§10.4、design.md §5.5）"""

import pytest

from artifact.factories import ArtifactFactory
from artifact.models import Artifact, ArtifactKind
from connector.factories import ConnectorFactory
from conversation.dispatch import CannotStart, post_message
from conversation.factories import ConversationFactory
from conversation.models import ConversationMessage, InstructionKind, InstructionState, MessageRole, UserInstruction
from harness import runner as runner_module
from harness.intake import Chosen
from harness.runner import Runner
from run.models import ClarificationRequest, ClarificationStatus, Run, RunEvent, RunEventKind, RunStatus
from run.tasks import claim_run
from skill.factories import SkillVersionFactory

from .fakes import FakeAdapter, calls, reply

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
            calls(("c1", "skill", {"name": "請求書の照合"})),
            calls(("c2", "skill", {"name": "ないSkill"}), ("c3", "skill", {"name": "請求書の照合"})),
            reply("終わりました。"),
        ],
        change,
    )
    run = execute(run, adapter)

    results = {e.data["call_id"]: e.data for e in RunEvent.objects.filter(run=run, kind=RunEventKind.TOOL_RESULT)}
    assert "Available skills: (none)" in str(results["c1"]["content"])
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
    assert "submit_result is not available" in text


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
