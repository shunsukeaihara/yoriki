"""Draftの評価（design.md §3.7.1、harness.md §10.9）。評価のRun、検証と判定、公開の条件、test_skill_draft"""

from decimal import Decimal
from typing import Any

import pytest

from conversation import evaluation as evaluation_module
from conversation.dispatch import MessageMode, post_message
from conversation.evaluation import cancel_evaluation, match_expected, progress_evaluation, start_evaluation
from conversation.factories import ConversationFactory
from conversation.models import Conversation, ConversationPurpose
from harness.runner import Runner
from llm.factories import LLMConnectionFactory, ModelProfileFactory
from llm.models import ProviderKind
from resources.factories import ResourceVersionFactory
from run.models import (
    ClarificationKind,
    ClarificationRequest,
    ClarificationStatus,
    Run,
    RunEvent,
    RunEventKind,
    RunStatus,
)
from run.tasks import claim_run
from skill.errors import CannotEvaluate
from skill.factories import SkillDraftFactory, SkillFactory, SkillVersionFactory
from skill.models import (
    EvaluationResultState,
    EvaluationStatus,
    EvaluationVerdict,
    Skill,
    SkillAssetBinding,
    SkillAssetRole,
    SkillDraft,
    SkillDraftAsset,
    SkillEvaluation,
    SkillEvaluationCase,
    SkillScope,
)
from usage.models import UsageLedger, UsagePurpose
from user.factories import DepartmentFactory, DepartmentMembershipFactory, grant
from utils.permissions import AppPermission

from .fakes import FakeAdapter, calls, reply
from .test_skill_edit import FileSession

pytestmark = pytest.mark.usefixtures("default_runtime")

SKILLS = "/api/app/v1/skills/"
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


@pytest.fixture(autouse=True)
def fixture_workspaces(settings, tmp_path):
    settings.YORIKI_WORKSPACES_DIR = str(tmp_path / "workspaces")
    FileSession.files = {}


@pytest.fixture(name="model")
def fixture_model():
    return ModelProfileFactory.create(version__supports_tools=True, version__input_price=Decimal("1"))


def skill_with_draft(owner, *, scope=SkillScope.PERSONAL, department=None, **draft) -> Skill:
    """公開中の版（第1版）と、それを元にしたDraftを持つSkill"""
    skill = SkillFactory.create(scope=scope, department=department, created_by=owner)
    version = SkillVersionFactory.create(skill=skill, instructions="1. 旧の手順", output_schema=SCHEMA)
    master = ResourceVersionFactory.create(name="master.csv", content=b"code\n001\n")
    SkillAssetBinding.objects.create(version=version, key="master", role=SkillAssetRole.MASTER, resource_version=master)
    new_master = ResourceVersionFactory.create(name="master.csv", content=b"code\n001\n002\n")
    values = {"instructions": "1. 新しい手順", "output_schema": SCHEMA, **draft}
    created = SkillDraftFactory.create(skill=skill, **values)
    SkillDraftAsset.objects.create(draft=created, key="master", role=SkillAssetRole.MASTER, resource_version=new_master)
    return skill


def case(skill: Skill, name="通常の請求書", **kwargs) -> SkillEvaluationCase:
    values = {"prompt": "請求書を照合して", "created_by": skill.created_by, "updated_by": skill.created_by, **kwargs}
    return SkillEvaluationCase.objects.create(skill=skill, name=name, **values)


def execute(run: Run | None, adapter: FakeAdapter) -> Run:
    assert run is not None
    attempt = claim_run(run.pk)
    assert attempt is not None
    Runner(attempt, adapter_factory=adapter, sleep=lambda _s: None, session_factory=FileSession).run()
    run.refresh_from_db()
    return run


def submit(ok: bool, text: str = "照合しました。") -> FakeAdapter:
    return FakeAdapter([calls(("r1", "submit_result", {"ok": ok})), reply(text)])


def judge(monkeypatch, verdict="better", reason="Draftの方が正確です。") -> FakeAdapter:
    adapter = FakeAdapter([calls(("j1", "report_verdict", {"verdict": verdict, "reason": reason}))])
    monkeypatch.setattr(evaluation_module, "get_adapter", adapter)
    return adapter


