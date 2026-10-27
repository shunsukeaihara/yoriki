"""会話で、Connectorを宣言したSkillと機密のSkillを使う（design.md §4冒頭・§4.9.1、harness.md §10.1・§10.9）"""

import pytest

from connector.factories import ConnectorFactory
from connector.models import VERSION_FIELDS, ConnectorScope
from conversation.dispatch import CannotStart, MessageMode, post_message
from conversation.factories import ConversationFactory
from conversation.models import Conversation
from llm.factories import LLMConnectionFactory, ModelProfileFactory
from llm.models import ProviderKind
from run.models import Run, RunEndReason, RunStatus
from skill.factories import SkillDraftFactory, SkillFactory, SkillVersionFactory
from user.factories import DepartmentFactory

from .fakes import FakeAdapter, calls, reply
from .test_skills import execute, publish, results

pytestmark = pytest.mark.usefixtures("default_runtime")

TABLES = [{"schema": "public", "name": "vendors", "description": "取引先"}]


@pytest.fixture(name="local_model")
def fixture_local_model(db):
    return ModelProfileFactory.create(
        name="社内LLM",
        version__connection=LLMConnectionFactory.create(kind=ProviderKind.LOCAL),
        version__supports_tools=True,
    )


def _declaring(owner, connector, name="取引先の照合", **content):
    declaration = {"connector": connector.uid, "operations": ["datasource_query"], "tables": ["public.vendors"]}
    return publish(owner, name=name, tools=["datasource_query"], connectors=[declaration], **content)


def _start(conversation: Conversation, body="取引先を確かめて", **kwargs) -> Run:
    post_message(
        conversation, body=body, client_message_id="", mode=MessageMode.AUTO, actor=conversation.owner, **kwargs
    )
    return Run.objects.filter(conversation=conversation).latest("pk")


def _catalog_names(run: Run) -> list[str]:
    return [e["name"] for e in (run.manifest["fixed"]["skill_catalog"] or {}).get("skills", [])]


def _make_sensitive(connector) -> None:
    content = {name: getattr(connector.current_version, name) for name in VERSION_FIELDS}
    connector.revise(
        actor=connector.created_by, expected_version=connector.version_number, **content | {"is_sensitive": True}
    )


@pytest.mark.django_db
def test_skill_is_listed_only_when_the_user_can_use_its_connectors(app_user):
    usable = ConnectorFactory.create(name="販売管理", version__tables=TABLES)
    other = ConnectorFactory.create(scope=ConnectorScope.DEPARTMENT, department=DepartmentFactory.create())
    _declaring(app_user, usable)
    _declaring(app_user, other, name="他部門の照合")
    run = _start(ConversationFactory.create(owner=app_user))
    assert _catalog_names(run) == ["取引先の照合"]


@pytest.mark.django_db
def test_loaded_skill_shows_its_connectors(app_user):
    connector = ConnectorFactory.create(name="販売管理", version__tables=TABLES)
    _declaring(app_user, connector)
    run = _start(ConversationFactory.create(owner=app_user))
    adapter = FakeAdapter([calls(("c1", "skill", {"name": "取引先の照合"})), reply("ok")])
    run = execute(run, adapter)
    [result] = results(run, "skill")
    text = result["content"][0]["text"]
    assert "<connectors>" in text
    assert "販売管理 — datasource_query (tables: public.vendors)" in text


@pytest.mark.django_db
def test_chosen_skill_needs_its_connectors(app_user):
    other = ConnectorFactory.create(scope=ConnectorScope.DEPARTMENT, department=DepartmentFactory.create())
    skill, _version = _declaring(app_user, other)
    with pytest.raises(CannotStart, match="Connector"):
        _start(ConversationFactory.create(owner=app_user), skills=[skill])


@pytest.mark.django_db
def test_chosen_skill_puts_its_connectors_first_in_the_catalog(app_user, monkeypatch):
    from harness import connectors as connectors_module  # noqa: PLC0415

    monkeypatch.setattr(connectors_module, "MAX_CATALOG_CONNECTORS", 1)
    ConnectorFactory.create(name="あ先頭の接続", version__tables=TABLES)
    declared = ConnectorFactory.create(name="販売管理", version__tables=TABLES)
    skill, _version = _declaring(app_user, declared)
    run = _start(ConversationFactory.create(owner=app_user), skills=[skill])
    assert [e["name"] for e in run.manifest["fixed"]["connector_catalog"]["connectors"]] == ["販売管理"]


