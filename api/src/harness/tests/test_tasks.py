"""タスクのRun（harness.md §7.5・§10、authorization.md §5.4・§6）。固定したSkill・Connector・Toolだけを使い、
起動した人ではなくタスクの範囲で判定し、タスクの停止・Skillの停止で止まる"""

import pytest

from connector.factories import ConnectorFactory
from connector.models import ConnectorScope
from harness.tests.test_connectors import TABLES, execute, fixture_fake_datasource, results, text_of
from llm.factories import ModelProfileFactory
from run.models import Run, RunEndReason, RunEvent, RunEventKind, RunLaunch, RunStatus
from skill.factories import SkillFactory, SkillVersionFactory
from skill.models import SkillScope
from task.dispatch import start_task_run
from task.errors import CannotRun
from task.factories import TaskDefinitionFactory
from task.models import TaskScope
from user.factories import DepartmentFactory, UserFactory, grant
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


def test_task_run_uses_only_the_fixed_skill_tools_and_connectors(admin):
    connector = ConnectorFactory.create(
        name="経理DB", scope=ConnectorScope.DEPARTMENT, department=DepartmentFactory.create(), version__tables=TABLES
    )
    ConnectorFactory.create(name="販売管理")  # 全社のConnectorでも、固定していなければ使わない
    skill = _system_skill(tools=["read", "shell", "datasource_query"], output_schema=SCHEMA)
    task = TaskDefinitionFactory.create(
        scope=TaskScope.SYSTEM,
        created_by=admin,
        revision__skills=[{"skill": skill.uid, "version": 1}],
        revision__connectors=[connector.uid],
        revision__tools=["read", "datasource_query", "todo_write"],
        revision__model_profile=ModelProfileFactory.create(version__supports_tools=True),
    )
    run = _start(task, admin)
    adapter = FakeAdapter(
        [
            calls(("c1", "datasource_query", {"connector": "経理DB", "sql": "SELECT code FROM vendors"})),
            calls(("c2", "submit_result", {"ok": True})),
            reply("確認しました"),
        ]
    )
    run = execute(run, adapter)

    assert run.status == RunStatus.COMPLETED, run.error
    assert run.result == {"ok": True}
    first = adapter.requests[0]
    # Skillとタスクの両方に入っているToolだけ。skill・ask_user・request_skill_edit は出さない
    assert sorted(t.name for t in first.tools) == ["datasource_query", "read", "submit_result"]
    query_tool = next(t for t in first.tools if t.name == "datasource_query")
    assert query_tool.parameters["properties"]["connector"]["enum"] == ["経理DB"]
    context = next(m.text for m in first.messages if m.role == "user" and "<runtime_context>" in m.text)
    assert "<mode>task</mode>" in context
    # 起動した人は部門に所属していないが、システムのタスクの範囲で部門のConnectorを使える
    [queried] = results(run, "datasource_query")
    assert not queried["is_error"], text_of(queried)


def test_connector_disabled_during_the_task_run_is_refused(admin):
    connector = ConnectorFactory.create(name="経理DB", scope=ConnectorScope.SYSTEM, version__tables=TABLES)
    skill = _system_skill(tools=["datasource_query"])
    task = TaskDefinitionFactory.create(
        scope=TaskScope.SYSTEM,
        created_by=admin,
        revision__skills=[{"skill": skill.uid, "version": 1}],
        revision__connectors=[connector.uid],
        revision__tools=["datasource_query"],
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


def test_suspended_task_stops_the_queued_run(admin):
    task = TaskDefinitionFactory.create(scope=TaskScope.SYSTEM, created_by=admin)
    run = _start(task, admin)
    task.set_suspended(True)
    adapter = FakeAdapter([])
    run = execute(run, adapter)
    assert (run.status, run.end_reason) == (RunStatus.FAILED, RunEndReason.TASK)
    assert "タスクが停止されました" in run.error
    assert adapter.requests == []


def test_suspended_skill_stops_the_task_run(admin):
    skill = _system_skill()
    task = TaskDefinitionFactory.create(
        scope=TaskScope.SYSTEM, created_by=admin, revision__skills=[{"skill": skill.uid, "version": 1}]
    )
    run = _start(task, admin)
    skill.set_suspended(True, actor=admin)
    run = execute(run, FakeAdapter([]))
    assert (run.status, run.end_reason) == (RunStatus.FAILED, RunEndReason.SKILL)


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
        revision__tools=["datasource_query"],
    )
    run = _start(task, admin)
    call = {"id": "c1", "name": "datasource_query", "arguments": {"connector": "経理DB", "sql": "SELECT 1"}}
    RunEvent.record(run, RunEventKind.ASSISTANT_MESSAGE, {"step": 1, "text": "", "tool_calls": [call]})
    task.set_suspended(True)
    run = execute(run, FakeAdapter([]))
    assert fake_datasource.calls == []
    [result] = results(run, "datasource_query")
    assert "interrupted" in text_of(result)  # やり直さず、結果の分からない呼出しとして閉じた
    assert (run.status, run.end_reason) == (RunStatus.FAILED, RunEndReason.TASK)
