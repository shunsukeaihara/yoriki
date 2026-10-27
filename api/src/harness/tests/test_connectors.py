"""会話でConnectorを使う（harness.md §10.2）。一覧の固定、`datasource_describe`・`datasource_query`、`@` での指定、
機密のConnector、権限の確かめ直し、結果のファイル"""

from datetime import timedelta

import pytest
from django.utils import timezone

from artifact.factories import ArtifactFactory
from artifact.models import Artifact, ArtifactKind
from connector.adapters.base import ColumnInfo, QueryResult, TableInfo
from connector.errors import ConnectorError, ConnectorErrorKind
from connector.factories import ConnectorFactory
from connector.models import VERSION_FIELDS, ConnectionLease, ConnectorScope
from conversation.dispatch import CannotStart, MessageMode, post_message
from conversation.factories import ConversationFactory
from conversation.models import InstructionKind, UserInstruction
from harness.connectors import build_catalog
from harness.recovery import switch_candidates
from harness.runner import Runner
from harness.sandbox import SandboxSession
from harness.tools import datasource as datasource_module
from llm.factories import LLMConnectionFactory, ModelProfileFactory
from llm.models import ProviderKind
from run.models import Run, RunEvent, RunEventKind, RunStatus, ToolInvocation
from run.tasks import claim_run
from skill.factories import SkillFactory, SkillVersionFactory
from user.factories import DepartmentFactory, DepartmentMembershipFactory

from .fakes import FakeAdapter, calls, reply

pytestmark = pytest.mark.usefixtures("default_runtime")

TABLES = [{"schema": "public", "name": "vendors", "description": "取引先", "columns": {"code": "取引先コード"}}]


class FakeDatasource:
    """接続先につながないAdapter。describe・query の呼出しを記録する"""

    calls: list[tuple] = []
    error: ConnectorError | None = None

    def __init__(self, connector, version):
        self.connector = connector
        self.version = version

    def describe(self):
        FakeDatasource.calls.append(("describe", self.version.number))
        return [
            TableInfo(
                "public",
                "vendors",
                "view",
                (ColumnInfo("code", "text", "取引先コード"), ColumnInfo("name", "text")),
                "取引先",
            )
        ]

    def query(self, sql, params, *, keep_rows=None, csv_out=None):
        FakeDatasource.calls.append(("query", sql, tuple(params), keep_rows))
        if FakeDatasource.error is not None:
            raise FakeDatasource.error
        rows = [(f"{i:03d}", f"取引先{i}") for i in range(1, 31)]
        csv_out.write("code,name\n")
        for row in rows:
            csv_out.write(",".join(row) + "\n")
        kept = rows if keep_rows is None else rows[:keep_rows]
        return QueryResult(
            columns=["code", "name"],
            rows=kept,
            row_count=len(rows),
            csv_bytes=400,
            elapsed_ms=12,
            tables=("public.vendors",),
            sql='SELECT code, name FROM "public"."vendors" WHERE name LIKE ?',
        )


@pytest.fixture(autouse=True, name="fake_datasource")
def fixture_fake_datasource(monkeypatch):
    FakeDatasource.calls = []
    FakeDatasource.error = None
    monkeypatch.setattr(datasource_module, "get_adapter", FakeDatasource)
    return FakeDatasource


@pytest.fixture(name="local_model")
def fixture_local_model(db):
    return ModelProfileFactory.create(
        name="社内LLM",
        version__connection=LLMConnectionFactory.create(kind=ProviderKind.LOCAL),
        version__supports_tools=True,
    )


@pytest.fixture(name="conversation")
def fixture_conversation(app_user):
    return ConversationFactory.create(owner=app_user)


def start(conversation, body="取引先を調べて", **kwargs) -> Run:
    post_message(
        conversation, body=body, client_message_id="", mode=MessageMode.AUTO, actor=conversation.owner, **kwargs
    )
    return Run.objects.filter(conversation=conversation).latest("pk")


def execute(run: Run, adapter: FakeAdapter) -> Run:
    attempt = claim_run(run.pk)
    assert attempt is not None
    Runner(attempt, adapter_factory=adapter, sleep=lambda _s: None).run()
    run.refresh_from_db()
    return run


def results(run: Run, name: str) -> list[dict]:
    return [
        e.data
        for e in RunEvent.objects.filter(run=run, kind=RunEventKind.TOOL_RESULT).order_by("sequence")
        if e.data["name"] == name
    ]


def text_of(result: dict) -> str:
    return result["content"][0]["text"]


