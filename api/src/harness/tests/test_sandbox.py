"""SandboxのToolとWorkspaceの引継ぎのテスト。LLMの応答は FakeAdapter で差し替え、Sandboxは実際のDockerで動かす
（イメージがなければ飛ばす。conftest.py の sandbox_image）"""

import io
from unittest import mock

import docker
import pytest

from artifact.models import Artifact, ArtifactKind
from conversation.dispatch import MessageMode, post_message
from conversation.factories import ConversationFactory
from conversation.models import ConversationMessage
from harness.runner import Runner
from run.models import (
    Checkpoint,
    ClarificationRequest,
    ExecutionAttempt,
    Run,
    RunEvent,
    RunEventKind,
    RunStatus,
)
from run.tasks import claim_run

from .fakes import FakeAdapter, calls, reply

pytestmark = [pytest.mark.usefixtures("default_runtime", "sandbox_image"), pytest.mark.django_db, pytest.mark.sandbox]

QUESTION = {
    "questions": [
        {"header": "形式", "question": "どちらで出しますか", "options": [{"label": "CSV"}, {"label": "Excel"}]}
    ]
}


@pytest.fixture(name="conversation")
def fixture_conversation(app_user):
    return ConversationFactory.create(owner=app_user)


def post(conversation, body="作業して") -> Run:
    post_message(conversation, body=body, client_message_id="", mode=MessageMode.AUTO, actor=conversation.owner)
    return Run.objects.filter(conversation=conversation).latest("pk")


def execute(run: Run, adapter: FakeAdapter) -> Run:
    attempt = claim_run(run.pk)
    assert attempt is not None
    Runner(attempt, adapter_factory=adapter, sleep=lambda _s: None).run()
    run.refresh_from_db()
    return run


def results(run: Run) -> dict[str, dict]:
    events = RunEvent.objects.filter(run=run, kind=RunEventKind.TOOL_RESULT).order_by("sequence")
    return {e.data["call_id"]: e.data for e in events}


def text(result: dict) -> str:
    return "".join(p["text"] for p in result["content"])


def shell(command: str, **extra) -> dict:
    return {"description": "コマンド", "command": command, **extra}


def containers(run: Run) -> list:
    return docker.from_env().api.containers(all=True, filters={"label": f"org.yoriki.run={run.uid}"})


def test_shell_read_edit(conversation):
    run = post(conversation)
    adapter = FakeAdapter(
        [
            calls(("c1", "shell", shell("printf 'a\\nb\\n' > a.txt && ls"))),
            calls(("c2", "read", {"path": "/workspace/work/a.txt"})),
            calls(("c3", "edit", {"path": "/workspace/work/a.txt", "old_string": "b", "new_string": "c"})),
            calls(("c4", "shell", shell("cat a.txt; exit 3"))),
            reply("できました。"),
        ]
    )
    run = execute(run, adapter)

    assert run.status == RunStatus.COMPLETED
    r = results(run)
    assert text(r["c1"]).startswith("Exit code: 0\nWall time:")
    assert "a.txt" in text(r["c1"])
    assert "     1│a" in text(r["c2"])
    assert r["c2"]["value"]["file"]["path"] == "/workspace/work/a.txt"
    assert "Replaced 1 occurrence" in text(r["c3"])
    assert text(r["c4"]).startswith("Exit code: 3")
    assert "a\nc" in text(r["c4"])
    attempt = ExecutionAttempt.objects.get(run=run)
    assert attempt.sandbox_platform.startswith("linux/")
    # 終えたら保存して、Sandboxを消す
    assert Checkpoint.objects.filter(run=run).count() == 1
    assert containers(run) == []


def test_edit_and_overwrite_require_a_read(conversation):
    run = post(conversation)
    adapter = FakeAdapter(
        [
            calls(("c1", "shell", shell("echo x > a.txt"))),
            calls(("c2", "edit", {"path": "/workspace/work/a.txt", "old_string": "x", "new_string": "y"})),
            calls(("c3", "write", {"path": "/workspace/work/a.txt", "content": "z"})),
            calls(("c4", "write", {"path": "/workspace/input/a.txt", "content": "z"})),
            calls(("c5", "write", {"path": "/workspace/work/new/b.txt", "content": "z"})),
            reply("終わり"),
        ]
    )
    run = execute(run, adapter)
    r = results(run)
    assert r["c2"]["is_error"] and "not read" in text(r["c2"])
    assert r["c3"]["is_error"] and "has not been read" in text(r["c3"])
    assert r["c4"]["is_error"] and "not writable" in text(r["c4"])
    assert not r["c5"]["is_error"] and text(r["c5"]).startswith("Created /workspace/work/new/b.txt")


