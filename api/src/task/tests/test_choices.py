"""タスクの編集画面の候補のAPIと詳細の項目（authorization.md §5.4・§7.2）"""

import pytest

from connector.factories import ConnectorFactory
from connector.models import Connector, ConnectorScope
from llm.factories import ModelProfileFactory
from skill.factories import SkillFactory, SkillVersionFactory
from skill.models import Skill, SkillScope
from task import checks
from task.models import TaskDefinition, TaskScope
from user.factories import DepartmentFactory, DepartmentMembershipFactory, UserFactory, grant
from utils.permissions import AppPermission

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("default_runtime")]

TASKS = "/api/app/v1/tasks/"
SKILL_CHOICES = f"{TASKS}skill_choices/"
CONNECTOR_CHOICES = f"{TASKS}connector_choices/"


def _published(scope, **fields):
    skill = SkillFactory.create(scope=scope, **fields)
    SkillVersionFactory.create(skill=skill)
    return skill


@pytest.fixture(name="world")
def fixture_world(app_user):
    """範囲・部門・状態の違うSkillとConnector"""
    department, other = DepartmentFactory.create(), DepartmentFactory.create()
    DepartmentMembershipFactory.create(department=department, user=app_user)
    gone = DepartmentFactory.create()
    skills = {
        "mine": _published(SkillScope.PERSONAL, created_by=app_user),
        "someone": _published(SkillScope.PERSONAL),
        "department": _published(SkillScope.DEPARTMENT, department=department, owner=None),
        "other_department": _published(SkillScope.DEPARTMENT, department=other, owner=None),
        "gone_department": _published(SkillScope.DEPARTMENT, department=gone, owner=None),
        "company": _published(SkillScope.COMPANY, owner=None),
        "system": _published(SkillScope.SYSTEM, owner=None),
        "unpublished": SkillFactory.create(scope=SkillScope.COMPANY, owner=None),
        "suspended": _published(SkillScope.COMPANY, owner=None, suspended=True),
    }
    connectors = {
        "department": ConnectorFactory.create(scope=ConnectorScope.DEPARTMENT, department=department),
        "other_department": ConnectorFactory.create(scope=ConnectorScope.DEPARTMENT, department=other),
        "gone_department": ConnectorFactory.create(scope=ConnectorScope.DEPARTMENT, department=gone),
        "company": ConnectorFactory.create(scope=ConnectorScope.COMPANY),
        "system": ConnectorFactory.create(scope=ConnectorScope.SYSTEM),
        "inactive": ConnectorFactory.create(scope=ConnectorScope.COMPANY, is_active=False),
    }
    gone.deleted = True
    gone.save()
    return {"department": department, "other": other, "skills": skills, "connectors": connectors}


def _tasks(world, user):
    return [
        TaskDefinition(scope=TaskScope.PERSONAL, owner=user),
        TaskDefinition(scope=TaskScope.DEPARTMENT, department=world["department"]),
        TaskDefinition(scope=TaskScope.COMPANY),
        TaskDefinition(scope=TaskScope.SYSTEM),
    ]


def test_choices_agree_with_the_checks_on_save(world, app_user):
    """候補（QuerySet）と保存のときの検査（1件の判定）が同じ結果になる（authorization.md §7.2）"""
    for task in _tasks(world, app_user):
        listed = set(checks.allowed_skills(task).values_list("pk", flat=True))
        assert listed == {s.pk for s in Skill.objects.all() if checks.skill_allowed(task, s)}, task.scope
        listed = set(checks.allowed_connectors(task).values_list("pk", flat=True))
        assert listed == {c.pk for c in Connector.objects.all() if checks.connector_allowed(task, c)}, task.scope


def test_department_task_choices(api_client, app_user, world):
    manager = grant(app_user, AppPermission.MANAGE_DEPARTMENT_TASKS)
    api_client.force_login(manager)
    params = {"scope": TaskScope.DEPARTMENT, "department": world["department"].uid}
    res = api_client.get(SKILL_CHOICES, params)
    assert res.status_code == 200, res.json()
    names = {s["name"] for s in res.json()["results"]}
    skills = world["skills"]
    assert names == {skills["department"].name, skills["company"].name}
    entry = next(s for s in res.json()["results"] if s["id"] == skills["department"].uid)
    assert entry["version"] == 1
    assert entry["versions"] == [1]
    assert entry["department_info"]["id"] == world["department"].uid

    res = api_client.get(CONNECTOR_CHOICES, params)
    assert res.status_code == 200, res.json()
    connectors = world["connectors"]
    assert {c["id"] for c in res.json()["results"]} == {connectors["department"].uid, connectors["company"].uid}
    # 接続先は返さない（候補のAPIと設定のAPIを分ける）
    assert "connection" not in res.json()["results"][0]


