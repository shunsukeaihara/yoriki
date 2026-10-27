"""タスクの定義のAPI（authorization.md §5.3〜§5.4）。範囲ごとの管理、登録できるもの、版、手動実行、Runの見え方"""

import pytest

from artifact.factories import ArtifactFactory
from artifact.models import ArtifactKind
from artifact.tests.test_api import body_of
from connector.factories import ConnectorFactory
from connector.models import ConnectorScope
from conversation.models import Conversation, ConversationPurpose
from llm.factories import LLMConnectionFactory, ModelProfileFactory
from llm.models import ProviderKind
from run.models import Run, RunEvent, RunEventKind, RunLaunch, RunStatus, RunTrigger
from skill.factories import SkillFactory, SkillVersionFactory
from skill.models import SkillScope
from task.factories import TaskDefinitionFactory
from task.models import TaskAction, TaskDefinition, TaskDefinitionRevision, TaskScope
from user.factories import DepartmentFactory, DepartmentMembershipFactory, UserFactory, grant
from utils.permissions import AppPermission

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("default_runtime")]

TASKS = "/api/app/v1/tasks/"
TABLES = [{"schema": "public", "name": "vendors", "description": "取引先"}]


@pytest.fixture(name="model")
def fixture_model():
    return ModelProfileFactory.create(version__supports_tools=True)


def _url(task: TaskDefinition | dict, rest: str = "") -> str:
    uid = task["id"] if isinstance(task, dict) else task.uid
    return f"{TASKS}{uid}/{rest}"


def _department_manager(user, department):
    DepartmentMembershipFactory.create(department=department, user=user)
    return grant(user, AppPermission.MANAGE_DEPARTMENT_TASKS)


def _company_skill(name="請求書の確認", **version):
    skill = SkillFactory.create(scope=SkillScope.COMPANY, name=name, owner=None)
    SkillVersionFactory.create(skill=skill, tools=version.pop("tools", ["read", "datasource_query"]), **version)
    return skill


def _create(api_client, user, model, **data):
    api_client.force_login(user)
    body = {
        "scope": TaskScope.PERSONAL,
        "name": "請求書チェック",
        "prompt_template": "{{ inputs.vendor }} の請求書を確認してください。",
        "model_profile": model.uid,
        "inputs": [{"name": "vendor", "label": "取引先", "type": "string", "required": True}],
        **data,
    }
    return api_client.post(TASKS, body, format="json")


# --- 管理できる人 ---


def test_personal_task_is_managed_only_by_its_owner(api_client, app_user, other_user, model):
    res = _create(api_client, app_user, model)
    assert res.status_code == 201, res.json()
    task = res.json()
    assert task["version"] == 1
    assert task["owner"]["id"] == app_user.uid
    assert task["inputs"][0] == {
        "name": "vendor",
        "label": "取引先",
        "type": "string",
        "required": True,
        "description": "",
        "choices": [],
        "max_length": None,
    }
    assert [t["id"] for t in api_client.get(TASKS).json()["results"]] == [task["id"]]

    api_client.force_login(other_user)
    assert api_client.get(TASKS).json()["results"] == []
    assert api_client.get(_url(task)).status_code == 404


def test_department_task_needs_the_permission_and_a_manageable_department(api_client, app_user, model):
    department = DepartmentFactory.create()
    DepartmentMembershipFactory.create(department=department, user=app_user)
    body = {"scope": TaskScope.DEPARTMENT, "department": department.uid}
    assert _create(api_client, app_user, model, **body).status_code == 403  # permissionがない

    outsider = grant(UserFactory.create(), AppPermission.MANAGE_DEPARTMENT_TASKS)  # 所属していない
    assert _create(api_client, outsider, model, **body).status_code == 403

    manager = grant(app_user, AppPermission.MANAGE_DEPARTMENT_TASKS)
    res = _create(api_client, manager, model, **body)
    assert res.status_code == 201, res.json()
    assert res.json()["department_info"]["name"] == department.name

    # 同じ部門の別の管理者も直せる（1人の担当者に依存しない）。所属だけの人には見せない
    colleague = _department_manager(UserFactory.create(), department)
    api_client.force_login(colleague)
    assert api_client.get(_url(res.json())).status_code == 200
    member = UserFactory.create()
    DepartmentMembershipFactory.create(department=department, user=member)
    api_client.force_login(member)
    assert api_client.get(_url(res.json())).status_code == 404


