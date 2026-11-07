"""タスクのRun（harness.md §7.5・§10、authorization.md §5.4・§6）。固定したSkill・Connector・Toolだけを使い、
起動した人ではなくタスクの範囲で判定し、タスクの停止・Skillの停止で止まる"""

import pytest

from connector.factories import ConnectorFactory
from connector.models import Connector, ConnectorAction, ConnectorScope
from harness import connectors as connectors_module
from harness import skills as skills_module
from harness import tasks
from harness.catalogs import build_task_catalogs
from harness.tests.test_connectors import TABLES, execute, fixture_fake_datasource, results, text_of
from llm.factories import ModelProfileFactory
from run.models import Run, RunEndReason, RunEvent, RunEventKind, RunLaunch, RunStatus
from run.tasks import claim_run
from skill import connectors as skill_connectors
from skill.factories import SkillFactory, SkillVersionFactory
from skill.models import SkillScope
from task import checks
from task.dispatch import start_task_run
from task.errors import CannotRun
from task.factories import TaskDefinitionFactory
from task.models import TaskDefinition, TaskScope
from user.factories import DepartmentFactory, DepartmentMembershipFactory, UserFactory, grant
from utils.permissions import AppPermission

from .fakes import FakeAdapter, calls, reply

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("default_runtime")]

__all__ = ["fixture_fake_datasource"]  # 接続先につながないAdapter（autouse）

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


@pytest.fixture(name="admin")
def fixture_admin():
    """システムのタスクを管理する人。部門には所属しない"""
    return grant(UserFactory.create(), AppPermission.MANAGE_SYSTEM_TASKS)


def _system_skill(**version):
    skill = SkillFactory.create(scope=SkillScope.SYSTEM, name="突合", owner=None)
    SkillVersionFactory.create(skill=skill, **version)
    return skill


def _start(task, actor) -> Run:
    return start_task_run(task, inputs={}, actor=actor, launch=RunLaunch.MANUAL)


def test_task_run_can_use_everything_in_the_scope_of_the_task(admin):
    """定義で選んだものは使うよう指示するもので、アクセス範囲はタスクの範囲で決まる（authorization.md §5.4）"""
    connector = ConnectorFactory.create(
        name="経理DB", scope=ConnectorScope.DEPARTMENT, department=DepartmentFactory.create(), version__tables=TABLES
    )
    ConnectorFactory.create(name="販売管理", version__tables=TABLES)  # 定義で選んでいなくても範囲の中
    skill = _system_skill(tools=["read", "shell", "datasource_query"], output_schema=SCHEMA)
    task = TaskDefinitionFactory.create(
        scope=TaskScope.SYSTEM,
        created_by=admin,
        revision__skills=[{"skill": skill.uid, "version": 1}],
        revision__connectors=[connector.uid],
        revision__model_profile=ModelProfileFactory.create(version__supports_tools=True),
    )
    run = _start(task, admin)
    adapter = FakeAdapter(
        [
            calls(("c1", "datasource_query", {"connector": "経理DB", "sql": "SELECT code FROM vendors"})),
            calls(("c3", "datasource_query", {"connector": "販売管理", "sql": "SELECT code FROM vendors"})),
            calls(("c2", "submit_result", {"ok": True})),
            reply("確認しました"),
        ]
    )
    run = execute(run, adapter)

    assert run.status == RunStatus.COMPLETED, run.error
    # 選んだConnectorは使うよう指示する
    assert any("Use these connectors" in m.text and "public.vendors" in m.text for m in adapter.requests[0].messages)
    assert run.result == {"ok": True}
    first = adapter.requests[0]
    # Skillの `tools` では絞らない。答える人がいないので ask_user は出さない
    names = {t.name for t in first.tools}
    assert {"read", "write", "shell", "datasource_query", "datasource_describe", "skill", "submit_result"} <= names
    assert not names & {"ask_user", "request_skill_edit"}
    query_tool = next(t for t in first.tools if t.name == "datasource_query")
    assert "enum" not in query_tool.parameters["properties"]["connector"]
    context = next(m.text for m in first.messages if m.role == "user" and "<runtime_context>" in m.text)
    assert "<mode>task</mode>" in context
    assert '<connector name="販売管理">' in context
    # 起動した人は部門に所属していないが、システムのタスクの範囲で部門のConnectorを使える
    assert not any(queried["is_error"] for queried in results(run, "datasource_query"))


