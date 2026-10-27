"""Draftの評価（design.md §3.7.1、harness.md §10.9）。

評価を始めた時点のDraftの中身を SkillEvaluation に写し、選んだケースごとに、公開中の版とその中身で評価のRunを
1つずつ動かす。評価のRunは答える人がいないRunで、ケースの依頼の文と添付を最初の発言にする。両方のRunが
終わったら、機械的な検証（完了・業務結果・期待値）とLLMの判定（参考）を記録し、すべてのケースが終われば評価を
終える。編集のRunの `test_skill_draft` で始めた評価は、終わったら結果を返して編集のRunを再開する。
"""

import contextlib
import json
import logging
from datetime import timedelta
from functools import partial
from typing import Any

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext as _
from django_q.tasks import async_task

from artifact.models import Artifact, ArtifactKind
from harness.connectors import build_catalog as build_connector_catalog
from harness.skills import evaluation_entry, manifest_entry
from llm.models import ModelProfileVersion
from llm.providers import get_adapter
from llm.providers.base import ChatRequest, Completed, Message, ProviderError, ToolCallRequested, ToolDefinition, Usage
from run.dispatch import enqueue_run
from run.errors import AlreadyAnswered, InvalidTransition
from run.models import (
    TERMINAL_STATUSES,
    ClarificationKind,
    ClarificationRequest,
    ClarificationStatus,
    Run,
    RunEvent,
    RunEventKind,
    RunKind,
    RunStatus,
    RunTrigger,
)
from runtime.models import RuntimeProfile
from skill import connectors as skill_connectors
from skill.errors import CannotEvaluate
from skill.models import (
    MAX_CASES_PER_EVALUATION,
    EvaluationResultState,
    EvaluationStatus,
    EvaluationVerdict,
    Skill,
    SkillAction,
    SkillDraft,
    SkillEvaluation,
    SkillEvaluationAsset,
    SkillEvaluationCase,
    SkillEvaluationResult,
)
from usage.models import UsageLedger, UsagePurpose

from .dispatch import user_message_data
from .models import Conversation, ConversationMessage, ConversationPurpose, MessageRole

logger = logging.getLogger(__name__)

# 評価のRunの最初の発言に添える。答える人がいないので、確認の代わりに仮定を置いて進めさせる
NO_RESPONDER_NOTE = (
    "[This is a test run of a skill. No one can answer questions. If something is unclear, make a reasonable "
    "assumption, continue, and state the assumption in your final reply.]"
)
# 判定を始めてから、これだけ経っても終わらなければ、判定をやり直す（判定の処理が落ちた場合）
JUDGE_TIMEOUT = timedelta(minutes=10)
# 判定に渡す最後の応答と業務結果の長さ（文字）
JUDGE_TEXT_LIMIT = 6000
JUDGE_TOOL = "report_verdict"
JUDGE_SYSTEM = """You compare two runs of the same business request. Run A used the published version of a skill; \
run B used a draft (a proposed change). Judge whether B is better, the same, or worse than A for the request and \
the given points to check. Judge the content of the work, not the wording. "same" means no difference that \
matters to the person who asked. Call report_verdict once. Write the reason in Japanese, in 1 to 3 sentences."""
JUDGE_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["better", "same", "worse"]},
        "reason": {"type": "string", "minLength": 1, "maxLength": 1000},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}
VERDICTS = {"better": EvaluationVerdict.BETTER, "same": EvaluationVerdict.SAME, "worse": EvaluationVerdict.WORSE}


# --- 開始 ---