def text_of(data: dict[str, Any]) -> str:
    return "".join(part.get("text", "") for part in data["content"])


@pytest.mark.django_db
def test_evaluation_runs_the_published_version_and_the_draft_for_each_case(app_user, model):
    skill = skill_with_draft(app_user, tools=["read"])
    attachment = ResourceVersionFactory.create(name="invoice.pdf", content=b"%PDF")
    target = case(skill, criteria="合計を確かめる")
    target.files.add(attachment)

    evaluation = start_evaluation(skill, cases=[target], actor=app_user, model_version=model.current_version)

    result = evaluation.results.get()
    assert evaluation.draft_revision == 1
    assert evaluation.base_version == skill.current_version
    assert evaluation.instructions == "1. 新しい手順"
    assert [a.key for a in evaluation.assets.all()] == ["master"]
    assert result.base_run is not None and result.draft_run is not None
    for run in (result.base_run, result.draft_run):
        # 評価のRunごとに一覧に出さない会話を作り、ケースの添付を送る
        assert run.conversation.purpose == ConversationPurpose.EVALUATION
        assert [a.path for a in run.conversation.artifacts.all()] == ["invoice.pdf"]
    assert result.base_run.manifest["fixed"]["skills"][0]["version"] == skill.current_version_id
    assert result.draft_run.manifest["fixed"]["skills"][0]["evaluation"] == evaluation.pk

    adapter = submit(True)
    execute(result.draft_run, adapter)
    names = {t.name for t in adapter.requests[0].tools}
    # Skillの `tools` と submit_result だけ。答える人がいないので ask_user も、提案・読込みのToolも出さない
    assert names == {"read", "submit_result"}
    first = next(m.text for m in adapter.requests[0].messages if "新しい手順" in m.text)
    assert "/workspace/master/skills/" in first and "master.csv" in first
    assert "No one can answer questions" in first
    assert "invoice.pdf" in first


@pytest.mark.django_db
def test_checks_and_judge_finish_the_evaluation(app_user, model, monkeypatch):
    skill = skill_with_draft(app_user)
    target = case(skill, expected_result={"ok": True}, criteria="照合の誤りがない")
    evaluation = start_evaluation(skill, cases=[target], actor=app_user, model_version=model.current_version)
    result = evaluation.results.get()
    execute(result.base_run, submit(False, "旧版の回答"))
    progress_evaluation(evaluation.pk)
    assert SkillEvaluation.objects.get(pk=evaluation.pk).status == EvaluationStatus.RUNNING  # Draftの側がまだ

    execute(result.draft_run, submit(True, "新版の回答"))
    adapter = judge(monkeypatch)
    progress_evaluation(evaluation.pk)

    evaluation.refresh_from_db()
    result.refresh_from_db()
    assert evaluation.status == EvaluationStatus.FINISHED
    assert result.state == EvaluationResultState.DONE
    assert result.passed is True
    assert result.draft_check["reply"] == "新版の回答"
    assert result.base_check["passed"] is False
    assert result.base_check["mismatches"] == [{"path": "ok", "expected": True, "actual": False}]
    assert (result.verdict, result.judge_reason) == (EvaluationVerdict.BETTER, "Draftの方が正確です。")
    request = adapter.requests[0]
    assert request.forced_tool == "report_verdict"
    assert "照合の誤りがない" in request.messages[1].text
    assert "旧版の回答" in request.messages[1].text and "新版の回答" in request.messages[1].text
    # 判定の利用量はDraftの側のRunに記録する
    assert UsageLedger.objects.filter(run=result.draft_run, purpose=UsagePurpose.EVALUATION_JUDGE).count() == 1
    # 何度呼んでも判定し直さない
    progress_evaluation(evaluation.pk)
    assert len(adapter.requests) == 1