def test_connector_disabled_during_the_task_run_is_refused(admin):
    connector = ConnectorFactory.create(name="経理DB", scope=ConnectorScope.SYSTEM, version__tables=TABLES)
    skill = _system_skill(tools=["datasource_query"])
    task = TaskDefinitionFactory.create(
        scope=TaskScope.SYSTEM,
        created_by=admin,
        revision__skills=[{"skill": skill.uid, "version": 1}],
        revision__connectors=[connector.uid],
    )
    run = _start(task, admin)
    connector.is_active = False
    connector.save(update_fields=["is_active"])
    adapter = FakeAdapter(
        [calls(("c1", "datasource_query", {"connector": "経理DB", "sql": "SELECT 1"})), reply("使えませんでした")]
    )
    run = execute(run, adapter)
    [queried] = results(run, "datasource_query")
    assert queried["is_error"]
    assert "cannot be used now" in text_of(queried)


def test_suspending_a_task_cancels_the_queued_run_even_if_resumed(admin):
    task = TaskDefinitionFactory.create(scope=TaskScope.SYSTEM, created_by=admin)
    run = _start(task, admin)
    task.set_suspended(True, actor=admin)
    task.set_suspended(False, actor=admin)  # すぐ再開しても、停止の前に受け付けたRunは動かさない
    run.refresh_from_db()
    assert run.status == RunStatus.CANCELLED
    assert claim_run(run.pk) is None


def test_suspended_task_stops_the_queued_run_at_the_boundary(admin):
    """停止の依頼より先にWorkerが受け取っていても、次の区切りで止める"""
    task = TaskDefinitionFactory.create(scope=TaskScope.SYSTEM, created_by=admin)
    run = _start(task, admin)
    TaskDefinition.objects.filter(pk=task.pk).update(suspended=True)
    adapter = FakeAdapter([])
    run = execute(run, adapter)
    assert (run.status, run.end_reason) == (RunStatus.FAILED, RunEndReason.TASK)
    assert "タスクが停止されました" in run.error
    assert adapter.requests == []


def test_deleting_a_task_stops_its_runs(api_client, admin):
    task = TaskDefinitionFactory.create(scope=TaskScope.SYSTEM, created_by=admin)
    run = _start(task, admin)
    api_client.force_login(admin)
    assert api_client.delete(f"/api/app/v1/tasks/{task.uid}/").status_code == 204
    run.refresh_from_db()
    assert run.status == RunStatus.CANCELLED


def test_task_run_continues_when_the_instructed_skill_is_suspended(admin):
    """始めた後の停止ではRunを止めない。次の受付からは始めない"""
    skill = _system_skill()
    task = TaskDefinitionFactory.create(
        scope=TaskScope.SYSTEM, created_by=admin, revision__skills=[{"skill": skill.uid, "version": 1}]
    )
    run = _start(task, admin)
    skill.set_suspended(True, actor=admin)
    run = execute(run, FakeAdapter([reply("終わりました")]))
    assert run.status == RunStatus.COMPLETED
    with pytest.raises(CannotRun):
        _start(task, admin)


def test_task_run_is_not_refused_when_the_launcher_loses_the_permission(admin):
    """入口・手動実行のRunはタスクに固定したものの範囲で動き、起動した人の権限には依らない（authorization.md §6）"""
    task = TaskDefinitionFactory.create(scope=TaskScope.SYSTEM, created_by=admin)
    run = _start(task, admin)
    admin.user_permissions.clear()
    run = execute(run, FakeAdapter([reply("完了しました")]))
    assert run.status == RunStatus.COMPLETED