@pytest.mark.django_db
def test_agent_sees_usable_connectors_and_reads_with_them(conversation, app_user):
    department = DepartmentFactory.create()
    DepartmentMembershipFactory.create(department=department, user=app_user)
    sales = ConnectorFactory.create(name="販売管理", version__description="受注と取引先", version__tables=TABLES)
    ConnectorFactory.create(name="経理", scope=ConnectorScope.DEPARTMENT, department=department)
    ConnectorFactory.create(name="他部門", scope=ConnectorScope.DEPARTMENT, department=DepartmentFactory.create())
    ConnectorFactory.create(name="システム", scope=ConnectorScope.SYSTEM)
    ConnectorFactory.create(name="停止中", is_active=False)
    ConnectorFactory.create(name="機密", version__is_sensitive=True)  # クラウドのモデルの会話には出さない
    run = start(conversation)
    entries = run.manifest["fixed"]["connector_catalog"]["connectors"]
    assert [e["name"] for e in entries] == ["経理", "販売管理"]
    adapter = FakeAdapter(
        [
            calls(("c1", "datasource_describe", {"connector": "販売管理"})),
            calls(
                (
                    "c2",
                    "datasource_query",
                    {
                        "connector": "販売管理",
                        "sql": "SELECT code, name FROM vendors WHERE name LIKE ?",
                        "params": ["%A%"],
                    },
                )
            ),
            reply("調べました"),
        ]
    )
    run = execute(run, adapter)

    assert run.status == RunStatus.COMPLETED
    first = adapter.requests[0]
    query_tool = next(t for t in first.tools if t.name == "datasource_query")
    assert query_tool.parameters["properties"]["connector"]["enum"] == ["経理", "販売管理"]
    context = next(m.text for m in first.messages if m.role == "user" and "<runtime_context>" in m.text)
    assert '<connector name="販売管理">受注と取引先</connector>' in context
    assert "datasource_describe" in first.messages[0].text  # system promptの使い方

    [described] = results(run, "datasource_describe")
    assert "- code (text): 取引先コード" in text_of(described)
    assert described["value"] == {"connector": sales.uid, "version": 1, "tables": 1}

    [queried] = results(run, "datasource_query")
    invocation = ToolInvocation.objects.get(run=run, call_id="c2")
    artifact = Artifact.objects.get(run=run, kind=ArtifactKind.CONNECTOR_RESULT)
    assert artifact.path == f"connectors/{invocation.uid}/result.csv"
    assert artifact.name == "販売管理.csv"
    with artifact.blob.open() as f:
        content = f.read().decode()
    assert content.startswith("code,name\n001,取引先1\n") and content.count("\n") == 31
    text = text_of(queried)
    assert "rows: 30" in text
    assert f"file: /workspace/input/connectors/{invocation.uid}/result.csv" in text
    assert "first 10 rows (CSV):" in text and "010,取引先10" in text and "011," not in text
    value = invocation.value or {}
    assert value["version"] == 1
    assert value["rows"] == 30
    assert value["sql"].startswith("SELECT code, name")
    assert value["artifact"] == artifact.uid
    assert ("query", "SELECT code, name FROM vendors WHERE name LIKE ?", ("%A%",), 10) in FakeDatasource.calls


@pytest.mark.django_db
def test_no_connector_tools_without_usable_connectors(conversation):
    run = start(conversation)
    assert run.manifest["fixed"]["connector_catalog"] is None
    adapter = FakeAdapter([reply("できません")])
    execute(run, adapter)
    names = [t.name for t in adapter.requests[0].tools]
    assert "datasource_query" not in names and "datasource_describe" not in names


@pytest.mark.django_db
def test_connector_disabled_during_the_run_is_refused(conversation):
    connector = ConnectorFactory.create(name="販売管理", version__tables=TABLES)
    run = start(conversation)
    connector.is_active = False
    connector.save()
    adapter = FakeAdapter([calls(("c1", "datasource_describe", {"connector": "販売管理"})), reply("止めました")])
    run = execute(run, adapter)

    [result] = results(run, "datasource_describe")
    assert result["is_error"]
    assert "cannot be used now" in text_of(result)
    assert FakeDatasource.calls == []


@pytest.mark.django_db
def test_department_membership_removed_during_the_run_is_refused(conversation, app_user):
    department = DepartmentFactory.create()
    membership = DepartmentMembershipFactory.create(department=department, user=app_user)
    ConnectorFactory.create(name="経理", scope=ConnectorScope.DEPARTMENT, department=department, version__tables=TABLES)
    run = start(conversation)
    membership.delete()
    adapter = FakeAdapter(
        [calls(("c1", "datasource_query", {"connector": "経理", "sql": "SELECT 1 FROM vendors"})), reply("止めました")]
    )
    run = execute(run, adapter)

    [result] = results(run, "datasource_query")
    assert result["is_error"]
    assert not Artifact.objects.filter(run=run).exists()