@pytest.mark.django_db
def test_failed_draft_run_is_not_passed_and_not_judged(app_user, model, monkeypatch):
    skill = skill_with_draft(app_user)
    evaluation = start_evaluation(skill, cases=[case(skill)], actor=app_user, model_version=model.current_version)
    result = evaluation.results.get()
    execute(result.base_run, submit(True))
    # 業務結果を確定せずに終えようとし、促されても確定しない
    execute(result.draft_run, FakeAdapter([reply("できました"), reply("できました")]))
    adapter = judge(monkeypatch)
    progress_evaluation(evaluation.pk)

    result.refresh_from_db()
    assert result.passed is False
    assert result.draft_check["completed"] is False
    assert result.verdict is None
    assert "完了しなかった" in result.judge_note
    assert adapter.requests == []


@pytest.mark.django_db
def test_first_version_runs_only_the_draft(app_user, model, monkeypatch):
    skill = SkillFactory.create(created_by=app_user)
    SkillDraftFactory.create(skill=skill)
    evaluation = start_evaluation(skill, cases=[case(skill)], actor=app_user, model_version=model.current_version)
    result = evaluation.results.get()
    assert result.base_run is None
    execute(result.draft_run, FakeAdapter([reply("照合しました。")]))
    progress_evaluation(evaluation.pk)
    result.refresh_from_db()
    assert result.passed is True
    assert "公開中の版がない" in result.judge_note


def test_expected_result_matches_only_the_written_fields():
    assert match_expected({"ok": True}, {"ok": True, "note": "x"}) == []
    assert match_expected({"a": {"b": 1}}, {"a": {"b": 2, "c": 3}}) == [{"path": "a.b", "expected": 1, "actual": 2}]
    assert match_expected({"rows": [1, 2]}, {"rows": [1]}) == [{"path": "rows", "expected": [1, 2], "actual": [1]}]
    assert match_expected({"x": 1}, {}) == [{"path": "x", "expected": 1, "actual": None, "missing": True}]
    assert match_expected({"x": 1}, None) == [{"path": "$", "expected": {"x": 1}, "actual": None}]


@pytest.mark.django_db
def test_evaluation_cannot_start_twice_or_for_a_suspended_skill(app_user, model):
    skill = skill_with_draft(app_user)
    target = case(skill)
    start_evaluation(skill, cases=[target], actor=app_user, model_version=model.current_version)
    with pytest.raises(CannotEvaluate, match="評価中"):
        start_evaluation(skill, cases=[target], actor=app_user, model_version=model.current_version)

    suspended = skill_with_draft(app_user)
    Skill.objects.filter(pk=suspended.pk).update(suspended=True)
    suspended.refresh_from_db()
    with pytest.raises(CannotEvaluate, match="停止中"):
        start_evaluation(suspended, cases=[case(suspended)], actor=app_user, model_version=model.current_version)


@pytest.mark.django_db
def test_sensitive_skill_is_evaluated_with_a_local_model_as_confidential_work(app_user, model):
    """機密のSkillは、ローカルLLMのモデルで、機密のRunとして評価する（design.md §3.7.1）"""
    skill = skill_with_draft(app_user, is_sensitive=True)
    target = case(skill)
    with pytest.raises(CannotEvaluate, match="ローカルLLM"):
        start_evaluation(skill, cases=[target], actor=app_user, model_version=model.current_version)
    local = ModelProfileFactory.create(
        version__connection=LLMConnectionFactory.create(kind=ProviderKind.LOCAL), version__supports_tools=True
    )
    assert local.current_version is not None
    evaluation = start_evaluation(skill, cases=[target], actor=app_user, model_version=local.current_version)
    [result] = evaluation.results.all()
    for run in (result.base_run, result.draft_run):
        assert run is not None
        assert run.handles_sensitive_data and run.manifest["fixed"]["handles_sensitive_data"]
        assert run.conversation.handles_sensitive_data


@pytest.mark.django_db
def test_cancel_stops_the_runs_and_closes_the_cases(app_user, model):
    skill = skill_with_draft(app_user)
    evaluation = start_evaluation(skill, cases=[case(skill)], actor=app_user, model_version=model.current_version)
    cancel_evaluation(evaluation.pk, actor=app_user)
    evaluation.refresh_from_db()
    result = evaluation.results.get()
    assert evaluation.status == EvaluationStatus.CANCELLED
    assert result.state == EvaluationResultState.DONE and result.passed is False
    assert result.base_run is not None and result.base_run.status == RunStatus.CANCELLED


