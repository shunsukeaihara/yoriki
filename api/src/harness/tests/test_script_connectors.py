"""SkillのスクリプトからConnectorを使う（design.md §4.9.6、harness.md §10.2）。Toolと同じ確かめ方・上限で実行し、
ToolInvocationに記録して、結果はスクリプトへ返す。回数・頻度の上限"""

import queue
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest
from rest_framework.test import APIClient

from artifact.models import Artifact, ArtifactKind
from connector.errors import ConnectorError, ConnectorErrorKind
from connector.factories import ConnectorFactory
from harness import script_connectors
from harness.connectors import DATASOURCE_TOOL_NAMES
from harness.prompts import system_prompt
from harness.sandbox import SCRIPT_REQUESTS_BUSY, SCRIPT_REQUESTS_PER_CALL, SandboxSession
from harness.script_connectors import ScriptConnectors
from harness.tools.base import Tool
from run.models import RunEvent, RunEventKind, ToolInvocation, ToolStatus
from run.tasks import claim_run

from .test_connectors import TABLES, FakeDatasource, fixture_conversation, fixture_fake_datasource, start  # noqa: F401  fixture

pytestmark = [pytest.mark.usefixtures("default_runtime", "fake_datasource"), pytest.mark.django_db]

QUERY = {"op": "query", "connector": "販売管理", "sql": "SELECT code FROM vendors WHERE code = ?", "params": ["001"]}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(name="connector")
def fixture_connector(db):
    return ConnectorFactory.create(name="販売管理", version__tables=TABLES)


@pytest.fixture(name="attempt")
def fixture_attempt(conversation, connector):
    run = start(conversation)
    attempt = claim_run(run.pk)
    assert attempt is not None
    return attempt


def make(attempt, *, exposed=DATASOURCE_TOOL_NAMES, clock=None) -> ScriptConnectors:
    return ScriptConnectors(
        attempt.run,
        attempt,
        session=None,
        exposed=lambda: frozenset(exposed),
        current_call=lambda: "call-shell-1",
        **({"clock": clock} if clock else {}),
    )


def test_query_runs_like_the_tool_and_is_recorded(attempt):
    response = make(attempt).handle(QUERY)

    invocation = ToolInvocation.objects.get(run=attempt.run)
    assert invocation.call_id.startswith("script-")
    assert (invocation.name, invocation.status) == ("datasource_query", ToolStatus.SUCCEEDED)
    assert invocation.arguments == {k: v for k, v in QUERY.items() if k != "op"}
    value = invocation.value or {}
    assert value["via"] == "script"
    assert value["during"] == "call-shell-1"
    assert value["rows"] == 30
    assert response == {
        "ok": True,
        "connector": "販売管理",
        "version": 1,
        "path": f"/workspace/input/connectors/{invocation.uid}/result.csv",
        "columns": ["code", "name"],
        "row_count": 30,
        "truncated": False,
    }
    # 行はファイルで渡すので、メモリには残さない
    assert FakeDatasource.calls == [("query", QUERY["sql"], ("001",), 0)]
    artifact = Artifact.objects.get(run=attempt.run, kind=ArtifactKind.CONNECTOR_RESULT)
    assert artifact.path == f"connectors/{invocation.uid}/result.csv"
    # モデルの文脈には入れない
    assert not RunEvent.objects.filter(run=attempt.run, kind=RunEventKind.TOOL_RESULT).exists()


def test_describe_returns_the_tables(attempt):
    response = make(attempt).handle({"op": "describe", "connector": "販売管理"})

    assert response["ok"]
    assert response["tables"] == [
        {
            "schema": "public",
            "name": "vendors",
            "kind": "view",
            "description": "取引先",
            "columns": [
                {"name": "code", "type": "text", "description": "取引先コード"},
                {"name": "name", "type": "text", "description": ""},
            ],
        }
    ]
    assert ToolInvocation.objects.get(run=attempt.run).name == "datasource_describe"


def test_only_the_tools_given_to_the_agent_can_be_used(attempt):
    handler = make(attempt, exposed=("datasource_describe", "shell"))
    assert handler.available()

    response = handler.handle(QUERY)

    assert response["error"]["code"] == "unavailable"
    assert "datasource_query" in response["error"]["message"]
    assert handler.handle({"op": "describe", "connector": "販売管理"})["ok"]
    assert not make(attempt, exposed=("shell",)).available()
    assert FakeDatasource.calls == [("describe", 1)]