def test_personal_task_run_stops_when_the_owner_is_deactivated(app_user):
    task = TaskDefinitionFactory.create(created_by=app_user)
    run = _start(task, app_user)
    app_user.is_active = False
    app_user.save(update_fields=["is_active"])
    run = execute(run, FakeAdapter([]))
    assert (run.status, run.end_reason) == (RunStatus.FAILED, RunEndReason.TASK)


def test_task_run_is_refused_when_a_fixed_skill_left_the_scope(admin):
    skill = _system_skill()
    task = TaskDefinitionFactory.create(
        scope=TaskScope.SYSTEM, created_by=admin, revision__skills=[{"skill": skill.uid, "version": 1}]
    )
    skill.deleted = True
    skill.save(update_fields=["deleted"])
    with pytest.raises(CannotRun):
        _start(task, admin)


def test_suspended_task_does_not_replay_a_pending_tool_call(admin, fake_datasource):
    """障害の後のAttemptでも、停止したタスクの読むだけのToolをやり直さない"""
    connector = ConnectorFactory.create(name="経理DB", scope=ConnectorScope.SYSTEM, version__tables=TABLES)
    skill = _system_skill(tools=["datasource_query"])
    task = TaskDefinitionFactory.create(
        scope=TaskScope.SYSTEM,
        created_by=admin,
        revision__skills=[{"skill": skill.uid, "version": 1}],
        revision__connectors=[connector.uid],
    )
    run = _start(task, admin)
    call = {"id": "c1", "name": "datasource_query", "arguments": {"connector": "経理DB", "sql": "SELECT 1"}}
    RunEvent.record(run, RunEventKind.ASSISTANT_MESSAGE, {"step": 1, "text": "", "tool_calls": [call]})
    TaskDefinition.objects.filter(pk=task.pk).update(suspended=True)  # Workerが受け取った後に止めた
    run = execute(run, FakeAdapter([]))
    assert fake_datasource.calls == []
    [result] = results(run, "datasource_query")
    assert "interrupted" in text_of(result)  # やり直さず、結果の分からない呼出しとして閉じた
    assert (run.status, run.end_reason) == (RunStatus.FAILED, RunEndReason.TASK)


def test_department_task_stops_when_the_department_is_disabled(app_user):
    department = DepartmentFactory.create()
    manager = grant(app_user, AppPermission.MANAGE_DEPARTMENT_TASKS)
    DepartmentMembershipFactory.create(department=department, user=manager)
    task = TaskDefinitionFactory.create(scope=TaskScope.DEPARTMENT, department=department, created_by=manager)
    run = _start(task, manager)
    department.deleted = True
    department.save(update_fields=["deleted"])
    run = execute(run, FakeAdapter([]))
    assert (run.status, run.end_reason) == (RunStatus.FAILED, RunEndReason.TASK)
    assert "部門が無効" in run.error


def test_system_task_cannot_use_a_connector_of_a_disabled_department(admin):
    department = DepartmentFactory.create()
    connector = ConnectorFactory.create(
        name="経理DB", scope=ConnectorScope.DEPARTMENT, department=department, version__tables=TABLES
    )
    skill = _system_skill(tools=["datasource_query"])
    task = TaskDefinitionFactory.create(
        scope=TaskScope.SYSTEM,
        created_by=admin,
        revision__skills=[{"skill": skill.uid, "version": 1}],
        revision__connectors=[connector.uid],
    )
    run = _start(task, admin)
    department.deleted = True
    department.save(update_fields=["deleted"])
    run = execute(
        run,
        FakeAdapter([calls(("c1", "datasource_query", {"connector": "経理DB", "sql": "SELECT 1"})), reply("止めます")]),
    )
    [queried] = results(run, "datasource_query")
    assert queried["is_error"]