@pytest.mark.django_db
def test_cancel_during_the_judge_keeps_the_case_closed(app_user, model, monkeypatch):
    skill = skill_with_draft(app_user)
    evaluation = start_evaluation(skill, cases=[case(skill)], actor=app_user, model_version=model.current_version)
    result = evaluation.results.get()
    execute(result.base_run, submit(True))
    execute(result.draft_run, submit(True))

    def judge_after_cancel(*_args):
        cancel_evaluation(evaluation.pk)  # 判定の間に止められた
        return EvaluationVerdict.SAME, "同じ", ""

    monkeypatch.setattr(evaluation_module, "_judge", judge_after_cancel)
    progress_evaluation(evaluation.pk)
    result.refresh_from_db()
    evaluation.refresh_from_db()
    assert evaluation.status == EvaluationStatus.CANCELLED
    assert result.passed is False and result.verdict is None
    assert result.judge_note == "評価を止めました。"


@pytest.mark.django_db
def test_evaluation_conversations_are_not_listed(api_client, app_user, model):
    ConversationFactory.create(owner=app_user)
    skill = skill_with_draft(app_user)
    start_evaluation(skill, cases=[case(skill)], actor=app_user, model_version=model.current_version)
    assert Conversation.objects.filter(owner=app_user, purpose=ConversationPurpose.EVALUATION).count() == 2
    api_client.force_login(app_user)
    assert len(api_client.get("/api/app/v1/conversations/").json()["results"]) == 1


# --- 公開の条件 ---


def _finish(evaluation: SkillEvaluation, monkeypatch) -> None:
    for result in evaluation.results.all():
        for run in (result.base_run, result.draft_run):
            if run is not None:
                execute(run, submit(True))
    judge(monkeypatch, verdict="same")
    progress_evaluation(evaluation.pk)


@pytest.mark.django_db
def test_required_case_must_pass_with_the_current_draft_before_publishing(api_client, app_user, model, monkeypatch):
    department = DepartmentFactory.create()
    DepartmentMembershipFactory.create(department=department, user=app_user)
    grant(app_user, AppPermission.PUBLISH_DEPARTMENT_SKILLS)
    skill = skill_with_draft(app_user, scope=SkillScope.DEPARTMENT, department=department)
    required = case(skill, required=True)
    case(skill, name="任意のケース")
    draft = SkillDraft.objects.get(skill=skill)
    api_client.force_login(app_user)
    body = {"draft": draft.uid, "revision": draft.revision}

    res = api_client.post(f"{SKILLS}{skill.uid}/publish/", body, format="json")
    assert res.status_code == 400
    assert "必須の評価ケース" in res.json()["non_field_errors"][0]
    shown = api_client.get(f"{SKILLS}{skill.uid}/drafts/{draft.uid}/").json()["unevaluated_cases"]
    assert shown == [{"id": required.uid, "name": required.name}]

    evaluation = start_evaluation(skill, cases=[required], actor=app_user, model_version=model.current_version)
    _finish(evaluation, monkeypatch)
    assert draft.unevaluated_cases() == []

    # 評価の後にケースを変えたら、評価し直すまで満たさない
    res = api_client.patch(
        f"{SKILLS}{skill.uid}/evaluation_cases/{required.uid}/", {"prompt": "別の依頼"}, format="json"
    )
    assert res.status_code == 200, res.json()
    assert [c.pk for c in draft.unevaluated_cases()] == [required.pk]
    evaluation = start_evaluation(skill, cases=[required], actor=app_user, model_version=model.current_version)
    _finish(evaluation, monkeypatch)

    # 後の評価で失敗したら、前の通過では満たさない
    evaluation = start_evaluation(skill, cases=[required], actor=app_user, model_version=model.current_version)
    for run in (r for result in evaluation.results.all() for r in (result.base_run, result.draft_run)):
        execute(run, FakeAdapter([reply("できました"), reply("できました")]))
    progress_evaluation(evaluation.pk)
    assert [c.pk for c in draft.unevaluated_cases()] == [required.pk]
    evaluation = start_evaluation(skill, cases=[required], actor=app_user, model_version=model.current_version)
    _finish(evaluation, monkeypatch)
    assert draft.unevaluated_cases() == []

    # Draftを変えたら（revisionが進んだら）、評価し直すまで満たさない
    draft.revise(actor=app_user, expected_revision=1, instructions="1. さらに新しい手順")
    res = api_client.post(f"{SKILLS}{skill.uid}/publish/", {**body, "revision": 2}, format="json")
    assert res.status_code == 400
    evaluation = start_evaluation(skill, cases=[required], actor=app_user, model_version=model.current_version)
    _finish(evaluation, monkeypatch)
    res = api_client.post(f"{SKILLS}{skill.uid}/publish/", {**body, "revision": 2}, format="json")
    assert res.status_code == 201, res.json()