def test_without_connectors_in_the_run_scripts_cannot_use_them(conversation):
    run = start(conversation)
    attempt = claim_run(run.pk)
    handler = make(attempt)

    assert not handler.available()
    assert handler.handle({"op": "describe", "connector": "販売管理"})["error"]["code"] == "unavailable"


@pytest.mark.parametrize(
    "request_",
    [
        {"op": "drop", "connector": "販売管理"},
        {"connector": "販売管理"},
        {"op": "query", "connector": "ほかのDB", "sql": "SELECT 1"},
        {"op": "query", "connector": "販売管理"},
        {"op": "query", "connector": "販売管理", "sql": "SELECT 1", "params": [1]},
        {"op": "query", "connector": "販売管理", "sql": "SELECT 1", "extra": True},
    ],
)
def test_invalid_requests_are_refused(attempt, request_):
    response = make(attempt).handle(request_)

    assert response["error"]["code"] == "invalid_argument"
    assert not ToolInvocation.objects.exists()
    assert FakeDatasource.calls == []


def test_permissions_are_checked_again_for_each_call(attempt, connector):
    handler = make(attempt)
    assert handler.handle(QUERY)["ok"]
    connector.is_active = False
    connector.save()

    response = handler.handle(QUERY)

    assert response["error"]["code"] == "unavailable"
    assert "cannot be used now" in response["error"]["message"]
    failed = ToolInvocation.objects.filter(run=attempt.run).latest("pk")
    assert failed.status == ToolStatus.FAILED
    assert (failed.value or {})["via"] == "script"
    assert len(FakeDatasource.calls) == 1


def test_query_errors_are_returned_with_their_kind(attempt, fake_datasource):
    fake_datasource.error = ConnectorError(ConnectorErrorKind.REJECTED, "公開していない表です：secrets")

    response = make(attempt).handle(QUERY)

    assert response["error"]["code"] == "rejected"
    assert "公開していない表です" in response["error"]["message"]
    invocation = ToolInvocation.objects.get(run=attempt.run)
    assert (invocation.value or {})["error"] == "rejected"
    # 失敗の理由は、画面の作業の記録に出すので記録に残す
    assert "公開していない表です" in invocation.error


def test_calls_per_minute_are_limited(attempt, monkeypatch):
    monkeypatch.setattr(script_connectors, "MAX_CALLS_PER_MINUTE", 3)
    clock = Clock()
    handler = make(attempt, clock=clock)
    for _ in range(3):
        assert handler.handle(QUERY)["ok"]
        clock.now += 10

    response = handler.handle(QUERY)

    assert response["error"]["code"] == "rate_limited"
    assert response["error"]["retry_after"] == 30
    assert ToolInvocation.objects.count() == 3
    clock.now += 30
    assert handler.handle(QUERY)["ok"]


def test_calls_per_run_are_limited_across_attempts(attempt, monkeypatch):
    monkeypatch.setattr(script_connectors, "MAX_CALLS_PER_RUN", 2)
    assert make(attempt).handle(QUERY)["ok"]

    handler = make(attempt)
    assert handler.handle(QUERY)["ok"]
    response = handler.handle(QUERY)

    assert response["error"]["code"] == "limit_exceeded"
    assert make(attempt).handle({"op": "describe", "connector": "販売管理"})["error"]["code"] == "limit_exceeded"
    assert ToolInvocation.objects.count() == 2


class FakeRelay:
    def __init__(self):
        self.responses = []
        self.done = type("Done", (), {"is_set": staticmethod(lambda: False)})()

    def respond(self, request_id, response):
        self.responses.append((request_id, response))
        return True

    def close(self):
        pass