def test_task_run_cannot_use_what_is_outside_the_scope_of_the_task():
    department = DepartmentFactory.create()
    manager = grant(UserFactory.create(), AppPermission.MANAGE_DEPARTMENT_TASKS)
    DepartmentMembershipFactory.create(department=department, user=manager)
    ConnectorFactory.create(name="他部門DB", scope=ConnectorScope.DEPARTMENT, department=DepartmentFactory.create())
    ConnectorFactory.create(name="全社DB", version__tables=TABLES)
    outside = SkillFactory.create(
        scope=SkillScope.DEPARTMENT, department=DepartmentFactory.create(), name="他部門の手順"
    )
    SkillVersionFactory.create(skill=outside)
    inside = SkillFactory.create(scope=SkillScope.COMPANY, name="全社の手順", owner=None)
    SkillVersionFactory.create(skill=inside, instructions="全社の手順の本文")
    task = TaskDefinitionFactory.create(
        scope=TaskScope.DEPARTMENT,
        department=department,
        created_by=manager,
        revision__model_profile=ModelProfileFactory.create(version__supports_tools=True),
    )
    run = _start(task, manager)
    adapter = FakeAdapter(
        [
            calls(("c1", "datasource_describe", {"connector": "他部門DB"})),
            calls(("c2", "skill", {"name": "他部門の手順"})),
            calls(("c3", "skill", {"name": "全社の手順"})),
            reply("確認しました"),
        ]
    )
    run = execute(run, adapter)

    assert run.status == RunStatus.COMPLETED, run.error
    context = next(m.text for m in adapter.requests[0].messages if m.role == "user" and "<runtime_context>" in m.text)
    assert "全社DB" in context and "他部門DB" not in context
    assert "全社の手順" in context and "他部門の手順" not in context
    [connector] = results(run, "datasource_describe")
    assert connector["is_error"]
    missing, loaded = results(run, "skill")
    assert missing["is_error"]
    assert "全社の手順の本文" in text_of(loaded)


# --- チャットから起動したRun（authorization.md §6） ---


def _department_task_with_connector(manager, department):
    connector = ConnectorFactory.create(
        name="経理DB", scope=ConnectorScope.DEPARTMENT, department=department, version__tables=TABLES
    )
    skill = SkillFactory.create(scope=SkillScope.COMPANY, name="照合", owner=None)
    SkillVersionFactory.create(skill=skill, tools=["datasource_query"], connectors=[])
    return TaskDefinitionFactory.create(
        scope=TaskScope.DEPARTMENT,
        department=department,
        created_by=manager,
        chat_launchable=True,
        revision__skills=[{"skill": skill.uid, "version": 1}],
        revision__connectors=[connector.uid],
    )


def test_chat_launched_run_stops_when_the_launcher_leaves_the_department(app_user):
    """チャットから起動したRunは、起動した人が今もそのタスクを起動できる立場かを区切りごとに確かめる"""
    department = DepartmentFactory.create()
    manager = grant(UserFactory.create(), AppPermission.MANAGE_DEPARTMENT_TASKS)
    DepartmentMembershipFactory.create(department=department, user=manager)
    task = _department_task_with_connector(manager, department)
    membership = DepartmentMembershipFactory.create(department=department, user=app_user)
    run = start_task_run(task, inputs={}, actor=app_user, launch=RunLaunch.CHAT)
    assert (run.owner, run.launch) == (app_user, RunLaunch.CHAT)
    membership.delete()  # 起動した後に部門を離れた
    run = execute(run, FakeAdapter([]))
    assert (run.status, run.end_reason) == (RunStatus.FAILED, RunEndReason.TASK)
    assert "部門から外れました" in run.error