def start_evaluation(
    skill: Skill,
    *,
    cases: list[SkillEvaluationCase],
    actor,
    model_version: ModelProfileVersion,
    effort: str = "",
    run: Run | None = None,
    tool_call_id: str = "",
) -> SkillEvaluation:
    """今のDraftを評価する。始められなければ CannotEvaluate。

    `run`・`tool_call_id` は test_skill_draft で始めたときの編集のRunと呼出し（終わったら結果を返す）
    """
    with transaction.atomic():
        skill = Skill.objects.select_for_update(of=("self",)).select_related("current_version").get(pk=skill.pk)
        # ケースは読み直して、今の中身と更新日時を写す（公開の条件の照合に使う）
        # ケースの変更も行をロックして行うので、本文と添付を同じ時点のものにする
        found = SkillEvaluationCase.objects.select_for_update().in_bulk([c.pk for c in cases])
        cases = [found[c.pk] for c in cases if c.pk in found]
        _check_startable(skill, cases, actor, model_version)
        # 資料の変更はDraftの行をロックして行うので、写し終えるまでDraftの行をロックする（本文と資料を混ぜない）
        draft = SkillDraft.objects.select_for_update().filter(skill=skill).prefetch_related("assets").first()
        if draft is None:
            raise CannotEvaluate(_("評価するDraftがありません。"))
        if reason := draft.unsupported_by(model_version):
            raise CannotEvaluate(reason)
        # 機密のSkill（公開中の版かDraft）は、ローカルLLMのモデルで、機密のRunとして評価する（design.md §3.7.1）
        sensitive = skill_connectors.skill_is_sensitive(skill)
        if sensitive and not model_version.is_local:
            raise CannotEvaluate(_("機密のSkillは、ローカルLLMのモデルで評価してください。"))
        if skill_connectors.declares_system_connector(draft):
            # システムのConnectorは会話のRunから使えないので、評価のRunでも使えない（システムのタスクの段階で扱う）
            raise CannotEvaluate(_("システムのConnectorを使うSkillは、まだ評価できません。"))
        if reason := skill_connectors.unusable_reason(draft, actor, sensitive=sensitive):
            raise CannotEvaluate(reason)
        # Skillの範囲に合わないConnector（部門のSkillに別の部門のもの等）は、評価のRunでも読ませない。評価の結果は
        # そのSkillを編集できる人が見るので（公開と同じ範囲の検証。design.md §4.9.1）
        if skill_connectors.out_of_scope(draft, skill):
            raise CannotEvaluate(_("このSkillの範囲では使えないConnectorを宣言しています。"))
        # 公開中の版を評価する人が動かせなければ（使えなくなったConnector、システムのConnector、Skillの範囲に
        # 合わなくなったConnectorを宣言している）、最初の公開の前と同じくDraftだけで評価する（その宣言を外した
        # Draftを評価して公開できるように）
        base = skill.current_version
        if base is not None and (
            skill_connectors.declares_system_connector(base)
            or skill_connectors.unusable_reason(base, actor, sensitive=sensitive)
            or skill_connectors.out_of_scope(base, skill)
        ):
            base = None
        runtime_version = RuntimeProfile.default_version()
        if runtime_version is None:
            raise CannotEvaluate(
                _("コマンドを動かす環境（Runtime Profile）が登録されていません。基盤管理者に問い合わせてください。")
            )
        if effort not in model_version.effort_values:
            effort = model_version.default_effort
        evaluation = SkillEvaluation.objects.create(
            skill=skill,
            draft_uid=draft.uid,
            draft_revision=draft.revision,
            base_version=base,
            model_version=model_version,
            effort=effort,
            requested_by=actor,
            run=run,
            tool_call_id=tool_call_id,
            **draft.content(),
        )
        SkillEvaluationAsset.objects.bulk_create(
            SkillEvaluationAsset(
                evaluation=evaluation,
                key=a.key,
                role=a.role,
                description=a.description,
                resource_version_id=a.resource_version_id,
            )
            for a in draft.assets.all()
        )
        for order, case in enumerate(cases, start=1):
            files = list(case.files.select_related("blob").order_by("pk"))
            result = SkillEvaluationResult.objects.create(
                evaluation=evaluation,
                case=case,
                order=order,
                case_name=case.name,
                required=case.required,
                case_data={
                    "prompt": case.prompt,
                    "criteria": case.criteria,
                    "expected_result": case.expected_result,
                    "files": [{"id": f.uid, "name": f.name} for f in files],
                },
                case_updated=case.updated,
            )
            fields = []
            if base is not None:
                result.base_run = _start_run(evaluation, case, files, runtime_version, "base", sensitive=sensitive)
                fields.append("base_run")
            result.draft_run = _start_run(evaluation, case, files, runtime_version, "draft", sensitive=sensitive)
            result.save(update_fields=[*fields, "draft_run"])
        return evaluation