@pytest.mark.django_db
def test_boundary_changes_apply_from_the_next_operation(conversation):
    connector = ConnectorFactory.create(name="販売管理", version__tables=TABLES)
    run = start(conversation)
    connector.revise(actor=connector.created_by, expected_version=1, **{**_content(connector), "max_rows": 5})
    adapter = FakeAdapter(
        [calls(("c1", "datasource_query", {"connector": "販売管理", "sql": "SELECT code FROM vendors"})), reply("ok")]
    )
    run = execute(run, adapter)
    assert (ToolInvocation.objects.get(run=run, call_id="c1").value or {})["version"] == 2


def _content(connector) -> dict:
    return {name: getattr(connector.current_version, name) for name in VERSION_FIELDS}


@pytest.mark.django_db
def test_query_error_is_returned_to_the_agent(conversation, fake_datasource):
    ConnectorFactory.create(name="販売管理", version__tables=TABLES)
    fake_datasource.error = ConnectorError(ConnectorErrorKind.REJECTED, "公開していない表です：secrets")
    run = start(conversation)
    adapter = FakeAdapter(
        [
            calls(("c1", "datasource_query", {"connector": "販売管理", "sql": "SELECT * FROM secrets"})),
            reply("直します"),
        ]
    )
    run = execute(run, adapter)

    [result] = results(run, "datasource_query")
    assert result["is_error"]
    assert "(rejected): 公開していない表です" in text_of(result)
    assert not Artifact.objects.filter(run=run).exists()


@pytest.mark.django_db
def test_sensitive_connector_is_listed_only_for_local_models(app_user, local_model):
    ConnectorFactory.create(name="人事", version__is_sensitive=True)
    cloud = ModelProfileFactory.create(version__supports_tools=True)
    assert build_catalog(app_user, cloud.current_version) is None
    catalog = build_catalog(app_user, local_model.current_version)
    assert catalog is not None
    assert [e["name"] for e in catalog["connectors"]] == ["人事"]


@pytest.mark.django_db
def test_using_a_sensitive_connector_makes_the_conversation_confidential(app_user, local_model):
    connector = ConnectorFactory.create(name="人事", version__is_sensitive=True, version__tables=TABLES)
    ModelProfileFactory.create(name="クラウド", version__supports_tools=True)
    conversation = ConversationFactory.create(owner=app_user, model_profile=local_model)
    run = start(conversation)
    adapter = FakeAdapter(
        [calls(("c1", "datasource_query", {"connector": "人事", "sql": "SELECT code FROM vendors"})), reply("ok")]
    )
    run = execute(run, adapter)

    assert run.handles_sensitive_data
    conversation.refresh_from_db()
    assert conversation.handles_sensitive_data
    marks = [e for e in run.manifest["appended"] if e["kind"] == "sensitive_connector"]
    assert [m["connector"] for m in marks] == [connector.uid]
    # 以後は、切り替えの候補もローカルLLMのモデルだけ
    assert all(p.current_version and p.current_version.is_local for p in switch_candidates(run))


@pytest.mark.django_db
def test_sensitive_connector_is_refused_after_switching_to_a_cloud_model(app_user, local_model):
    ConnectorFactory.create(name="人事", version__is_sensitive=True, version__tables=TABLES)
    conversation = ConversationFactory.create(owner=app_user, model_profile=local_model)
    run = start(conversation)
    cloud = ModelProfileFactory.create(version__supports_tools=True)
    run.switch_model(cloud.current_version, "", trigger="user")
    adapter = FakeAdapter(
        [calls(("c1", "datasource_query", {"connector": "人事", "sql": "SELECT code FROM vendors"})), reply("ok")]
    )
    run = execute(run, adapter)

    [result] = results(run, "datasource_query")
    assert result["is_error"]
    assert "local model" in text_of(result)
    assert not run.handles_sensitive_data
    assert FakeDatasource.calls == []


@pytest.mark.django_db
def test_chosen_connector_is_described_in_the_first_message(conversation):
    connector = ConnectorFactory.create(name="販売管理", version__description="受注と取引先", version__tables=TABLES)
    run = start(conversation, connectors=[connector])

    assert run.manifest["fixed"]["connectors"] == [{"connector": connector.uid, "name": "販売管理"}]
    data = RunEvent.objects.get(run=run, kind=RunEventKind.USER_MESSAGE).data
    assert '<connector name="販売管理">' in data["connectors_text"]
    assert "public.vendors — 取引先" in data["connectors_text"]
    adapter = FakeAdapter([reply("ok")])
    execute(run, adapter)
    first_user = next(m.text for m in adapter.requests[0].messages if m.role == "user" and "取引先を調べて" in m.text)
    assert "public.vendors — 取引先" in first_user


@pytest.mark.django_db
def test_chosen_sensitive_connector_needs_a_local_model(conversation):
    connector = ConnectorFactory.create(name="人事", version__is_sensitive=True)
    with pytest.raises(CannotStart, match="ローカルLLM"):
        start(conversation, connectors=[connector])