@pytest.mark.django_db
def test_personal_skill_has_no_required_cases(api_client, app_user):
    skill = skill_with_draft(app_user)
    api_client.force_login(app_user)
    res = api_client.post(
        f"{SKILLS}{skill.uid}/evaluation_cases/", {"name": "a", "prompt": "依頼", "required": True}, format="json"
    )
    assert res.status_code == 400
    assert "required" in res.json()


@pytest.mark.django_db
def test_editor_cannot_loosen_required_cases(api_client, app_user, other_user):
    department = DepartmentFactory.create()
    DepartmentMembershipFactory.create(department=department, user=app_user)
    grant(app_user, AppPermission.EDIT_DEPARTMENT_SKILLS)
    skill = skill_with_draft(other_user, scope=SkillScope.DEPARTMENT, department=department)
    required = case(skill, required=True)
    optional = case(skill, name="任意")
    api_client.force_login(app_user)
    url = f"{SKILLS}{skill.uid}/evaluation_cases/"

    assert api_client.post(url, {"name": "b", "prompt": "依頼", "required": True}, format="json").status_code == 403
    assert api_client.patch(f"{url}{required.uid}/", {"prompt": "緩い依頼"}, format="json").status_code == 403
    assert api_client.delete(f"{url}{required.uid}/").status_code == 403
    assert api_client.patch(f"{url}{optional.uid}/", {"required": True}, format="json").status_code == 403
    # 必須でないケースは編集の権限で作り・変え・消せる
    res = api_client.post(url, {"name": "c", "prompt": "依頼"}, format="json")
    assert res.status_code == 201, res.json()
    assert api_client.patch(f"{url}{optional.uid}/", {"criteria": "観点"}, format="json").status_code == 200
    assert api_client.delete(f"{url}{optional.uid}/").status_code == 204


@pytest.mark.django_db
def test_case_files_are_uploaded_and_removed(api_client, app_user):
    from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: PLC0415

    skill = skill_with_draft(app_user)
    api_client.force_login(app_user)
    url = f"{SKILLS}{skill.uid}/evaluation_cases/"
    res = api_client.post(
        url,
        {
            "name": "添付あり",
            "prompt": "依頼",
            "expected_result": '{"ok": true}',
            "add_files": [SimpleUploadedFile("a.txt", b"A"), SimpleUploadedFile("b.txt", b"B")],
        },
        format="multipart",
    )
    assert res.status_code == 201, res.json()
    body = res.json()
    assert body["expected_result"] == {"ok": True}
    assert sorted(f["name"] for f in body["files_info"]) == ["a.txt", "b.txt"]
    removed = next(f["id"] for f in body["files_info"] if f["name"] == "a.txt")
    res = api_client.patch(f"{url}{body['id']}/", {"remove_files": [removed]}, format="multipart")
    assert res.status_code == 200, res.json()
    assert [f["name"] for f in res.json()["files_info"]] == ["b.txt"]
    kept = res.json()["files_info"][0]["id"]
    assert api_client.get(f"{SKILLS}{skill.uid}/files/{kept}/download/").status_code == 200