def _check_startable(skill: Skill, cases: list[SkillEvaluationCase], actor, model_version: ModelProfileVersion) -> None:
    if skill.deleted:
        raise CannotEvaluate(_("このSkillは削除されました。"))
    if not skill.can(actor, SkillAction.EDIT):
        raise CannotEvaluate(_("このSkillのDraftを評価する権限がありません。"))
    if skill.suspended:
        raise CannotEvaluate(_("停止中のSkillは評価できません。"))
    if not cases:
        raise CannotEvaluate(_("評価ケースを選んでください。"))
    if len(cases) > MAX_CASES_PER_EVALUATION:
        raise CannotEvaluate(_("1回に評価できるケースは%(limit)s件までです。") % {"limit": MAX_CASES_PER_EVALUATION})
    if any(c.skill_id != skill.pk for c in cases):
        raise CannotEvaluate(_("別のSkillの評価ケースは使えません。"))
    if SkillEvaluation.objects.filter(skill=skill, status=EvaluationStatus.RUNNING).exists():
        raise CannotEvaluate(_("このSkillは評価中です。終わってから、もう一度評価してください。"))
    profile = model_version.profile
    if not profile.is_active or profile.deleted:
        raise CannotEvaluate(_("このモデル設定は使えません。別のモデルを選んでください。"))


def _start_run(
    evaluation: SkillEvaluation, case: SkillEvaluationCase, files: list, runtime_version, side: str, *, sensitive: bool
) -> Run:
    """ケースの依頼の文と添付を最初の発言にして、評価のRunを1つ始める。Runごとに一覧に出さない会話を作る"""
    actor = evaluation.requested_by
    version = evaluation.model_version
    skill = evaluation.skill
    base = evaluation.base_version
    entry = evaluation_entry(evaluation) if side == "draft" or base is None else manifest_entry(skill, base)
    content = evaluation if side == "draft" or base is None else base
    conversation = Conversation.objects.create(
        owner=actor,
        model_profile=version.profile,
        effort=evaluation.effort,
        purpose=ConversationPurpose.EVALUATION,
        handles_sensitive_data=sensitive,
        title=f"{evaluation.skill.name}：{case.name}"[:200],
        last_posted=timezone.now(),
    )
    message = ConversationMessage.objects.create(
        conversation=conversation, sender=actor, role=MessageRole.USER, body=case.prompt
    )
    if files:
        attachments = [
            Artifact.objects.create(
                owner=actor,
                conversation=conversation,
                blob=f.blob,
                kind=ArtifactKind.ATTACHMENT,
                name=f.name,
                content_type=f.content_type,
                expires=Artifact.default_expires(),
            )
            for f in files
        ]
        Artifact.attach(conversation, attachments)
        message.attachments.set(attachments)
    # 評価を依頼した人が使えるConnector。宣言したものを先に並べる
    connector_catalog = (
        build_connector_catalog(
            actor,
            sensitive=sensitive,
            explicit=[{"connector": uid} for uid in skill_connectors.declared_uids(content)],
        )
        if version.supports_tools
        else None
    )
    run = Run.objects.create(
        conversation=conversation,
        owner=actor,
        trigger=RunTrigger.EVALUATION,
        kind=RunKind.EVALUATION,
        model_version=version,
        effort=evaluation.effort,
        runtime_version=runtime_version,
        handles_sensitive_data=sensitive,
        manifest={
            "fixed": {
                "model_version": version.pk,
                "effort": evaluation.effort,
                "runtime_version": runtime_version.pk,
                "handles_sensitive_data": sensitive,
                "skills": [entry],
                "skill_catalog": None,
                # 評価を依頼した人が使えるConnector。Draftの「使ってよいTool」にConnectorの操作があれば使う
                "connectors": [],
                "connector_catalog": connector_catalog,
                "evaluation": {"evaluation": evaluation.uid, "side": side},
            },
            "appended": [],
        },
    )
    message.run = run
    message.save(update_fields=["run"])
    RunEvent.record(run, RunEventKind.STATUS, {"status": RunStatus.QUEUED}, actor=actor)
    text = f"{case.prompt}\n\n{NO_RESPONDER_NOTE}"
    RunEvent.record(
        run,
        RunEventKind.USER_MESSAGE,
        user_message_data(message, text, skills=[entry], connector_catalog=connector_catalog),
        actor=actor,
    )
    enqueue_run(run)
    return run


