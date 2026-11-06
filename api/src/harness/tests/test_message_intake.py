"""発言を取り込むときの扱い（harness.md §10.1・§10.2・§10.4、design.md §5.5）"""

import pytest

from connector.factories import ConnectorFactory
from conversation.dispatch import MessageMode, post_message
from conversation.factories import ConversationFactory
from harness.runner import Runner
from run.models import Run, RunEvent, RunEventKind, RunStatus
from run.tasks import claim_run
from skill.factories import SkillVersionFactory

from .fakes import FakeAdapter, calls, reply

pytestmark = pytest.mark.usefixtures("default_runtime")

TABLES = [{"name": "vendors", "schema": "public", "description": "取引先", "columns": []}]
TODOS = {"todos": [{"content": "読む", "status": "in_progress"}]}


@pytest.fixture(name="conversation")
def fixture_conversation(app_user):
    return ConversationFactory.create(owner=app_user)


def send(conversation, body="お願いします", **kwargs):
    return post_message(
        conversation, body=body, client_message_id="", mode=MessageMode.AUTO, actor=conversation.owner, **kwargs
    )


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
def test_a_run_started_with_a_chosen_skill_gets_no_skill_list(conversation):
    """`/` で指定して始めたRunには、Skillの一覧を出さない（作り直しても出ない）"""
    skill = SkillVersionFactory.create(
        skill__created_by=conversation.owner, skill__name="請求書の照合", tools=["read"]
    ).skill
    run = start(conversation, skills=[skill])

    adapter = DuringFirstReply(
        [calls(("c1", "read", {"path": "/workspace/none"})), reply("終わりました。")],
        lambda: send(conversation, body="支店は別にして"),
    )
    run = execute(run, adapter)

    assert run.manifest["fixed"]["skill_catalog"] is None
    assert not [e for e in run.manifest["appended"] if e["kind"] == "skill_catalog"]
    assert not any("<skills>" in t for t in user_texts(adapter.requests[-1]))