def test_system_tasks_are_listed_only_when_asked(api_client, model):
    admin = grant(UserFactory.create(), AppPermission.MANAGE_SYSTEM_TASKS, AppPermission.MANAGE_COMPANY_TASKS)
    TaskDefinitionFactory.create(scope=TaskScope.SYSTEM, name="突合", created_by=admin)
    TaskDefinitionFactory.create(scope=TaskScope.COMPANY, name="要約", created_by=admin)
    api_client.force_login(admin)
    assert [t["name"] for t in api_client.get(TASKS).json()["results"]] == ["要約"]
    assert [t["name"] for t in api_client.get(TASKS, {"scope": TaskScope.SYSTEM}).json()["results"]] == ["突合"]


def test_list_and_one_task_agree(app_user):
    """一覧（QuerySet）と1件の判定が同じ結果になる（authorization.md §7.2）"""
    department, other = DepartmentFactory.create(), DepartmentFactory.create()
    manager = _department_manager(app_user, department)
    owner = UserFactory.create()
    tasks = [
        TaskDefinitionFactory.create(created_by=manager),
        TaskDefinitionFactory.create(created_by=owner),
        TaskDefinitionFactory.create(scope=TaskScope.DEPARTMENT, department=department, created_by=owner),
        TaskDefinitionFactory.create(scope=TaskScope.DEPARTMENT, department=other, created_by=owner),
        TaskDefinitionFactory.create(scope=TaskScope.COMPANY, created_by=owner),
        TaskDefinitionFactory.create(scope=TaskScope.SYSTEM, created_by=owner),
    ]
    listed = set(TaskDefinition.objects.manageable_by(manager).values_list("pk", flat=True))
    assert listed == {t.pk for t in tasks if t.can(manager, TaskAction.MANAGE)}
    assert listed == {tasks[0].pk, tasks[2].pk}


# --- 登録できるもの ---


def test_department_task_uses_its_department_and_company_skills(api_client, app_user, model):
    department, other = DepartmentFactory.create(), DepartmentFactory.create()
    manager = _department_manager(app_user, department)
    own = SkillFactory.create(scope=SkillScope.DEPARTMENT, department=department, owner=None)
    SkillVersionFactory.create(skill=own)
    foreign = SkillFactory.create(scope=SkillScope.DEPARTMENT, department=other, owner=None)
    SkillVersionFactory.create(skill=foreign)
    company = _company_skill()
    personal = SkillFactory.create(created_by=manager)
    SkillVersionFactory.create(skill=personal)
    body = {"scope": TaskScope.DEPARTMENT, "department": department.uid}

    ok = [{"skill": own.uid, "version": 1}, {"skill": company.uid, "version": 1}]
    assert _create(api_client, manager, model, skills=ok, **body).status_code == 201
    for skill in (foreign, personal):
        res = _create(api_client, manager, model, name=skill.name, skills=[{"skill": skill.uid, "version": 1}], **body)
        assert res.status_code == 400
        assert "このタスクでは使えません" in res.json()["skills"][0]


def test_system_task_uses_department_connectors_of_any_department(api_client, model):
    admin = grant(UserFactory.create(), AppPermission.MANAGE_SYSTEM_TASKS)
    connector = ConnectorFactory.create(scope=ConnectorScope.DEPARTMENT, department=DepartmentFactory.create())
    skill = SkillFactory.create(scope=SkillScope.SYSTEM, owner=None)
    SkillVersionFactory.create(skill=skill, tools=["datasource_query"])
    res = _create(
        api_client,
        admin,
        model,
        scope=TaskScope.SYSTEM,
        skills=[{"skill": skill.uid, "version": 1}],
        connectors=[connector.uid],
        tools=["datasource_query"],
    )
    assert res.status_code == 201, res.json()

    # 全社のタスクには部門のConnectorを登録できない
    company = grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)
    res = _create(
        api_client,
        company,
        model,
        scope=TaskScope.COMPANY,
        skills=[{"skill": _company_skill().uid, "version": 1}],
        connectors=[connector.uid],
        tools=["datasource_query"],
    )
    assert res.status_code == 400
    assert "このタスクでは使えません" in res.json()["connectors"][0]


def test_skill_declared_connector_must_be_in_the_task(api_client, model):
    admin = grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)
    connector = ConnectorFactory.create(name="販売管理", version__tables=TABLES)
    declaration = {"connector": connector.uid, "operations": ["datasource_query"], "tables": ["public.vendors"]}
    skill = _company_skill(connectors=[declaration])
    body = {"scope": TaskScope.COMPANY, "skills": [{"skill": skill.uid, "version": 1}]}

    res = _create(api_client, admin, model, **body)
    assert res.status_code == 400
    assert "販売管理" in res.json()["connectors"][0]
    assert "datasource_query" in res.json()["tools"][0]
    res = _create(api_client, admin, model, connectors=[connector.uid], tools=["read", "datasource_query"], **body)
    assert res.status_code == 201, res.json()