# --- 進行 ---


def on_run_finished(run: Run) -> None:
    """Runが終わったとき（Run.set_status() のtransactionの中）に呼ぶ。評価のRunなら評価を進め、編集のRunなら
    待っていた評価を止める。どちらもcommitした後にQ2で行う（ロックの順序を崩さないため）"""
    if run.kind == RunKind.EVALUATION:
        evaluation_uid = ((run.manifest or {}).get("fixed") or {}).get("evaluation", {}).get("evaluation")
        evaluation_id = SkillEvaluation.objects.filter(uid=evaluation_uid).values_list("pk", flat=True).first()
        if evaluation_id is not None:
            transaction.on_commit(partial(async_task, "conversation.evaluation.progress_evaluation", evaluation_id))
    elif run.kind == RunKind.SKILL_EDIT:
        ids = list(
            SkillEvaluation.objects.filter(run=run, status=EvaluationStatus.RUNNING).values_list("pk", flat=True)
        )
        for evaluation_id in ids:
            transaction.on_commit(partial(async_task, "conversation.evaluation.cancel_evaluation", evaluation_id))


def progress_evaluation(evaluation_id: int) -> None:
    """両方のRunが終わったケースを検証・判定し、すべてのケースが終われば評価を終える。何度呼んでもよい（Q2）"""
    evaluation = SkillEvaluation.objects.select_related("model_version__connection").get(pk=evaluation_id)
    if evaluation.status == EvaluationStatus.RUNNING:
        results = evaluation.results.select_related("base_run", "draft_run").filter(state=EvaluationResultState.RUNNING)
        for result in results:
            runs = [r for r in (result.base_run, result.draft_run) if r is not None]
            if all(r.status in TERMINAL_STATUSES for r in runs) and _claim(result):
                _check_and_judge(evaluation, result)
        if not evaluation.results.exclude(state=EvaluationResultState.DONE).exists():
            _finish(evaluation, EvaluationStatus.FINISHED)
    deliver(evaluation.pk)


def _claim(result: SkillEvaluationResult) -> bool:
    """判定を始める（同時に呼ばれても、1つだけが判定する）"""
    claimed = SkillEvaluationResult.objects.filter(pk=result.pk, state=EvaluationResultState.RUNNING).update(
        state=EvaluationResultState.JUDGING, judging_started=timezone.now()
    )
    return claimed == 1


def _check_and_judge(evaluation: SkillEvaluation, result: SkillEvaluationResult) -> None:
    expected = result.case_data.get("expected_result")
    schema = evaluation.output_schema
    base_schema = evaluation.base_version.output_schema if evaluation.base_version else None
    result.draft_check = check_run(result.draft_run, schema=schema, expected=expected)
    result.base_check = check_run(result.base_run, schema=base_schema, expected=expected) if result.base_run else {}
    result.passed = bool(result.draft_check.get("passed"))
    result.verdict, result.judge_reason, result.judge_note = _judge(evaluation, result)
    # 判定の間に評価が止められていれば（cancel_evaluation() がケースを閉じた）、結果を上書きしない
    SkillEvaluationResult.objects.filter(
        pk=result.pk, state=EvaluationResultState.JUDGING, evaluation__status=EvaluationStatus.RUNNING
    ).update(
        draft_check=result.draft_check,
        base_check=result.base_check,
        passed=result.passed,
        verdict=result.verdict,
        judge_reason=result.judge_reason,
        judge_note=result.judge_note,
        state=EvaluationResultState.DONE,
    )