def test_changed_after_read(conversation):
    run = post(conversation)
    adapter = FakeAdapter(
        [
            calls(("c1", "write", {"path": "/workspace/work/a.txt", "content": "x\n"})),
            calls(("c2", "shell", shell("echo y >> a.txt"))),
            calls(("c3", "edit", {"path": "/workspace/work/a.txt", "old_string": "x", "new_string": "z"})),
            reply("終わり"),
        ]
    )
    run = execute(run, adapter)
    r = results(run)
    # write の結果の版を読んだ版として使い、shellで変わったことに気づく
    assert r["c3"]["is_error"] and "changed" in text(r["c3"]).lower()


def test_workspace_survives_waiting_for_the_user(conversation, app_user):
    run = post(conversation)
    run = execute(
        run,
        FakeAdapter(
            [
                calls(("c1", "shell", shell("echo 1 > /workspace/output/r.csv && echo 2 > memo.txt"))),
                calls(("c2", "read", {"path": "/workspace/work/memo.txt"})),
                calls(("c3", "ask_user", QUESTION)),
            ]
        ),
    )
    assert run.status == RunStatus.WAITING_USER
    assert Checkpoint.objects.filter(run=run).count() == 1
    assert containers(run) == []

    ClarificationRequest.objects.get(run=run).accept_answer([{"selected": ["CSV"], "text": ""}], "", app_user)
    run = execute(
        run,
        FakeAdapter(
            [
                calls(("c4", "read", {"path": "/workspace/output/r.csv"})),
                # 確認待ちの前に読んだ版が、戻したWorkspaceでも使える
                calls(("c5", "edit", {"path": "/workspace/work/memo.txt", "old_string": "2", "new_string": "3"})),
                reply("CSVで出しました。"),
            ]
        ),
    )
    assert run.status == RunStatus.COMPLETED
    r = results(run)
    assert "     1│1" in text(r["c4"])
    assert not r["c5"]["is_error"], text(r["c5"])


def test_next_request_continues_from_the_previous_files(conversation):
    first = execute(post(conversation), FakeAdapter([calls(("c1", "shell", shell("echo v1 > a.txt"))), reply("1")]))
    assert first.status == RunStatus.COMPLETED
    # Sandboxを使わない依頼を挟んでも、その前の依頼のファイルから続ける
    second = execute(post(conversation, "ありがとう"), FakeAdapter([reply("どういたしまして")]))
    assert second.parent == first
    third = post(conversation, "続けて")
    third = execute(third, FakeAdapter([calls(("c2", "read", {"path": "/workspace/work/a.txt"})), reply("3")]))
    assert third.parent == second
    assert "     1│v1" in text(results(third)["c2"])


def test_command_that_outlives_the_timeout_becomes_a_job(conversation, monkeypatch):
    monkeypatch.setattr("harness.sandbox.new_job_id", lambda: "job_1")
    run = post(conversation)
    adapter = FakeAdapter(
        [
            calls(("c1", "shell", shell("echo start; sleep 2; echo end", timeout_seconds=1))),
            calls(("c2", "job_output", {"job_id": "job_1", "wait": 30})),
            calls(("c3", "job_kill", {"job_id": "job_1"})),
            calls(("c4", "job_output", {"job_id": "job_9"})),
            reply("終わり"),
        ]
    )
    run = execute(run, adapter)
    r = results(run)
    assert "still running" in text(r["c1"]) and "start" in text(r["c1"])
    # 前回の続きだけを返す
    assert "finished with exit code 0" in text(r["c2"])
    assert "end" in text(r["c2"]) and "start" not in text(r["c2"])
    assert "already finished" in text(r["c3"])
    assert r["c4"]["is_error"]
    # 結果を返したジョブは、終わりを知らせ直さない
    assert not RunEvent.objects.filter(run=run, kind=RunEventKind.JOB_FINISHED).exists()