@pytest.mark.django_db
def test_evaluation_api_starts_lists_and_cancels(api_client, app_user, model):
    skill = skill_with_draft(app_user)
    target = case(skill)
    api_client.force_login(app_user)
    url = f"{SKILLS}{skill.uid}/evaluations/"
    res = api_client.post(url, {"cases": [target.uid], "model_profile": model.uid}, format="json")
    assert res.status_code == 201, res.json()
    body = res.json()
    assert body["current"] is True
    assert body["total"] == 1 and body["results"][0]["draft_status"] == "順番待ち"
    assert api_client.get(url).json()["results"][0]["id"] == body["id"]
    res = api_client.post(f"{url}{body['id']}/cancel/")
    assert res.status_code == 200
    assert res.json()["status"] == EvaluationStatus.CANCELLED


# --- test_skill_draft ---


def _edit_run(conversation, skill: Skill) -> Run:
    post_message(
        conversation,
        body="手順を直して評価して",
        client_message_id="",
        mode=MessageMode.AUTO,
        actor=conversation.owner,
        skill_edit={"id": skill.uid, "name": skill.name},
    )
    return Run.objects.filter(conversation=conversation).latest("pk")


@pytest.mark.django_db
def test_test_skill_draft_waits_for_the_evaluation_and_resumes(app_user, monkeypatch):
    skill = skill_with_draft(app_user)
    case(skill, required=False)
    conversation = ConversationFactory.create(owner=app_user)
    run = _edit_run(conversation, skill)
    run = execute(run, FakeAdapter([calls(("t1", "test_skill_draft", {}))]))

    assert run.status == RunStatus.WAITING_USER
    clarification = ClarificationRequest.objects.get(run=run)
    assert clarification.kind == ClarificationKind.EVALUATION
    evaluation = SkillEvaluation.objects.get(run=run)
    assert clarification.context["evaluation"] == evaluation.uid
    assert evaluation.tool_call_id == "t1"

    _finish(evaluation, monkeypatch)
    run.refresh_from_db()
    clarification.refresh_from_db()
    assert run.status == RunStatus.QUEUED
    assert clarification.status == ClarificationStatus.ANSWERED
    result = RunEvent.objects.filter(run=run, kind=RunEventKind.TOOL_RESULT, data__name="test_skill_draft").get()
    text = text_of(result.data)
    assert "1 of 1 cases passed" in text
    assert '"通常の請求書": passed' in text
    assert "about the same" in text
    assert result.data["value"]["passed"] == 1

    adapter = FakeAdapter([reply("評価は通りました。")])
    assert execute(run, adapter).status == RunStatus.COMPLETED


@pytest.mark.django_db
def test_evaluation_finished_before_the_wait_is_delivered_after_commit(
    app_user, monkeypatch, django_capture_on_commit_callbacks
):
    skill = skill_with_draft(app_user)
    case(skill)
    conversation = ConversationFactory.create(owner=app_user)
    run = _edit_run(conversation, skill)
    started = evaluation_module.start_evaluation

    def start_and_finish(*args, **kwargs):
        # 編集のRunが確認待ちになる前に評価が終わった場合
        evaluation = started(*args, **kwargs)
        _finish(evaluation, monkeypatch)
        return evaluation

    monkeypatch.setattr(evaluation_module, "start_evaluation", start_and_finish)
    monkeypatch.setattr(evaluation_module, "async_task", lambda *args, **kwargs: None)
    with django_capture_on_commit_callbacks(execute=True):
        run = execute(run, FakeAdapter([calls(("t1", "test_skill_draft", {}))]))
    run.refresh_from_db()
    # 確認待ちにした直後のcommitで結果を返し、再開を待つ
    assert run.status == RunStatus.QUEUED
    assert ClarificationRequest.objects.get(run=run).status == ClarificationStatus.ANSWERED


@pytest.mark.django_db
def test_test_skill_draft_without_cases_tells_the_user(app_user):
    skill = skill_with_draft(app_user)
    conversation = ConversationFactory.create(owner=app_user)
    run = _edit_run(conversation, skill)
    run = execute(run, FakeAdapter([calls(("t1", "test_skill_draft", {})), reply("ケースがありません。")]))
    assert run.status == RunStatus.COMPLETED
    result = RunEvent.objects.filter(run=run, kind=RunEventKind.TOOL_RESULT, data__name="test_skill_draft").get()
    assert result.data["is_error"] and "no test cases" in text_of(result.data)
    assert not SkillEvaluation.objects.exists()