def check_run(run: Run | None, *, schema: dict[str, Any] | None, expected: Any) -> dict[str, Any]:
    """機械的な検証：Runが完了したか、業務結果のSchemaがあれば確定したか、期待する業務結果と一致したか"""
    if run is None:
        return {"completed": False, "passed": False}
    run.refresh_from_db()
    completed = run.status == RunStatus.COMPLETED
    submitted = run.result is not None
    mismatches = match_expected(expected, run.result) if expected is not None else []
    reply = (
        ConversationMessage.objects.filter(run=run, role=MessageRole.AGENT)
        .order_by("-pk")
        .values_list("body", flat=True)
    ).first() or ""
    cost = UsageLedger.objects.filter(run=run).aggregate(total=Sum("cost"))["total"]
    return {
        "completed": completed,
        "status_display": run.get_status_display(),
        "error": run.error,
        "result_required": bool(schema),
        "result_submitted": submitted,
        "expected_checked": expected is not None,
        "mismatches": mismatches,
        "passed": completed and (submitted or not schema) and not mismatches,
        "reply": reply,
        "result": run.result,
        "cost": str(cost) if cost is not None else "0",
    }


def match_expected(expected: Any, actual: Any, path: str = "") -> list[dict[str, Any]]:
    """期待する業務結果のうち、一致しない項目。オブジェクトは書いた項目だけを照合し、配列と値は全体で照合する"""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [{"path": path or "$", "expected": expected, "actual": actual}]
        mismatches = []
        for key, value in expected.items():
            child = f"{path}.{key}" if path else key
            if key not in actual:
                mismatches.append({"path": child, "expected": value, "actual": None, "missing": True})
            else:
                mismatches += match_expected(value, actual[key], child)
        return mismatches
    if expected != actual:
        return [{"path": path or "$", "expected": expected, "actual": actual}]
    return []


def _judge(evaluation: SkillEvaluation, result: SkillEvaluationResult) -> tuple[int | None, str, str]:
    """LLMの判定（参考。公開の条件には使わない）。判定しなければ (None, "", 理由)"""
    if result.base_run is None:
        return None, "", _("公開中の版がないので、比べていません。")
    if not (result.base_check.get("completed") and result.draft_check.get("completed")):
        return None, "", _("完了しなかったRunがあるので、判定していません。")
    version = evaluation.model_version
    if not version.supports_tools:
        return None, "", _("このモデルはToolを使えないので、判定していません。")
    request = ChatRequest(
        model=version.model_name,
        messages=[Message("system", JUDGE_SYSTEM), Message("user", _judge_input(result))],
        tools=[ToolDefinition(JUDGE_TOOL, "Report the verdict.", JUDGE_PARAMETERS)],
        max_output_tokens=version.max_output_tokens,
        effort=evaluation.effort or None,
        forced_tool=JUDGE_TOOL,
        reasoning=version.supports_reasoning,
    )
    call, usage, request_id = None, None, ""
    try:
        for event in get_adapter(version.connection).stream(request):
            match event:
                case ToolCallRequested(call=requested) if requested.name == JUDGE_TOOL and call is None:
                    call = requested
                case Usage():
                    usage = event
                case Completed(request_id=rid):
                    request_id = rid
    except ProviderError as exc:
        logger.warning("evaluation %s: judge failed: %s", evaluation.uid, exc.message)
        return None, "", _("判定に失敗しました：%(error)s") % {"error": exc.message}
    finally:
        if usage is not None and result.draft_run is not None:
            UsageLedger.record(
                run=result.draft_run,
                model_version=version,
                purpose=UsagePurpose.EVALUATION_JUDGE,
                usage=usage,
                request_id=request_id,
            )
    verdict = VERDICTS.get(str((call.arguments if call else {}).get("verdict", "")))
    reason = str((call.arguments if call else {}).get("reason", "")).strip()
    if verdict is None:
        return None, "", _("判定の結果を受け取れませんでした。")
    return verdict, reason[:1000], ""


def _judge_input(result: SkillEvaluationResult) -> str:
    data = result.case_data
    parts = [f"<request>\n{data.get('prompt', '')}\n</request>"]
    if files := data.get("files"):
        parts.append("<attachments>" + ", ".join(f["name"] for f in files) + "</attachments>")
    if criteria := (data.get("criteria") or "").strip():
        parts.append(f"<points_to_check>\n{criteria}\n</points_to_check>")
    for label, check in (("A", result.base_check), ("B", result.draft_check)):
        reply = (check.get("reply") or "")[:JUDGE_TEXT_LIMIT]
        parts.append(f'<run name="{label}">\n<final_reply>\n{reply}\n</final_reply>')
        if check.get("result") is not None:
            text = json.dumps(check["result"], ensure_ascii=False)[:JUDGE_TEXT_LIMIT]
            parts.append(f"<result>\n{text}\n</result>")
        parts.append("</run>")
    return "\n".join(parts)