def test_versions_are_listed_newest_first(api_client, app_user):
    skill = _published(SkillScope.PERSONAL, created_by=app_user)
    SkillVersionFactory.create(skill=skill)
    api_client.force_login(app_user)
    [entry] = api_client.get(SKILL_CHOICES, {"scope": TaskScope.PERSONAL}).json()["results"]
    assert entry["version"] == 2
    assert entry["versions"] == [2, 1]


def test_choices_need_the_right_to_manage_the_scope(api_client, app_user, world):
    api_client.force_login(app_user)
    department = {"scope": TaskScope.DEPARTMENT, "department": world["department"].uid}
    assert api_client.get(SKILL_CHOICES, department).status_code == 403  # permissionがない
    assert api_client.get(SKILL_CHOICES, {"scope": TaskScope.COMPANY}).status_code == 403
    assert api_client.get(CONNECTOR_CHOICES, {"scope": TaskScope.SYSTEM}).status_code == 403
    assert api_client.get(SKILL_CHOICES).status_code == 400  # 範囲がない
    assert api_client.get(SKILL_CHOICES, {"scope": TaskScope.DEPARTMENT}).status_code == 400  # 部門がない

    # 管理できない部門（所属していない）の候補は見せない
    manager = grant(app_user, AppPermission.MANAGE_DEPARTMENT_TASKS)
    api_client.force_login(manager)
    other = {"scope": TaskScope.DEPARTMENT, "department": world["other"].uid}
    assert api_client.get(SKILL_CHOICES, other).status_code == 403

    # 個人のタスクの候補は、本人が使えるもの
    res = api_client.get(SKILL_CHOICES, {"scope": TaskScope.PERSONAL})
    skills = world["skills"]
    assert {s["id"] for s in res.json()["results"]} == {
        skills["mine"].uid,
        skills["department"].uid,
        skills["company"].uid,
    }


def test_detail_shows_names_and_problems(api_client, app_user):
    model = ModelProfileFactory.create(version__supports_tools=True)
    skill = SkillFactory.create(scope=SkillScope.COMPANY, name="請求書の確認", owner=None)
    SkillVersionFactory.create(skill=skill, tools=["read", "datasource_query"])
    notify = _published(SkillScope.COMPANY, owner=None, name="結果の要約")
    connector = ConnectorFactory.create(name="販売管理")
    manager = grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)
    api_client.force_login(manager)
    res = api_client.post(
        TASKS,
        {
            "scope": TaskScope.COMPANY,
            "name": "請求書チェック",
            "prompt_template": "確認してください",
            "model_profile": model.uid,
            "skills": [{"skill": skill.uid, "version": 1}],
            "connectors": [connector.uid],
            "tools": ["read", "datasource_query"],
        },
        format="json",
    )
    assert res.status_code == 201, res.json()
    task = res.json()
    assert task["skills_info"] == [{"id": skill.uid, "name": "請求書の確認", "deleted": False}]
    assert task["connectors_info"] == [{"id": connector.uid, "name": "販売管理", "deleted": False}]
    assert task["problems"] == []
    assert notify.uid not in {s["id"] for s in task["skills_info"]}

    # 版を作った後にSkillを止め、Connectorを消すと、今の版のままでは実行できない理由が出る
    skill.suspended = True
    skill.save()
    connector.deleted = True
    connector.save()
    task = api_client.get(f"{TASKS}{task['id']}/").json()
    assert task["connectors_info"] == [{"id": connector.uid, "name": "販売管理", "deleted": True}]
    assert "Skill「請求書の確認」は、このタスクでは使えません" in task["problems"]
    assert "登録したConnectorが削除されました" in task["problems"]
    # 一覧は詳細の項目を返さない（件数でクエリを増やさない）
    assert "problems" not in api_client.get(TASKS).json()["results"][0]
