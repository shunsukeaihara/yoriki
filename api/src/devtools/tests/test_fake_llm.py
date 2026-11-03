"""偽のLLM。ルールの照らし方と、実際のProvider Adapter（ローカルLLM）から呼んだときの応答を確かめる"""

import json
import os
import threading
from pathlib import Path

import pytest

from devtools.fake_llm import DEFAULT_SCENARIO, FakeLLMServer, Responder, Scenario, ScenarioError, Turn
from llm.models import LLMConnection, ProviderKind
from llm.providers import get_adapter
from llm.providers.base import (
    ChatRequest,
    Completed,
    Message,
    ProviderError,
    StopReason,
    TextDelta,
    ToolCall,
    ToolCallRequested,
    ToolDefinition,
)
from llm.providers.openai import TOOL_IMAGES_LABEL

MASTER_FILE = r"(?P<file>/workspace/master/[^\n]+?) \("
ASSETS = "<assets>\n  /workspace/master/skills/s1/k/{} (参照, version 1)"
SKILL_TOOL = ToolDefinition(
    "skill", "Skillを読み込む", {"type": "object", "properties": {"name": {"type": "string", "enum": ["請求書の照合"]}}}
)
READ_TOOL = ToolDefinition("read", "読む", {"type": "object", "properties": {"path": {"type": "string"}}})


def body(messages: list[dict], *, tools: tuple[str, ...] = (), model: str = "fake") -> dict:
    return {
        "model": model,
        "stream": True,
        "messages": messages,
        "tools": [{"type": "function", "function": {"name": t, "parameters": {}}} for t in tools],
    }


def call(name: str) -> dict:
    return {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "function": {"name": name}}]}


def write_scenario(tmp_path: Path, data: dict) -> Responder:
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return Responder(path)


def test_turn_counts_steps_after_the_last_prompt():
    turn = Turn.parse(
        body(
            [
                {"role": "system", "content": "あなたは"},
                {"role": "user", "content": "前の依頼"},
                {"role": "assistant", "content": "前の回答"},
                {"role": "user", "content": "[shell] 確かめて\n2行目"},
                # Runnerが足す文脈も、今の依頼の発言に含める
                {"role": "user", "content": "<runtime_context>…</runtime_context>"},
                call("shell"),
                {"role": "tool", "tool_call_id": "c1", "content": "結果1"},
                # Toolの結果の画像を移した発言は、利用者の発言として数えない
                {"role": "user", "content": [{"type": "text", "text": TOOL_IMAGES_LABEL.format(id="c1")}]},
                call("read"),
                {"role": "tool", "tool_call_id": "c2", "content": "結果2"},
            ],
            tools=("shell", "read"),
        )
    )
    assert (turn.step, turn.called, turn.result) == (2, "read", "結果2")
    assert turn.prompt == "[shell] 確かめて\n2行目\n<runtime_context>…</runtime_context>"
    assert turn.system == "あなたは"
    assert turn.variables()["prompt"] == "[shell] 確かめて"
    assert set(turn.tools) == {"shell", "read"}


def test_rules_match_in_order_and_fill_variables(tmp_path):
    responder = write_scenario(
        tmp_path,
        {
            "rules": [
                {"name": "合わない", "when": {"prompt": "[none]"}, "reply": {"text": "x"}},
                {
                    "name": "読む",
                    "when": {"prompt": "[skill]", "called": "skill", "match": MASTER_FILE},
                    "reply": {"tool_calls": [{"name": "read", "arguments": {"path": "${file}", "note": "${HOME}"}}]},
                },
                {
                    "name": "読み込む",
                    "when": {"prompt": "[skill]", "step": 0, "tools": ["skill"]},
                    "reply": {"tool_calls": [{"name": "skill", "arguments": {"name": "${enum:skill.name}"}}]},
                },
            ],
            "default": {"text": "既定：${prompt}"},
        },
    )
    skill = {"type": "function", "function": {"name": "skill", "parameters": SKILL_TOOL.parameters}}
    first = body([{"role": "user", "content": "[skill] 照合して"}]) | {"tools": [skill]}
    assert responder.choose(first)[0::2] == (
        "読み込む",
        {"tool_calls": [{"name": "skill", "arguments": {"name": "請求書の照合"}}]},
    )

    loaded = body(
        [
            {"role": "user", "content": "[skill] 照合して"},
            call("skill"),
            {"role": "tool", "content": ASSETS.format("台帳 2026.csv")},
        ]
    )
    # 知らない変数（シェルの変数）はそのまま残す
    read_arguments = {"path": "/workspace/master/skills/s1/k/台帳 2026.csv", "note": "${HOME}"}
    assert responder.choose(loaded)[0::2] == (
        "読む",
        {"tool_calls": [{"name": "read", "arguments": read_arguments}]},
    )
    hello = body([{"role": "user", "content": "こんにちは"}])
    assert responder.choose(hello)[0::2] == ("default", {"text": "既定：こんにちは"})


def test_times_counts_the_same_request(tmp_path):
    """同じ要求（Runnerの再試行）には最初のN回だけ合い、その後は次のルールに進む"""
    responder = write_scenario(
        tmp_path,
        {"rules": [{"name": "エラー", "when": {"prompt": "[error]"}, "times": 2, "reply": {"status": 500}}]},
    )
    request = body([{"role": "user", "content": "[error]"}])
    assert [responder.choose(request)[0] for _ in range(3)] == ["エラー", "エラー", "default"]
    # 中身の違う要求は、別に数える
    assert responder.choose(body([{"role": "user", "content": "[error] 別"}]))[0] == "エラー"