def _finish(evaluation: SkillEvaluation, status: EvaluationStatus) -> None:
    SkillEvaluation.objects.filter(pk=evaluation.pk, status=EvaluationStatus.RUNNING).update(
        status=status, finished=timezone.now()
    )


# --- 停止 ---


def cancel_evaluation(evaluation_id: int, actor=None) -> None:
    """評価を止める。動いている評価のRunを止め、終わっていないケースは検証せずに閉じる"""
    evaluation = SkillEvaluation.objects.get(pk=evaluation_id)
    if evaluation.status != EvaluationStatus.RUNNING:
        return
    for result in evaluation.results.select_related("base_run", "draft_run"):
        for run in (result.base_run, result.draft_run):
            if run is not None and run.is_cancellable():
                try:
                    with transaction.atomic():
                        run.request_cancel(_("評価を止めました"), actor=actor)
                except InvalidTransition:
                    pass  # その間に終わった
    with transaction.atomic():
        current = SkillEvaluation.objects.select_for_update().get(pk=evaluation_id)
        if current.status != EvaluationStatus.RUNNING:
            return
        current.results.exclude(state=EvaluationResultState.DONE).update(
            state=EvaluationResultState.DONE, passed=False, judge_note=_("評価を止めました。")
        )
        _finish(current, EvaluationStatus.CANCELLED)
    deliver(evaluation_id)


# --- 編集のRunへの返却 ---


def deliver(evaluation_id: int) -> None:
    """test_skill_draft で始めた評価が終わっていれば、編集のRunの確認に結果を返して再開する（harness.md §10.9）。
    編集のRunが確認待ちになる前に評価が終わった場合は、確認を作るときに呼び直す（何度呼んでもよい）"""
    evaluation = SkillEvaluation.objects.filter(pk=evaluation_id).values("status", "run_id", "uid").first()
    if evaluation is None or evaluation["status"] == EvaluationStatus.RUNNING or evaluation["run_id"] is None:
        return
    clarification = ClarificationRequest.objects.filter(
        run_id=evaluation["run_id"],
        kind=ClarificationKind.EVALUATION,
        status=ClarificationStatus.OPEN,
        context__evaluation=evaluation["uid"],
    ).first()
    if clarification is None:
        return
    # 別の呼出しが先に返した、または編集のRunが止まったなら何もしない
    with contextlib.suppress(AlreadyAnswered):
        clarification.accept_answer([], "", None)


def reconcile_evaluations() -> None:
    """取りこぼした進行をやり直す（判定の処理が落ちた、通知が届かなかった）。Reconcilerから1分ごとに呼ぶ"""
    stale = timezone.now() - JUDGE_TIMEOUT
    SkillEvaluationResult.objects.filter(
        state=EvaluationResultState.JUDGING, judging_started__lt=stale, evaluation__status=EvaluationStatus.RUNNING
    ).update(state=EvaluationResultState.RUNNING)
    running = SkillEvaluation.objects.filter(
        status=EvaluationStatus.RUNNING, created__lt=timezone.now() - timedelta(minutes=1)
    )
    for evaluation in running.prefetch_related("results__base_run", "results__draft_run")[:100]:
        results = [r for r in evaluation.results.all() if r.state == EvaluationResultState.RUNNING]
        runs = [r for result in results for r in (result.base_run, result.draft_run) if r is not None]
        if all(r.status in TERMINAL_STATUSES for r in runs):
            async_task("conversation.evaluation.progress_evaluation", evaluation.pk)
    # 編集のRunが確認待ちになる前に終わった評価
    for evaluation_id in (
        SkillEvaluation.objects.filter(
            run__status=RunStatus.WAITING_USER,
            run__clarifications__kind=ClarificationKind.EVALUATION,
            run__clarifications__status=ClarificationStatus.OPEN,
        )
        .exclude(status=EvaluationStatus.RUNNING)
        .values_list("pk", flat=True)[:100]
    ):
        deliver(evaluation_id)
