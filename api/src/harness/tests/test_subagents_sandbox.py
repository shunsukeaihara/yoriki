"""サブエージェントのワークスペース（親のコピー、返すファイル、/workspace/input/subagents/）のテスト。Sandboxは実際の
Dockerで動かす（イメージがなければ飛ばす。conftest.py の sandbox_image）"""

import pytest

from artifact.models import ArtifactKind
from conversation.dispatch import post_message
from conversation.factories import ConversationFactory
from harness.runner import Runner
from harness.subagent_runner import SubagentRunner
from run.models import Run, RunEvent, RunEventKind, RunStatus, Subagent, SubagentStatus
from run.tasks import claim_run, claim_subagent

from .fakes import FakeAdapter, calls, reply

pytestmark = [pytest.mark.usefixtures("default_runtime", "sandbox_image"), pytest.mark.django_db, pytest.mark.sandbox]


def shell(command: str) -> dict:
    return {"description": "コマンド", "command": command}


def results(run: Run, **filters) -> dict[str, str]:
    events = RunEvent.objects.filter(run=run, kind=RunEventKind.TOOL_RESULT, **filters).order_by("sequence")
    return {e.data["call_id"]: "".join(p["text"] for p in e.data["content"]) for e in events}


def test_subagent_works_on_a_copy_and_returns_new_or_changed_files(app_user):
    conversation = ConversationFactory.create(owner=app_user)
    post_message(conversation, body="作業して", client_message_id="", actor=app_user)
    run = Run.objects.get(conversation=conversation)

    prepare = "echo parent > /workspace/work/a.txt && echo keep > /workspace/output/keep.txt"
    start = ("s1", "subagent", {"description": "写す", "prompt": "a.txt を写して"})
    parent = FakeAdapter([calls(("c1", "shell", shell(prepare))), calls(start), reply("頼みました。")])
    attempt = claim_run(run.pk)
    assert attempt is not None
    Runner(attempt, adapter_factory=parent, sleep=lambda _s: None).run()
    run.refresh_from_db()
    assert run.status == RunStatus.WAITING_BACKGROUND
    subagent = Subagent.objects.get(run=run)

    # 子はコピーの上で動く。子の変更は親のファイルに届かない
    work = (
        "cat /workspace/work/a.txt > /workspace/output/from_parent.txt && echo new > /workspace/output/new.txt "
        "&& echo child > /workspace/work/a.txt"
    )
    child = FakeAdapter([calls(("c1", "shell", shell(work))), reply("写しました。")])
    child_attempt = claim_subagent(subagent.pk)
    assert child_attempt is not None
    SubagentRunner(child_attempt, adapter_factory=child, sleep=lambda _s: None).run()
    subagent.refresh_from_db()
    assert subagent.status == SubagentStatus.COMPLETED, subagent.error
    # 変わっていない親の成果物（keep.txt）は返さない
    assert sorted(f["path"] for f in subagent.result_files) == ["from_parent.txt", "new.txt"]
    returned = run.artifacts.get(kind=ArtifactKind.SUBAGENT_RESULT, path=f"subagents/{subagent.uid}/from_parent.txt")
    with returned.blob.open() as f:
        assert f.read() == b"parent\n"
    assert subagent.copy_key == ""

    run.refresh_from_db()
    assert run.status == RunStatus.QUEUED
    read_back = f"cat /workspace/input/subagents/{subagent.uid}/new.txt /workspace/work/a.txt"
    resumed = FakeAdapter([calls(("c2", "shell", shell(read_back))), reply("受け取りました。")])
    attempt = claim_run(run.pk)
    assert attempt is not None
    Runner(attempt, adapter_factory=resumed, sleep=lambda _s: None).run()
    run.refresh_from_db()
    assert run.status == RunStatus.COMPLETED
    output = results(run, subagent__isnull=True)["c2"]
    assert "new" in output
    assert "parent" in output and "child" not in output