def test_sensitive_connector_needs_a_sensitive_task_with_a_local_model(api_client, model):
    admin = grant(UserFactory.create(), AppPermission.MANAGE_COMPANY_TASKS)
    connector = ConnectorFactory.create(version__is_sensitive=True)
    body = {
        "scope": TaskScope.COMPANY,
        "skills": [{"skill": _company_skill().uid, "version": 1}],
        "connectors": [connector.uid],
        "tools": ["datasource_query"],
    }
    res = _create(api_client, admin, model, **body)
    assert res.status_code == 400
    assert "機密" in res.json()["connectors"][0]
    res = _create(api_client, admin, model, handles_sensitive_data=True, **body)
    assert res.status_code == 400
    assert "ローカルLLM" in res.json()["model_profile"][0]
    local = ModelProfileFactory.create(
        version__connection=LLMConnectionFactory.create(kind=ProviderKind.LOCAL), version__supports_tools=True
    )
    assert _create(api_client, admin, local, handles_sensitive_data=True, **body).status_code == 201


def test_template_uses_only_defined_inputs(api_client, app_user, model):
    res = _create(api_client, app_user, model, prompt_template="{{ inputs.amount }}円の請求書")
    assert res.status_code == 400
    assert "amount" in res.json()["prompt_template"][0]


# --- 版 ---


def test_saving_makes_a_new_revision_and_conflicts_are_detected(api_client, app_user, model):
    task = _create(api_client, app_user, model).json()
    res = api_client.patch(_url(task), {"version": 1, "description": "経理部の請求書"}, format="json")
    assert res.status_code == 200, res.json()
    assert res.json()["version"] == 2
    assert res.json()["prompt_template"] == task["prompt_template"]  # 送らなかった項目は今の版のまま
    # 同じ中身なら版を作らない。名前だけの変更も版を作らない
    res = api_client.patch(_url(task), {"version": 2, "description": "経理部の請求書", "name": "請求書"}, format="json")
    assert (res.json()["version"], res.json()["name"]) == (2, "請求書")
    assert api_client.patch(_url(task), {"version": 1, "description": "古い"}, format="json").status_code == 409
    assert api_client.patch(_url(task), {"description": "版なし"}, format="json").status_code == 400

    revisions = api_client.get(_url(task, "revisions/")).json()["results"]
    assert [r["number"] for r in revisions] == [2, 1]
    assert revisions[1]["description"] == ""


# --- 手動実行 ---


def test_manual_run_fixes_the_revision_and_renders_the_inputs(api_client, app_user, model):
    skill = _company_skill(output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}})
    task = _create(api_client, app_user, model, skills=[{"skill": skill.uid, "version": 1}], tools=["read"]).json()
    SkillVersionFactory.create(skill=skill, instructions="新しい手順")  # 公開しても、タスクは版1のまま

    res = api_client.post(_url(task, "run/"), {"inputs": {"vendor": "A商事"}}, format="json")
    assert res.status_code == 201, res.json()
    run = Run.objects.get(uid=res.json()["id"])
    assert (run.trigger, run.launch, run.owner) == (RunTrigger.TASK, RunLaunch.MANUAL, app_user)
    assert run.task_revision == TaskDefinitionRevision.objects.get(task__uid=task["id"], number=1)
    assert run.task_scope == TaskScope.PERSONAL
    assert run.parent is None
    fixed = run.manifest["fixed"]
    assert [(s["skill"], s["number"]) for s in fixed["skills"]] == [(skill.uid, 1)]
    assert fixed["task"]["tools"] == ["read"]
    assert fixed["task"]["output_schema"]["properties"] == {"ok": {"type": "boolean"}}
    assert run.conversation.purpose == ConversationPurpose.TASK
    assert not Conversation.objects.owned_by(app_user).exists()  # 会話の一覧には出さない
    first = RunEvent.objects.get(run=run, kind=RunEventKind.USER_MESSAGE).data
    assert first["text"].startswith("A商事 の請求書を確認してください。")
    assert '<inputs>{"vendor": "A商事"}</inputs>' in first["text"]
    assert "1. 請求書を読む" in first["skills_text"]


def test_manual_run_accepts_only_the_defined_inputs(api_client, app_user, model):
    task = _create(api_client, app_user, model).json()
    res = api_client.post(_url(task, "run/"), {"inputs": {"memo": "これも見て"}}, format="json")
    assert res.status_code == 400
    assert set(res.json()["inputs"]) == {"vendor", "memo"}  # 必須がない、定義にない
    assert not Run.objects.exists()