def test_chat_launched_run_stops_when_the_launcher_is_deactivated(app_user):
    task = TaskDefinitionFactory.create(scope=TaskScope.COMPANY, created_by=app_user, chat_launchable=True)
    run = start_task_run(task, inputs={}, actor=app_user, launch=RunLaunch.CHAT)
    app_user.is_active = False
    app_user.save(update_fields=["is_active"])
    run = execute(run, FakeAdapter([]))
    assert (run.status, run.end_reason) == (RunStatus.FAILED, RunEndReason.TASK)
    assert "起動した人" in run.error


def test_chat_launched_run_works_in_the_scope_of_the_task(app_user):
    """起動できる人はタスクより狭い権限を持たないので、起動した人とは照らし合わせず、タスクの範囲で動く"""
    department = DepartmentFactory.create()
    manager = grant(UserFactory.create(), AppPermission.MANAGE_DEPARTMENT_TASKS)
    DepartmentMembershipFactory.create(department=department, user=manager)
    task = _department_task_with_connector(manager, department)
    DepartmentMembershipFactory.create(department=department, user=app_user)
    run = start_task_run(task, inputs={}, actor=app_user, launch=RunLaunch.CHAT)
    run = execute(
        run,
        FakeAdapter(
            [calls(("c1", "datasource_query", {"connector": "経理DB", "sql": "SELECT 1"})), reply("確認しました")]
        ),
    )
    [queried] = results(run, "datasource_query")
    assert not queried["is_error"], text_of(queried)


def test_chat_launched_run_is_refused_the_connector_the_launcher_cannot_use(app_user, monkeypatch):
    """Connectorの操作のたびに、起動した人の今の権限でも判定する（タスクの範囲 ∩ 起動した人の権限）"""
    department = DepartmentFactory.create()
    manager = grant(UserFactory.create(), AppPermission.MANAGE_DEPARTMENT_TASKS)
    DepartmentMembershipFactory.create(department=department, user=manager)
    task = _department_task_with_connector(manager, department)
    DepartmentMembershipFactory.create(department=department, user=app_user)
    run = start_task_run(task, inputs={}, actor=app_user, launch=RunLaunch.CHAT)
    monkeypatch.setattr(tasks.checks, "launcher_connector_permitted", lambda user, connector: False)
    run = execute(
        run,
        FakeAdapter(
            [calls(("c1", "datasource_query", {"connector": "経理DB", "sql": "SELECT 1"})), reply("使えませんでした")]
        ),
    )
    [queried] = results(run, "datasource_query")
    assert queried["is_error"]
    assert "cannot be used now" in text_of(queried)


def _scope_world(personal_owner):
    """範囲ごとのSkill・Connector（2つの部門、全社、システム、個人）"""
    department, other = DepartmentFactory.create(), DepartmentFactory.create()
    for scope, place, label in (
        (SkillScope.DEPARTMENT, department, "部門"),
        (SkillScope.DEPARTMENT, other, "他部門"),
        (SkillScope.COMPANY, None, "全社"),
        (SkillScope.SYSTEM, None, "システム"),
    ):
        skill = SkillFactory.create(scope=scope, department=place, owner=None, name=f"{label}のSkill")
        SkillVersionFactory.create(skill=skill)
    personal = SkillFactory.create(created_by=personal_owner, name="個人のSkill")
    SkillVersionFactory.create(skill=personal)
    for connector_scope, place, label in (
        (ConnectorScope.DEPARTMENT, department, "部門"),
        (ConnectorScope.DEPARTMENT, other, "他部門"),
        (ConnectorScope.COMPANY, None, "全社"),
        (ConnectorScope.SYSTEM, None, "システム"),
    ):
        ConnectorFactory.create(
            scope=connector_scope, department=place, name=f"{label}のConnector", version__tables=TABLES
        )
    return department


