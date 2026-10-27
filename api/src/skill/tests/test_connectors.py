"""Skillが宣言するConnectorと機密のSkill（design.md §4冒頭・§4.9.1、authorization.md §5.1）。宣言の保存、公開時の
検証（範囲・境界・Tool・公開する人の権限）、機密のフラグの付け外しの権限、一覧の表示"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from connector.factories import ConnectorFactory
from connector.models import VERSION_FIELDS, ConnectorScope
from skill.factories import SkillDraftFactory, SkillFactory, SkillVersionFactory
from skill.models import Skill, SkillScope
from user.factories import DepartmentFactory, DepartmentMembershipFactory, grant

from .test_api import SKILLS, _create, _department_editor, _draft, _publish, _skill_publisher

TABLES = [
    {"schema": "public", "name": "vendors", "description": "取引先"},
    {"schema": "public", "name": "orders", "description": "発注"},
]


def _patch(api_client, skill_id: str, draft: dict, **data):
    return api_client.patch(
        f"{SKILLS}{skill_id}/drafts/{draft['id']}/", {"revision": draft["revision"], **data}, format="json"
    )


def _declaration(connector, *, operations=("datasource_query",), tables=("public.vendors",)) -> dict:
    return {"connector": connector.uid, "operations": list(operations), "tables": list(tables)}


@pytest.mark.django_db
def test_declared_connector_is_saved_and_published(api_client, app_user):
    connector = ConnectorFactory.create(name="販売管理", version__tables=TABLES)
    skill = _create(api_client, app_user, instructions="照合する", tools=["datasource_query"])
    draft = _draft(api_client, skill["id"])
    res = _patch(api_client, skill["id"], draft, connectors=[_declaration(connector)])
    assert res.status_code == 200, res.json()
    draft = res.json()
    assert draft["connectors_info"] == [
        {
            "connector": connector.uid,
            "name": "販売管理",
            "is_sensitive": False,
            "operations": ["datasource_query"],
            "tables": ["public.vendors"],
        }
    ]
    assert draft["connector_problems"] == []
    res = _publish(api_client, skill["id"], draft)
    assert res.status_code == 201, res.json()
    assert res.json()["connectors"][0]["name"] == "販売管理"
    version = Skill.objects.get(uid=skill["id"]).current_version
    assert version is not None
    assert version.connectors == [_declaration(connector)]


@pytest.mark.django_db
def test_connector_the_editor_cannot_use_is_not_declared(api_client, app_user):
    other = ConnectorFactory.create(scope=ConnectorScope.DEPARTMENT, department=DepartmentFactory.create())
    skill = _create(api_client, app_user)
    draft = _draft(api_client, skill["id"])
    res = _patch(api_client, skill["id"], draft, connectors=[_declaration(other)])
    assert res.status_code == 400
    assert "connectors" in res.json()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("declaration", "tools", "message"),
    [
        ({"tables": ["public.secret"]}, ["datasource_query"], "公開していません"),
        ({}, [], "使ってよいToolに加えて"),
    ],
)
def test_publish_checks_the_boundary_and_tools(api_client, app_user, declaration, tools, message):
    connector = ConnectorFactory.create(version__tables=TABLES)
    skill = _create(api_client, app_user, instructions="照合する", tools=tools)
    draft = _draft(api_client, skill["id"])
    draft = _patch(api_client, skill["id"], draft, connectors=[_declaration(connector, **declaration)]).json()
    assert any(message in p for p in draft["connector_problems"])
    res = _publish(api_client, skill["id"], draft)
    assert res.status_code == 400
    assert message in res.json()["non_field_errors"][0]


@pytest.mark.django_db
def test_company_skill_cannot_declare_a_department_connector(api_client, app_user):
    department = DepartmentFactory.create()
    DepartmentMembershipFactory.create(department=department, user=app_user)
    connector = ConnectorFactory.create(scope=ConnectorScope.DEPARTMENT, department=department, version__tables=TABLES)
    _skill_publisher(app_user)
    skill = _create(api_client, app_user, scope=SkillScope.COMPANY, instructions="照合する", tools=["datasource_query"])
    draft = _draft(api_client, skill["id"])
    draft = _patch(api_client, skill["id"], draft, connectors=[_declaration(connector)]).json()
    res = _publish(api_client, skill["id"], draft)
    assert res.status_code == 400
    assert "範囲" in res.json()["non_field_errors"][0]


@pytest.mark.django_db
def test_department_skill_declares_its_department_connector(api_client, app_user):
    department = DepartmentFactory.create()
    _department_editor(app_user, department, publish=True)
    connector = ConnectorFactory.create(scope=ConnectorScope.DEPARTMENT, department=department, version__tables=TABLES)
    skill = _create(
        api_client,
        app_user,
        scope=SkillScope.DEPARTMENT,
        department=department.uid,
        instructions="照合する",
        tools=["datasource_query"],
    )
    draft = _draft(api_client, skill["id"])
    draft = _patch(api_client, skill["id"], draft, connectors=[_declaration(connector, tables=())]).json()
    assert _publish(api_client, skill["id"], draft).status_code == 201


@pytest.mark.django_db
def test_editor_sets_the_sensitive_flag_but_only_a_publisher_clears_it(api_client, app_user, other_user):
    department = DepartmentFactory.create()
    _department_editor(app_user, department)
    _department_editor(other_user, department, publish=True)
    skill = SkillFactory.create(scope=SkillScope.DEPARTMENT, department=department, owner=None, created_by=app_user)
    api_client.force_login(app_user)
    res = api_client.post(f"{SKILLS}{skill.uid}/drafts/", {}, format="json")
    draft = _patch(api_client, skill.uid, res.json(), is_sensitive=True).json()
    assert draft["is_sensitive"] and draft["sensitive"]
    res = _patch(api_client, skill.uid, draft, is_sensitive=False)
    assert res.status_code == 403
    api_client.force_login(other_user)
    res = _patch(api_client, skill.uid, draft, is_sensitive=False)
    assert res.status_code == 200, res.json()
    assert not res.json()["is_sensitive"]


@pytest.mark.django_db
def test_skill_declaring_a_sensitive_connector_is_sensitive(api_client, app_user):
    connector = ConnectorFactory.create(version__tables=TABLES)
    skill = SkillFactory.create(created_by=app_user)
    SkillVersionFactory.create(skill=skill, tools=["datasource_query"], connectors=[_declaration(connector)])
    api_client.force_login(app_user)
    [item] = api_client.get(SKILLS).json()["results"]
    assert not item["sensitive"] and item["connectors_usable"]
    # 公開した後にConnectorに機密のフラグが付けば、Skillも機密になる
    version = connector.current_version
    content = {name: getattr(version, name) for name in VERSION_FIELDS}
    connector.revise(actor=app_user, expected_version=1, **{**content, "is_sensitive": True})
    [item] = api_client.get(SKILLS).json()["results"]
    assert item["sensitive"]
    # 宣言した表を公開しなくなったら、会話で使えないことを示す
    connector.revise(actor=app_user, expected_version=2, **{**content, "is_sensitive": True, "tables": TABLES[1:]})
    [item] = api_client.get(SKILLS).json()["results"]
    assert not item["connectors_usable"]
    connector.revise(actor=app_user, expected_version=3, **{**content, "is_sensitive": True})
    # 使えなくなったら、会話で使えないことを示す
    connector.is_active = False
    connector.save()
    [item] = api_client.get(SKILLS).json()["results"]
    assert not item["connectors_usable"]


@pytest.mark.django_db
def test_list_reads_declared_connectors_at_once(api_client, app_user):
    def add_skill():
        connector = ConnectorFactory.create(version__tables=TABLES)
        skill = SkillFactory.create(created_by=app_user)
        SkillVersionFactory.create(skill=skill, tools=["datasource_query"], connectors=[_declaration(connector)])

    add_skill()
    api_client.force_login(app_user)
    with CaptureQueriesContext(connection) as one:
        api_client.get(SKILLS)
    add_skill()
    add_skill()
    with CaptureQueriesContext(connection) as three:
        assert len(api_client.get(SKILLS).json()["results"]) == 3
    assert len(three) == len(one)


@pytest.mark.django_db
def test_connector_detail_returns_published_tables_to_users_who_can_use_it(api_client, app_user):
    connector = ConnectorFactory.create(version__tables=TABLES)
    hidden = ConnectorFactory.create(scope=ConnectorScope.DEPARTMENT, department=DepartmentFactory.create())
    api_client.force_login(app_user)
    res = api_client.get(f"/api/app/v1/connectors/{connector.uid}/")
    assert res.status_code == 200, res.json()
    assert [t["name"] for t in res.json()["tables"]] == ["public.vendors", "public.orders"]
    assert res.json()["operations"] == ["datasource_describe", "datasource_query"]
    assert "connection" not in res.json()
    assert api_client.get(f"/api/app/v1/connectors/{hidden.uid}/").status_code == 404


@pytest.mark.django_db
def test_rollback_checks_the_declarations_of_the_old_version(api_client, app_user):
    connector = ConnectorFactory.create(version__tables=TABLES)
    skill = SkillFactory.create(created_by=app_user)
    SkillVersionFactory.create(skill=skill, tools=["datasource_query"], connectors=[_declaration(connector)])
    SkillVersionFactory.create(skill=skill, number=2)
    # 第1版が使う表を、Connectorが公開しなくなった
    content = {name: getattr(connector.current_version, name) for name in VERSION_FIELDS}
    connector.revise(actor=app_user, expected_version=1, **{**content, "tables": TABLES[1:]})
    api_client.force_login(app_user)
    res = api_client.post(f"{SKILLS}{skill.uid}/rollback/", {"number": 1, "expected_version": 2}, format="json")
    assert res.status_code == 400
    assert "切り戻せません" in str(res.json())


@pytest.mark.django_db
def test_skill_with_a_sensitive_draft_is_edited_only_in_confidential_work(api_client, app_user):
    skill = SkillFactory.create(created_by=app_user)
    SkillVersionFactory.create(skill=skill)
    SkillDraftFactory.create(skill=skill, is_sensitive=True)
    api_client.force_login(app_user)
    [item] = api_client.get(SKILLS).json()["results"]
    assert not item["sensitive"] and item["edit_sensitive"]


@pytest.mark.django_db
def test_system_connectors_are_offered_only_to_those_who_can_view_them(api_client, app_user):
    system = ConnectorFactory.create(scope=ConnectorScope.SYSTEM, version__tables=TABLES)
    ConnectorFactory.create(name="全社の接続")
    api_client.force_login(app_user)
    assert [c["name"] for c in api_client.get("/api/app/v1/connectors/").json()["results"]] == ["全社の接続"]
    assert api_client.get("/api/app/v1/connectors/?scope=4").json()["results"] == []
    assert api_client.get(f"/api/app/v1/connectors/{system.uid}/").status_code == 404
    api_client.force_login(grant(app_user, "connector.view_connector"))
    assert [c["id"] for c in api_client.get("/api/app/v1/connectors/?scope=4").json()["results"]] == [system.uid]
    # `@` の候補（scopeを付けない一覧）には出さない
    assert [c["name"] for c in api_client.get("/api/app/v1/connectors/").json()["results"]] == ["全社の接続"]
    assert api_client.get(f"/api/app/v1/connectors/{system.uid}/").status_code == 200