def test_job_kill_stops_a_job(conversation, monkeypatch):
    monkeypatch.setattr("harness.sandbox.new_job_id", lambda: "job_1")
    run = post(conversation)
    adapter = FakeAdapter(
        [
            calls(("c1", "shell", shell("echo begin; sleep 60", background=True))),
            calls(("c2", "job_kill", {"job_id": "job_1"})),
            reply("止めました"),
        ]
    )
    run = execute(run, adapter)
    r = results(run)
    assert "stopped" in text(r["c2"]) and "begin" in text(r["c2"])
    assert Checkpoint.objects.get(run=run).stopped_jobs == []


def test_background_job_is_reported_when_it_finishes(conversation):
    run = post(conversation)
    adapter = FakeAdapter(
        [
            calls(("c1", "shell", shell("sleep 1; echo done", background=True))),
            calls(("c2", "shell", shell("sleep 3"))),
            reply("終わり"),
        ]
    )
    run = execute(run, adapter)
    assert run.status == RunStatus.COMPLETED
    finished = RunEvent.objects.get(run=run, kind=RunEventKind.JOB_FINISHED)
    assert finished.data["exit_code"] == 0
    # 終わったことは、次の要求でモデルに伝わる
    assert any("finished with exit code 0" in m.text for m in adapter.requests[2].messages)


def test_running_job_is_raised_before_the_final_reply(conversation):
    run = post(conversation)
    adapter = FakeAdapter(
        [calls(("c1", "shell", shell("sleep 60", background=True))), reply("終わりました"), reply("終わりました")]
    )
    run = execute(run, adapter)
    assert run.status == RunStatus.COMPLETED
    assert any("still running" in m.text for m in adapter.requests[2].messages)
    checkpoint = Checkpoint.objects.get(run=run)
    assert [j["command"] for j in checkpoint.stopped_jobs] == ["sleep 60"]


def test_long_output_is_truncated_and_saved(conversation):
    run = post(conversation)
    adapter = FakeAdapter(
        [
            calls(("c1", "shell", shell("seq 1 100000"))),
            calls(("c2", "glob", {"pattern": "*.txt", "path": "/workspace/work/.yoriki/outputs"})),
            reply("終わり"),
        ]
    )
    run = execute(run, adapter)
    r = results(run)
    output = text(r["c1"])
    assert "[output truncated" in output
    assert "\n100000" in output  # 末尾を残す
    assert "/workspace/work/.yoriki/outputs/" in output
    assert ".yoriki/outputs/" in text(r["c2"])


def test_independent_reads_run_together(conversation):
    run = post(conversation)
    adapter = FakeAdapter(
        [
            calls(("c1", "shell", shell("echo a > a.txt; echo b > b.txt"))),
            calls(
                ("c0", "read", {"path": 1}),
                ("c2", "read", {"path": "/workspace/work/a.txt"}),
                ("c3", "read", {"path": "/workspace/work/b.txt"}),
                ("c4", "grep", {"pattern": "b", "path": "/workspace/work"}),
                ("c5", "read", {"path": "/workspace/work/none.txt"}),
                ("c6", "read", {"path": 1}),
            ),
            reply("終わり"),
        ]
    )
    run = execute(run, adapter)
    r = results(run)
    assert "     1│a" in text(r["c2"]) and "     1│b" in text(r["c3"])
    assert "b.txt: 1" in text(r["c4"])
    assert r["c5"]["is_error"]
    sequences = [e["call_id"] for e in r.values()]
    # 引数の誤りも、モデルが返した順に記録する
    assert sequences == ["c1", "c0", "c2", "c3", "c4", "c5", "c6"]
    assert r["c6"]["is_error"] and "Invalid arguments" in text(r["c6"])


def test_finished_jobs_do_not_count_against_the_limit(conversation):
    run = post(conversation)
    quick = [(f"b{n}", "shell", shell("true", background=True)) for n in range(4)]
    adapter = FakeAdapter([calls(*quick), calls(("c1", "shell", shell("sleep 1; echo ok"))), reply("終わり")])
    run = execute(run, adapter)
    r = results(run)
    assert not r["c1"]["is_error"], text(r["c1"])
    assert text(r["c1"]).startswith("Exit code: 0")