@pytest.mark.django_db
@pytest.mark.parametrize("by_connector", [False, True])
def test_sensitive_skill_is_used_only_in_a_confidential_conversation(app_user, local_model, by_connector):
    connector = ConnectorFactory.create(name="人事", version__tables=TABLES, version__is_sensitive=by_connector)
    if by_connector:
        skill, _version = _declaring(app_user, connector, name="人事の照会")
    else:
        skill, _version = publish(app_user, name="人事の照会", is_sensitive=True)
    plain = ConversationFactory.create(owner=app_user, model_profile=local_model)
    assert "人事の照会" not in _catalog_names(_start(plain))
    with pytest.raises(CannotStart, match="機密"):
        _start(ConversationFactory.create(owner=app_user, model_profile=local_model), skills=[skill])
    confidential = ConversationFactory.create(owner=app_user, model_profile=local_model, handles_sensitive_data=True)
    assert "人事の照会" in _catalog_names(_start(confidential))


@pytest.mark.django_db
def test_run_stops_when_a_declared_connector_becomes_sensitive(app_user):
    connector = ConnectorFactory.create(name="販売管理", version__tables=TABLES)
    skill, _version = _declaring(app_user, connector)
    run = _start(ConversationFactory.create(owner=app_user), skills=[skill])
    _make_sensitive(connector)
    run = execute(run, FakeAdapter([]))
    assert run.status == RunStatus.FAILED
    assert run.end_reason == RunEndReason.SKILL
    assert "機密" in run.error


@pytest.mark.django_db
def test_run_stops_when_the_user_can_no_longer_use_a_declared_connector(app_user):
    connector = ConnectorFactory.create(name="販売管理", version__tables=TABLES)
    skill, _version = _declaring(app_user, connector)
    run = _start(ConversationFactory.create(owner=app_user), skills=[skill])
    connector.is_active = False
    connector.save(update_fields=["is_active"])
    run = execute(run, FakeAdapter([]))
    assert run.status == RunStatus.FAILED
    assert run.end_reason == RunEndReason.SKILL
    assert "Connector" in run.error


@pytest.mark.django_db
def test_sensitive_skill_is_edited_only_in_a_confidential_conversation(app_user, local_model):
    skill = SkillFactory.create(name="人事の照会", created_by=app_user)
    SkillDraftFactory.create(skill=skill, is_sensitive=True)
    target = {"id": skill.uid, "name": skill.name}
    with pytest.raises(CannotStart, match="機密"):
        _start(ConversationFactory.create(owner=app_user), skill_edit=target)
    confidential = ConversationFactory.create(owner=app_user, model_profile=local_model, handles_sensitive_data=True)
    run = _start(confidential, skill_edit=target)
    assert run.manifest["fixed"]["skill_edit"]["skill"] == skill.uid


@pytest.mark.django_db
def test_agent_declares_connectors_by_the_catalog_names(app_user):
    connector = ConnectorFactory.create(name="販売管理", version__tables=TABLES)
    skill = SkillFactory.create(name="取引先の照合", created_by=app_user)
    SkillVersionFactory.create(skill=skill, tools=["datasource_query"])
    run = _start(ConversationFactory.create(owner=app_user), skill_edit={"id": skill.uid, "name": skill.name})
    declaration = {"name": "販売管理", "operations": ["datasource_query"], "tables": ["public.vendors"]}
    unknown = {"name": "知らない接続", "operations": ["datasource_query"], "tables": []}
    adapter = FakeAdapter(
        [
            calls(("c1", "update_skill_draft", {"expected_revision": 0, "connectors": [unknown]})),
            calls(("c2", "update_skill_draft", {"expected_revision": 0, "connectors": [declaration]})),
            calls(("c3", "update_skill_draft", {"expected_revision": 1, "is_sensitive": True})),
            reply("ok"),
        ]
    )
    run = execute(run, adapter)
    first, second, third = results(run, "update_skill_draft")
    assert first["is_error"] and "販売管理" in first["content"][0]["text"]
    assert not second["is_error"], second
    # 機密でない会話では、機密のフラグを付けさせない（付けると、この会話では続けて扱えなくなる）
    assert third["is_error"]
    draft = skill.drafts.get()
    assert draft.connectors == [
        {"connector": connector.uid, "operations": ["datasource_query"], "tables": ["public.vendors"]}
    ]
    assert not draft.is_sensitive