@pytest.mark.django_db
def test_chosen_connector_that_was_disabled_does_not_start_the_run(conversation):
    connector = ConnectorFactory.create(name="販売管理")
    connector.is_active = False
    connector.save()
    with pytest.raises(CannotStart, match="販売管理"):
        start(conversation, connectors=[connector])


@pytest.mark.django_db
def test_chosen_connector_with_a_skill_that_does_not_query_is_refused(conversation, app_user):
    connector = ConnectorFactory.create(name="販売管理")
    skill = SkillFactory.create(created_by=app_user)
    SkillVersionFactory.create(skill=skill, tools=["read"])
    with pytest.raises(CannotStart, match="ConnectorのTool"):
        start(conversation, skills=[skill], connectors=[connector])


@pytest.mark.django_db
def test_message_with_a_connector_during_a_run_becomes_the_next_request(conversation):
    connector = ConnectorFactory.create(name="販売管理")
    run = start(conversation)
    start(conversation, body="こちらも", connectors=[connector])
    instruction = UserInstruction.objects.get(message__body="こちらも")
    assert instruction.kind == InstructionKind.NEXT
    assert Run.objects.filter(conversation=conversation).count() == 1
    assert run.status == RunStatus.QUEUED


@pytest.mark.django_db
def test_chosen_skill_limits_the_connector_tools(conversation, app_user):
    ConnectorFactory.create(name="販売管理", version__tables=TABLES)
    skill = SkillFactory.create(created_by=app_user)
    SkillVersionFactory.create(skill=skill, tools=["datasource_query"])
    run = start(conversation, skills=[skill])
    adapter = FakeAdapter(
        [calls(("c1", "datasource_query", {"connector": "販売管理", "sql": "SELECT code FROM vendors"})), reply("ok")]
    )
    run = execute(run, adapter)

    names = [t.name for t in adapter.requests[0].tools]
    assert "datasource_query" in names and "datasource_describe" not in names and "read" not in names
    # ファイルを読めないRunでは、上限の中の結果を表として見せる
    [result] = results(run, "datasource_query")
    text = text_of(result)
    assert "rows (CSV):" in text and "030,取引先30" in text
    assert "file:" not in text
    assert ("query", "SELECT code FROM vendors", (), datasource_module.INLINE_ROWS) in FakeDatasource.calls


@pytest.mark.django_db
def test_connector_results_are_placed_under_input(conversation, settings, tmp_path):
    settings.YORIKI_WORKSPACES_DIR = str(tmp_path / "workspaces")
    ConnectorFactory.create(name="販売管理", version__tables=TABLES)
    run = start(conversation)
    adapter = FakeAdapter(
        [calls(("c1", "datasource_query", {"connector": "販売管理", "sql": "SELECT code FROM vendors"})), reply("ok")]
    )
    run = execute(run, adapter)
    artifact = Artifact.objects.get(run=run, kind=ArtifactKind.CONNECTOR_RESULT)

    # 同じ会話の次の依頼でも /workspace/input/connectors/ にある
    following = start(conversation, body="続き")
    attempt = claim_run(following.pk)
    assert attempt is not None
    session = SandboxSession(following, attempt)
    session.sync_inputs()
    placed = session.input_dir / artifact.path
    assert placed.read_text().startswith("code,name\n")
    assert oct(placed.stat().st_mode & 0o777) == "0o644"
    assert oct(placed.parent.stat().st_mode & 0o777) == "0o755"
    session.close()


@pytest.mark.django_db
def test_attachment_named_like_the_connector_directory_is_renamed(conversation):
    attachment = ArtifactFactory.create(owner=conversation.owner, conversation=conversation, name="connectors")
    Artifact.attach(conversation, [attachment])
    assert attachment.path == "connectors (2)"


@pytest.mark.django_db
def test_busy_connection_is_reported_to_the_agent(conversation, monkeypatch):
    connector = ConnectorFactory.create(name="販売管理", version__tables=TABLES, connection__max_concurrency=1)
    ConnectionLease.objects.create(
        connection=connector.connection, holder="other", expires=timezone.now() + timedelta(minutes=5)
    )
    monkeypatch.setattr(datasource_module, "SLOT_WAIT_SECONDS", 0)
    run = start(conversation)
    adapter = FakeAdapter(
        [
            calls(("c1", "datasource_query", {"connector": "販売管理", "sql": "SELECT code FROM vendors"})),
            reply("待ちます"),
        ]
    )
    run = execute(run, adapter)

    [result] = results(run, "datasource_query")
    assert result["is_error"]
    assert "(busy)" in text_of(result)
    assert FakeDatasource.calls == []
    assert ConnectionLease.objects.filter(connection=connector.connection).count() == 1