def test_large_job_output_is_saved_in_full(conversation):
    """出力が大きくても、退避したファイルには全体がある（途中も末尾も読める）"""
    run = post(conversation)
    adapter = FakeAdapter(
        [
            calls(("c1", "shell", shell("seq 1 1500000"))),
            calls(("c2", "shell", shell("wc -l .yoriki/outputs/*.txt; tail -n 1 .yoriki/outputs/*.txt"))),
            reply("終わり"),
        ]
    )
    run = execute(run, adapter)
    r = results(run)
    assert "[full output: /workspace/work/.yoriki/outputs/" in text(r["c1"])
    assert "1500000 /workspace/work/.yoriki/outputs/" in text(r["c2"]) or "1500000 .yoriki/outputs/" in text(r["c2"])
    assert text(r["c2"]).rstrip().endswith("1500000")


def test_attachments_are_read_only_inputs_and_outputs_become_artifacts(conversation):
    """添付は /workspace/input に読取り専用で見え、/workspace/output のファイルは成果物になる（design.md §7.1・§7.4）"""
    attachment = Artifact.upload(
        io.BytesIO(b"a,b\n1,2\n"), "請求書.csv", owner=conversation.owner, conversation=conversation
    )
    post_message(
        conversation,
        body="集計して",
        client_message_id="",
        mode=MessageMode.AUTO,
        actor=conversation.owner,
        attachments=[attachment],
    )
    run = Run.objects.get(conversation=conversation)
    adapter = FakeAdapter(
        [
            calls(("c1", "shell", shell("cat /workspace/input/請求書.csv; touch /workspace/input/x"))),
            calls(
                (
                    "c2",
                    "shell",
                    shell(
                        "mkdir -p /workspace/output/sub /workspace/output/.cache && echo total > /workspace/output/sub/"
                        "集計.csv && echo hidden > /workspace/output/.cache/x && ln -s /etc/passwd /workspace/output/l"
                    ),
                )
            ),
            calls(("c3", "ask_user", QUESTION)),
        ]
    )
    run = execute(run, adapter)
    assert run.status == RunStatus.WAITING_USER
    r = results(run)
    assert "a,b\n1,2" in text(r["c1"]) and "Read-only file system" in text(r["c1"])
    outputs = Artifact.objects.filter(run=run, kind=ArtifactKind.OUTPUT)
    # 隠しファイルとsymlinkは回収しない
    assert [(a.path, a.name) for a in outputs] == [("sub/集計.csv", "集計.csv")]
    with outputs[0].blob.open() as f:
        assert f.read() == b"total\n"

    # 回答の後の新しいAttemptでは、変わっていない成果物を回収し直さない。追加指示の添付は動いているSandboxに見える
    clarification = ClarificationRequest.objects.get(run=run)
    clarification.accept_answer([{"selected": ["CSV"], "text": ""}], "", conversation.owner)
    adapter = FakeAdapter(
        [
            calls(("c4", "shell", shell("sleep 0.1"))),
            calls(("c5", "shell", shell("ls /workspace/input; echo changed > /workspace/output/sub/集計.csv"))),
            reply("できました"),
        ]
    )
    later = Artifact.upload(io.BytesIO(b"x"), "追加.txt", owner=conversation.owner, conversation=conversation)

    original_step = Runner._step

    def step(self):
        # Sandboxが動いている間に、添付つきの追加指示を送る
        if self.session.started and not ConversationMessage.objects.filter(body="これも").exists():
            post_message(
                conversation,
                body="これも",
                client_message_id="",
                mode=MessageMode.AUTO,
                actor=conversation.owner,
                attachments=[later],
            )
        return original_step(self)

    with mock.patch.object(Runner, "_step", step):
        run = execute(run, adapter)
    assert run.status == RunStatus.COMPLETED
    assert "追加.txt" in text(results(run)["c5"]) and "請求書.csv" in text(results(run)["c5"])
    outputs = Artifact.objects.filter(run=run, kind=ArtifactKind.OUTPUT).order_by("pk")
    assert [a.path for a in outputs] == ["sub/集計.csv", "sub/集計.csv"]
    with outputs[1].blob.open() as f:
        assert f.read() == b"changed\n"