class FakeHandler:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.requests: list[dict] = []

    def available(self):
        return True

    def handle(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return {"ok": True, "n": len(self.requests)}


def test_session_answers_queued_requests_in_order(attempt):
    session = SandboxSession(attempt.run, attempt)
    session.script_handler = FakeHandler()
    relay: Any = FakeRelay()
    session.script_requests.put((relay, 1, {"op": "describe"}))
    session.script_requests.put((relay, 2, {"op": "query"}))

    session.serve_script_requests()

    assert relay.responses == [(1, {"ok": True, "n": 1}), (2, {"ok": True, "n": 2})]
    assert session.script_requests.empty()
    session.close()


def test_session_answers_with_an_error_when_the_handler_fails(attempt):
    session = SandboxSession(attempt.run, attempt)
    session.script_handler = FakeHandler(RuntimeError("壊れた"))
    relay: Any = FakeRelay()
    session.script_requests.put((relay, 7, {"op": "query"}))

    session.serve_script_requests(timeout=0.1)

    assert relay.responses == [(7, {"ok": False, "error": {"code": "internal", "message": "The request failed."}})]
    session.close()


def test_session_waits_for_the_first_request_only_up_to_the_timeout(attempt):
    session = SandboxSession(attempt.run, attempt)
    session.script_handler = FakeHandler()
    session.script_requests = queue.Queue()

    session.serve_script_requests(timeout=0.05)

    assert session.script_handler.requests == []
    session.close()


def test_runner_gives_the_session_a_handler_for_the_run(attempt):
    from harness.runner import Runner  # noqa: PLC0415

    runner = Runner(attempt)

    assert isinstance(runner.session.script_handler, ScriptConnectors)
    runner.tools = ()
    assert not runner.session.script_handler.available()
    runner.tools = tuple(runner._exposed_tools())
    assert runner.session.script_handler.available()
    runner.session.close()


def test_requests_over_the_queue_limit_are_answered_busy(attempt, monkeypatch):
    session = SandboxSession(attempt.run, attempt)
    session.script_handler = FakeHandler()
    session.script_requests = queue.Queue(1)
    started = {}

    class FakeSandbox:
        def start_relay(self, on_request):
            started["on_request"] = on_request
            return FakeRelay()

    session.sandbox = FakeSandbox()  # type: ignore[assignment]
    session._start_relay()
    relay: Any = FakeRelay()
    started["on_request"](relay, 1, {"op": "describe"})
    started["on_request"](relay, 2, {"op": "describe"})

    assert relay.responses == [(2, SCRIPT_REQUESTS_BUSY)]
    session.serve_script_requests()
    assert relay.responses[1] == (1, {"ok": True, "n": 1})
    session.sandbox = None
    session.close()


def test_unexpected_errors_close_the_record(attempt, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(script_connectors, "run_query", broken)

    response = make(attempt).handle(QUERY)

    assert response == {"ok": False, "error": {"code": "internal", "message": "The request failed."}}
    invocation = ToolInvocation.objects.get(run=attempt.run)
    assert invocation.status == ToolStatus.FAILED
    assert invocation.ended is not None
    assert (invocation.value or {})["error"] == "internal"


def test_invalid_requests_also_count_toward_the_rate_limit(attempt, monkeypatch):
    monkeypatch.setattr(script_connectors, "MAX_CALLS_PER_MINUTE", 2)
    handler = make(attempt, clock=Clock())
    assert handler.handle({"op": "drop"})["error"]["code"] == "invalid_argument"
    assert handler.handle({"op": "drop"})["error"]["code"] == "invalid_argument"

    assert handler.handle(QUERY)["error"]["code"] == "rate_limited"
    assert FakeDatasource.calls == []


def test_session_answers_a_limited_number_of_requests_per_call(attempt):
    session = SandboxSession(attempt.run, attempt)
    session.script_handler = FakeHandler()
    relay: Any = FakeRelay()
    for i in range(SCRIPT_REQUESTS_PER_CALL + 3):
        session.script_requests.put((relay, i, {"op": "describe"}))

    session.serve_script_requests()
    assert len(relay.responses) == SCRIPT_REQUESTS_PER_CALL
    session.serve_script_requests()
    assert len(relay.responses) == SCRIPT_REQUESTS_PER_CALL + 3
    session.close()


def test_waiting_for_a_job_answers_one_request_at_a_time_and_keeps_the_deadline(attempt, monkeypatch):
    class SlowHandler(FakeHandler):
        def handle(self, request):
            time.sleep(0.3)
            return super().handle(request)

    session = SandboxSession(attempt.run, attempt)
    session.script_handler = SlowHandler()
    session.relay = FakeRelay()  # type: ignore[assignment]
    relay: Any = FakeRelay()
    for i in range(3):
        session.script_requests.put((relay, i, {"op": "describe"}))
    monkeypatch.setattr(session, "refresh", lambda job: False)

    assert not session.wait(cast(Any, object()), 0.2)

    # 処理中の1件は止められないが、期限を過ぎた後に次の要求は始めない
    assert len(relay.responses) == 1
    session.relay = None
    session.close()


def test_a_result_too_large_for_the_relay_is_refused_and_recorded(attempt, monkeypatch):
    monkeypatch.setattr(script_connectors, "MAX_RESPONSE_BYTES", 100)

    response = make(attempt).handle({"op": "describe", "connector": "販売管理"})

    assert response["error"]["code"] == "too_large"
    invocation = ToolInvocation.objects.get(run=attempt.run)
    assert invocation.status == ToolStatus.FAILED
    assert (invocation.value or {})["error"] == "too_large"


def test_large_arguments_are_refused_before_recording(attempt):
    response = make(attempt).handle(QUERY | {"params": ["x" * 70_000]})

    assert response["error"]["code"] == "invalid_argument"
    assert not ToolInvocation.objects.exists()


def test_rate_limit_counts_calls_of_the_previous_attempt(attempt, monkeypatch):
    monkeypatch.setattr(script_connectors, "MAX_CALLS_PER_MINUTE", 2)
    first = make(attempt)
    assert first.handle(QUERY)["ok"]
    assert first.handle(QUERY)["ok"]

    assert make(attempt).handle(QUERY)["error"]["code"] == "rate_limited"


def test_resuming_closes_script_calls_left_running_by_a_previous_attempt(attempt):
    from harness.runner import Runner  # noqa: PLC0415
    from run.models import Run, RunStatus  # noqa: PLC0415

    left = ToolInvocation.objects.create(
        run=attempt.run, attempt=attempt, call_id="script-left", name="datasource_query", status=ToolStatus.RUNNING
    )
    attempt.end()
    Run.objects.filter(pk=attempt.run_id).update(status=RunStatus.QUEUED)
    following = claim_run(attempt.run_id)
    assert following is not None and following.pk != attempt.pk
    runner = Runner(following)
    runner._resume()

    left.refresh_from_db()
    assert left.status == ToolStatus.UNKNOWN
    assert left.ended is not None
    runner.session.close()


def test_script_calls_are_listed_for_the_work_log(attempt, fake_datasource, app_user, other_user):
    """作業の記録で、そのときに実行していたToolの呼出しの下に出す。エージェントのToolの呼出しは含めない"""
    handler = make(attempt)
    handler.handle(QUERY)
    handler.handle({"op": "describe", "connector": "販売管理"})
    fake_datasource.error = ConnectorError(ConnectorErrorKind.REJECTED, "公開していない表です：secrets")
    handler.handle(QUERY)
    ToolInvocation.objects.create(
        run=attempt.run, attempt=attempt, call_id="call-1", name="datasource_query", arguments={}
    )
    client = APIClient()
    client.force_authenticate(app_user)

    res = client.get(f"/api/app/v1/runs/{attempt.run.uid}/script_calls/")

    assert res.status_code == 200
    calls = res.json()["results"]
    assert [
        (c["during"], c["operation"], c["connector"], c["status"], c["rows"], c["tables"], c["error_code"])
        for c in calls
    ] == [
        ("call-shell-1", "datasource_query", "販売管理", ToolStatus.SUCCEEDED, 30, None, ""),
        ("call-shell-1", "datasource_describe", "販売管理", ToolStatus.SUCCEEDED, None, 1, ""),
        ("call-shell-1", "datasource_query", "販売管理", ToolStatus.FAILED, None, None, "rejected"),
    ]
    assert (calls[0]["sql"], calls[0]["params"], calls[0]["truncated"]) == (QUERY["sql"], ["001"], False)
    assert "公開していない表です" in calls[2]["error"]
    # Runのログを見られない人には出さない
    client.force_authenticate(other_user)
    assert client.get(f"/api/app/v1/runs/{attempt.run.uid}/script_calls/").status_code == 404


@pytest.mark.parametrize(
    ("names", "guided"),
    [
        (("shell", "datasource_describe", "datasource_query"), True),
        (("datasource_describe", "datasource_query"), False),
        (("shell", "datasource_describe"), False),
    ],
)
def test_system_prompt_explains_script_connectors_only_with_shell_and_query(names, guided):
    tools = cast(tuple[Tool, ...], tuple(SimpleNamespace(name=name) for name in names))

    text = system_prompt(tools)

    assert ("yoriki_connector" in text) is guided
    if guided:
        # 前面で動かすか job_output で待つこと、行ごとに照会しないこと
        assert "job_output" in text and "IN (...)" in text