def test_scenario_is_reloaded_when_the_file_changes(tmp_path):
    responder = write_scenario(tmp_path, {"default": {"text": "1"}})
    assert responder.choose(body([]))[2] == {"text": "1"}
    responder.path.write_text(json.dumps({"default": {"text": "2"}}), encoding="utf-8")
    stat = responder.path.stat()
    os.utime(responder.path, (stat.st_atime, stat.st_mtime + 1))
    assert responder.choose(body([]))[2] == {"text": "2"}


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"rule": []},
        {"rules": [{"when": {"unknown": 1}}]},
        {"rules": [{"when": {"match": "("}}]},
        {"rules": [{"reply": {"txt": "x"}}]},
        {"rules": [{"reply": {"finish": "end"}}]},
        {"rules": [{"times": 0}]},
        {"rules": [{"reply": {"tool_calls": [{"arguments": {}}]}}]},
    ],
)
def test_invalid_scenario(data):
    with pytest.raises(ScenarioError):
        Scenario(data)


def test_default_scenario_is_valid():
    scenario = Scenario.load(DEFAULT_SCENARIO)
    assert scenario.rules
    assert len({rule.name for rule in scenario.rules}) == len(scenario.rules)


@pytest.fixture(name="fake_llm")
def fixture_fake_llm():
    """既定の台本で偽のLLMを起動し、ローカルLLMの接続を返す"""
    lines: list[str] = []
    server = FakeLLMServer(("127.0.0.1", 0), Responder(DEFAULT_SCENARIO), log=lines.append, sleep=lambda s: None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield LLMConnection(kind=ProviderKind.LOCAL, base_url=f"http://127.0.0.1:{server.server_port}/v1", api_key="")
    server.shutdown()
    server.server_close()


def test_adapter_streams_text_and_tool_calls(fake_llm):
    """実際のローカルLLMのAdapterで、文・Toolの呼出し・usageを受け取れる"""
    adapter = get_adapter(fake_llm)
    assert adapter.list_models() == ["fake", "fake-reasoning"]

    events = list(adapter.stream(ChatRequest(model="fake", messages=[Message("user", "こんにちは")])))
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert text.startswith("（偽のLLM）「こんにちは」を受け取りました。")
    assert isinstance(events[-1], Completed) and events[-1].stop_reason == StopReason.END

    request = ChatRequest(model="fake", messages=[Message("user", "[skill] 照合して")], tools=[SKILL_TOOL, READ_TOOL])
    events = list(adapter.stream(request))
    calls = [e.call for e in events if isinstance(e, ToolCallRequested)]
    assert [(c.name, c.arguments) for c in calls] == [("skill", {"name": "請求書の照合"})]
    assert isinstance(events[-1], Completed) and events[-1].stop_reason == StopReason.TOOL_USE

    followup = ChatRequest(
        model="fake",
        messages=[
            Message("user", "[skill] 照合して"),
            Message("assistant", "", (ToolCall(calls[0].id, "skill", calls[0].arguments),)),
            Message("tool", ASSETS.format("vendors.csv"), tool_call_id=calls[0].id),
        ],
        tools=[SKILL_TOOL, READ_TOOL],
    )
    calls = [e.call for e in adapter.stream(followup) if isinstance(e, ToolCallRequested)]
    assert [(c.name, c.arguments) for c in calls] == [("read", {"path": "/workspace/master/skills/s1/k/vendors.csv"})]


def test_adapter_receives_errors(fake_llm):
    adapter = get_adapter(fake_llm)
    request = ChatRequest(model="fake", messages=[Message("user", "[error]")])
    for _ in range(2):
        with pytest.raises(ProviderError) as error:
            list(adapter.stream(request))
        assert error.value.retryable
    # 3回目（Runnerの再試行）は既定の応答
    assert any(isinstance(e, TextDelta) for e in adapter.stream(request))


def test_default_scenario_edits_a_skill():
    """既定の台本の [skill-edit]：Draftの版を前の結果から読み、整数のJSONの引数にする"""
    responder = Responder(DEFAULT_SCENARIO)
    tools = ("get_skill", "update_skill_draft", "publish_skill_draft")
    messages = [{"role": "user", "content": "[skill-edit] 説明を直す"}]
    assert responder.choose(body(messages, tools=tools))[0] == "編集のRun：Skillを読む"

    messages += [call("get_skill"), {"role": "tool", "content": '<skill>\n<draft revision="3" base_version="2">'}]
    name, _turn, reply = responder.choose(body(messages, tools=tools))
    assert name == "編集のRun：Draftを直す"
    assert json.loads(reply["tool_calls"][0]["arguments"])["expected_revision"] == 3

    messages += [call("update_skill_draft"), {"role": "tool", "content": "Saved the draft (now revision 4). Changed"}]
    name, _turn, reply = responder.choose(body(messages, tools=tools))
    assert name == "編集のRun：公開する"
    assert json.loads(reply["tool_calls"][0]["arguments"]) == {"expected_revision": 4, "change_reason": "偽のLLMの確認"}