def _scope_world_in(department):
    other = DepartmentFactory.create()
    for scope, place, label in ((SkillScope.COMPANY, None, "全社"), (SkillScope.DEPARTMENT, other, "他部門")):
        skill = SkillFactory.create(scope=scope, department=place, owner=None, name=f"{label}のSkill")
        SkillVersionFactory.create(skill=skill)
    ConnectorFactory.create(name="全社のConnector", version__tables=TABLES)
    ConnectorFactory.create(
        scope=ConnectorScope.DEPARTMENT, department=other, name="他部門のConnector", version__tables=TABLES
    )


def _names(catalogs) -> tuple[set[str], set[str]]:
    skills, connectors = catalogs
    return {e["name"] for e in skills["skills"]}, {e["name"] for e in connectors["connectors"]}


def _launchers_and_tasks():
    """（タスクの範囲、起動できる人の種類）。authorization.md §5.3 の表のすべて"""
    return [
        (TaskScope.DEPARTMENT, "member"),
        (TaskScope.DEPARTMENT, "manager_of_all"),
        (TaskScope.COMPANY, "anyone"),
        (TaskScope.PERSONAL, "owner"),
    ]


@pytest.mark.parametrize(("scope", "kind"), _launchers_and_tasks())
def test_launcher_access_intersected_with_the_task_scope_equals_the_task_scope(scope, kind):
    """起動できる人はタスクより狭い権限を持たないので、タスクの範囲 ∩ 起動した人の権限は、タスクの範囲と同じになる"""
    launcher = UserFactory.create()
    department = _scope_world(launcher)
    if kind == "member":
        DepartmentMembershipFactory.create(department=department, user=launcher)
    elif kind == "manager_of_all":
        grant(launcher, AppPermission.MANAGE_ALL_DEPARTMENTS)
    launcher = type(launcher).objects.get(pk=launcher.pk)
    owner = (
        launcher if scope == TaskScope.PERSONAL else grant(UserFactory.create(), AppPermission.MANAGE_DEPARTMENT_TASKS)
    )
    task = TaskDefinitionFactory.create(
        scope=scope,
        department=department if scope == TaskScope.DEPARTMENT else None,
        created_by=owner,
        chat_launchable=True,
    )
    assert TaskDefinition.objects.launchable_by(launcher).filter(pk=task.pk).exists()
    version = ModelProfileFactory.create(version__supports_tools=True).current_version

    in_scope = build_task_catalogs(task, version, sensitive=False, explicit=[])
    intersected = build_task_catalogs(task, version, sensitive=False, explicit=[], launcher=launcher)

    assert _names(intersected) == _names(in_scope)
    expected_skills = set(checks.allowed_skills(task).values_list("name", flat=True))
    expected_connectors = set(checks.allowed_connectors(task).values_list("name", flat=True))
    assert _names(intersected) == (expected_skills, expected_connectors)
    assert expected_skills and expected_connectors  # 空同士の一致にしない
    # 操作のたびの判定も同じ
    for skill in checks.allowed_skills(task):
        assert checks.launcher_skill_problem(launcher, skill) == ""
    for connector in checks.allowed_connectors(task):
        assert checks.launcher_connector_permitted(launcher, connector)


def test_the_intersection_takes_away_what_the_launcher_cannot_use():
    """共通部分を実際に取っていること：起動できない立場の人を渡せば、タスクの範囲より狭くなる"""
    outsider = UserFactory.create()
    department = _scope_world(outsider)
    manager = grant(UserFactory.create(), AppPermission.MANAGE_DEPARTMENT_TASKS)
    task = TaskDefinitionFactory.create(scope=TaskScope.DEPARTMENT, department=department, created_by=manager)
    assert not TaskDefinition.objects.launchable_by(outsider).filter(pk=task.pk).exists()
    version = ModelProfileFactory.create(version__supports_tools=True).current_version

    skills, connectors = _names(build_task_catalogs(task, version, sensitive=False, explicit=[], launcher=outsider))

    assert skills == {"全社のSkill"}
    assert connectors == {"全社のConnector"}