def test_suspended_task_cannot_be_run(api_client, app_user, model):
    task = _create(api_client, app_user, model).json()
    assert api_client.post(_url(task, "suspend/")).json()["suspended"] is True
    res = api_client.post(_url(task, "run/"), {"inputs": {"vendor": "A商事"}}, format="json")
    assert res.status_code == 400
    assert api_client.post(_url(task, "resume/")).json()["can_run"] is True


def test_runs_are_seen_by_the_managers_of_the_recorded_scope(api_client, app_user, model):
    department = DepartmentFactory.create()
    manager = _department_manager(app_user, department)
    task = _create(api_client, manager, model, scope=TaskScope.DEPARTMENT, department=department.uid).json()
    run_id = api_client.post(_url(task, "run/"), {"inputs": {"vendor": "A商事"}}, format="json").json()["id"]

    colleague = _department_manager(UserFactory.create(), department)
    api_client.force_login(colleague)
    assert [r["id"] for r in api_client.get(_url(task, "runs/")).json()["results"]] == [run_id]
    assert api_client.get(f"/api/app/v1/runs/{run_id}/").status_code == 200
    assert api_client.get(f"/api/app/v1/runs/{run_id}/events/").status_code == 200
    # 管理者は取り消せる
    assert api_client.post(f"/api/app/v1/runs/{run_id}/cancel/", {"reason": ""}, format="json").status_code == 200
    assert Run.objects.get(uid=run_id).status == RunStatus.CANCELLED

    member = UserFactory.create()
    DepartmentMembershipFactory.create(department=department, user=member)
    api_client.force_login(member)
    assert api_client.get(f"/api/app/v1/runs/{run_id}/").status_code == 404
    assert api_client.get(f"/api/app/v1/runs/{run_id}/events/").status_code == 404


def test_outputs_of_a_task_run_are_downloaded_by_the_managers(api_client, app_user, model):
    department = DepartmentFactory.create()
    manager = _department_manager(app_user, department)
    task = _create(api_client, manager, model, scope=TaskScope.DEPARTMENT, department=department.uid).json()
    run = Run.objects.get(
        uid=api_client.post(_url(task, "run/"), {"inputs": {"vendor": "A"}}, format="json").json()["id"]
    )
    output = ArtifactFactory.create(
        conversation=run.conversation, run=run, kind=ArtifactKind.OUTPUT, name="結果.csv", content=b"ok\n"
    )
    artifacts = f"/api/app/v1/runs/{run.uid}/artifacts/"

    colleague = _department_manager(UserFactory.create(), department)
    api_client.force_login(colleague)
    assert [a["id"] for a in api_client.get(artifacts).json()["results"]] == [output.uid]
    res = api_client.get(f"{artifacts}{output.uid}/download/")
    assert res.status_code == 200
    assert body_of(res) == b"ok\n"

    # 管理の権限を外せば、起動した人でも取得できない（取得のたびに判定し直す）
    manager.user_permissions.clear()
    api_client.force_login(UserFactory._meta.model.objects.get(pk=manager.pk))
    assert api_client.get(f"{artifacts}{output.uid}/download/").status_code == 404


def test_number_input_rejects_infinity_and_huge_numbers(api_client, app_user, model):
    inputs = [{"name": "amount", "label": "金額", "type": "number", "required": True}]
    task = _create(api_client, app_user, model, prompt_template="{{ inputs.amount }}円", inputs=inputs).json()
    for value in ("1e999", "1" + "0" * 400):
        body = f'{{"inputs": {{"amount": {value}}}}}'
        res = api_client.post(_url(task, "run/"), body, content_type="application/json")
        assert res.status_code == 400
        assert "amount" in res.json()["inputs"]


def test_result_schema_needs_a_model_with_tools(api_client, app_user):
    model = ModelProfileFactory.create(version__supports_tools=False)
    res = _create(api_client, app_user, model, output_schema={"type": "object", "properties": {}})
    assert res.status_code == 400
    assert "Toolを使えるモデル" in res.json()["model_profile"][0]


def test_tools_that_no_skill_allows_are_refused(api_client, app_user, model):
    """実行時はSkillとタスクの両方にあるToolだけを出すので、どのSkillも使わないToolは保存させない"""
    skill = _company_skill(tools=["read"])
    res = _create(api_client, app_user, model, skills=[{"skill": skill.uid, "version": 1}], tools=["read", "shell"])
    assert res.status_code == 400
    assert "shell" in res.json()["tools"][0]