@pytest.mark.django_db
def test_publish_tool_asks_to_test_required_cases(app_user):
    department = DepartmentFactory.create()
    DepartmentMembershipFactory.create(department=department, user=app_user)
    grant(app_user, AppPermission.PUBLISH_DEPARTMENT_SKILLS)
    skill = skill_with_draft(app_user, scope=SkillScope.DEPARTMENT, department=department)
    case(skill, required=True)
    conversation = ConversationFactory.create(owner=app_user)
    run = _edit_run(conversation, skill)
    publish = ("p1", "publish_skill_draft", {"expected_revision": 1, "change_reason": "手順の修正"})
    run = execute(run, FakeAdapter([calls(publish), reply("公開できませんでした。")]))
    result = RunEvent.objects.filter(run=run, kind=RunEventKind.TOOL_RESULT, data__name="publish_skill_draft").get()
    assert result.data["is_error"]
    assert "test_skill_draft" in text_of(result.data)


@pytest.mark.django_db
def test_stopping_the_edit_run_stops_its_evaluation(app_user):
    skill = skill_with_draft(app_user)
    case(skill)
    conversation = ConversationFactory.create(owner=app_user)
    run = execute(_edit_run(conversation, skill), FakeAdapter([calls(("t1", "test_skill_draft", {}))]))
    evaluation = SkillEvaluation.objects.get(run=run)
    run.request_cancel("止める", actor=app_user)
    cancel_evaluation(evaluation.pk)  # on_run_finished() がcommitの後にQ2で呼ぶもの
    evaluation.refresh_from_db()
    assert evaluation.status == EvaluationStatus.CANCELLED
    assert ClarificationRequest.objects.get(run=run).status == ClarificationStatus.CANCELLED


@pytest.mark.django_db
def test_replayed_call_waits_for_the_running_evaluation(app_user):
    skill = skill_with_draft(app_user)
    case(skill)
    conversation = ConversationFactory.create(owner=app_user)
    run = _edit_run(conversation, skill)
    model_version = run.model_version
    evaluation = start_evaluation(
        skill, cases=list(skill.evaluation_cases.all()), actor=app_user, model_version=model_version, run=run
    )
    run = execute(run, FakeAdapter([calls(("t2", "test_skill_draft", {}))]))
    assert SkillEvaluation.objects.filter(run=run).count() == 1
    evaluation.refresh_from_db()
    assert evaluation.tool_call_id == "t2"
    assert ClarificationRequest.objects.get(run=run).context["evaluation"] == evaluation.uid


@pytest.mark.django_db
def test_replayed_call_reuses_an_evaluation_that_finished_meanwhile(app_user, monkeypatch):
    skill = skill_with_draft(app_user)
    case(skill)
    conversation = ConversationFactory.create(owner=app_user)
    run = _edit_run(conversation, skill)
    evaluation = start_evaluation(
        skill, cases=list(skill.evaluation_cases.all()), actor=app_user, model_version=run.model_version, run=run
    )
    _finish(evaluation, monkeypatch)
    run = execute(run, FakeAdapter([calls(("t2", "test_skill_draft", {}))]))
    assert SkillEvaluation.objects.filter(run=run).count() == 1
    assert ClarificationRequest.objects.get(run=run).context["evaluation"] == evaluation.uid


@pytest.mark.django_db
def test_user_cannot_answer_the_evaluation_wait(api_client, app_user):
    skill = skill_with_draft(app_user)
    case(skill)
    conversation = ConversationFactory.create(owner=app_user)
    run = execute(_edit_run(conversation, skill), FakeAdapter([calls(("t1", "test_skill_draft", {}))]))
    clarification = ClarificationRequest.objects.get(run=run)
    api_client.force_login(app_user)
    url = f"/api/app/v1/runs/{run.uid}/clarifications/{clarification.uid}/"
    shown = api_client.get(url).json()
    assert shown["target"]["evaluation"] == SkillEvaluation.objects.get(run=run).uid
    assert shown["target"]["cases"] == 1
    assert api_client.post(f"{url}answer/", {"comment": "まだ？"}, format="json").status_code == 400