def test_what_the_catalog_omits_is_still_usable_in_the_scope_of_the_task(monkeypatch):
    """一覧の量の上限はアクセス範囲ではない。範囲の外のものは、名前を指定しても届かない"""
    monkeypatch.setattr(connectors_module, "MAX_CATALOG_CONNECTORS", 0)
    monkeypatch.setattr(skills_module, "CATALOG_CONTEXT_RATIO", 0)
    department = DepartmentFactory.create()
    manager = grant(UserFactory.create(), AppPermission.MANAGE_DEPARTMENT_TASKS)
    DepartmentMembershipFactory.create(department=department, user=manager)
    _scope_world_in(department)
    task = TaskDefinitionFactory.create(
        scope=TaskScope.DEPARTMENT,
        department=department,
        created_by=manager,
        revision__model_profile=ModelProfileFactory.create(version__supports_tools=True),
    )
    run = _start(task, manager)
    adapter = FakeAdapter(
        [
            calls(("c1", "datasource_describe", {"connector": "全社のConnector"})),
            calls(("c2", "datasource_describe", {"connector": "他部門のConnector"})),
            calls(("c3", "skill", {"name": "全社のSkill"})),
            calls(("c4", "skill", {"name": "他部門のSkill"})),
            reply("確認しました"),
        ]
    )
    run = execute(run, adapter)
    inside, outside = results(run, "datasource_describe")
    assert not inside["is_error"], text_of(inside)
    assert outside["is_error"]
    loaded, refused = results(run, "skill")
    assert not loaded["is_error"], text_of(loaded)
    assert refused["is_error"]


def test_system_task_can_use_skills_and_connectors_of_every_scope(admin):
    department = DepartmentFactory.create()
    _scope_world_in(department)
    task = TaskDefinitionFactory.create(scope=TaskScope.SYSTEM, created_by=admin)
    version = ModelProfileFactory.create(version__supports_tools=True).current_version
    skills, connectors = _names(build_task_catalogs(task, version, sensitive=False, explicit=[]))
    assert {"全社のSkill", "他部門のSkill"} <= skills
    assert {"全社のConnector", "他部門のConnector"} <= connectors


def test_declarations_of_a_skill_a_session_can_choose_are_usable_in_that_session():
    """Skillの範囲の全員に見えるConnectorだけを宣言できるので、選べるSkillの宣言はそのセッションで使える
    （authorization.md §5.1）。すべてのタスクの範囲と、Skill・Connectorの組合せで確かめる"""
    owner = UserFactory.create()
    department = _scope_world(owner)
    DepartmentMembershipFactory.create(department=department, user=owner)
    owner = type(owner).objects.get(pk=owner.pk)
    manager = grant(UserFactory.create(), AppPermission.MANAGE_DEPARTMENT_TASKS)
    tasks_by_scope = [
        TaskDefinitionFactory.create(scope=TaskScope.PERSONAL, created_by=owner),
        TaskDefinitionFactory.create(scope=TaskScope.DEPARTMENT, department=department, created_by=manager),
        TaskDefinitionFactory.create(scope=TaskScope.COMPANY, created_by=manager),
        TaskDefinitionFactory.create(scope=TaskScope.SYSTEM, created_by=manager),
    ]
    every_connector = list(Connector.objects.select_related("connection", "department", "current_version"))
    checked = 0
    for task in tasks_by_scope:
        usable = set(checks.allowed_connectors(task).values_list("pk", flat=True))
        for skill in checks.allowed_skills(task).select_related("department", "owner"):
            for connector in every_connector:
                declarable = skill_connectors.scope_allows(skill, connector) and (
                    skill.scope != SkillScope.PERSONAL or connector.can(skill.owner, ConnectorAction.USE)
                )
                if declarable:
                    assert connector.pk in usable, (task.scope, skill.name, connector.name)
                    checked += 1
    assert checked  # 空の確かめにしない
