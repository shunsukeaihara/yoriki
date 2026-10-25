"""Draftを評価する（harness.md §10.9、design.md §3.7.1）。Skillの編集のRunにだけ出す。

呼ぶと、編集の対象のSkillのDraftを評価ケースで評価し始め、Runは確認待ち（確認の種類「Draftの評価」）になる。
評価が終わると、サーバー（conversation/evaluation.deliver()）が結果をこの呼出しの結果として記録し、Runを再開する。
"""

from typing import TYPE_CHECKING, Any

from run.models import ClarificationKind
from skill.errors import CannotEvaluate
from skill.models import (
    MAX_CASES_PER_EVALUATION,
    EvaluationStatus,
    EvaluationVerdict,
    SkillEvaluation,
    SkillEvaluationCase,
)

from .base import Effect, Tool, ToolContext, ToolResult
from .skill_admin import NO_TARGET, _target

if TYPE_CHECKING:
    from run.models import ClarificationRequest

NAME = "test_skill_draft"
# 1つの編集のRunで評価できる回数（design.md §9。エージェントが評価と修正を繰り返す場合の上限）
MAX_EVALUATIONS_PER_RUN = 3
# 結果に載せるDraftの側の最後の応答の長さ（文字）
REPLY_EXCERPT = 600

DESCRIPTION = f"""Test the draft of the skill you are editing with the skill's test cases, and wait for the result.

Each case runs once with the published version and once with the draft, in separate test runs that no one answers \
(this takes several minutes). For each case, the result tells whether the draft passed the automatic checks \
(the work completed, the result was submitted if the skill has a result schema, the expected result matched), an AI \
judge's comparison with the published version (for reference), and an excerpt of the draft's final reply.

- Call it when the change is in the draft and before publish_skill_draft. A skill with required cases cannot be \
published until they pass with the current draft.
- People make the test cases on the skill's page; you cannot add them. If there are none, tell the user.
- Call this tool alone. You can call it up to {MAX_EVALUATIONS_PER_RUN} times in one work."""

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "cases": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 100},
            "maxItems": MAX_CASES_PER_EVALUATION,
            "description": "Names of the test cases to run. Omit to run all (required cases first, up to "
            f"{MAX_CASES_PER_EVALUATION}).",
        }
    },
    "additionalProperties": False,
}


def execute(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    """評価を始め、確認待ちにする値を返す。確認の作成と確認待ちへの遷移は、Runnerが同じtransactionで行う"""
    from conversation.evaluation import start_evaluation  # noqa: PLC0415  conversationがharnessをimportするため

    skill, error = _target(context)
    if skill is None:
        return error or ToolResult.error(NO_TARGET)
    run = context.run
    # 前の試みが評価を始めた後に中断した呼出しのやり直しなら、その評価の終わりを待つ
    running = SkillEvaluation.objects.filter(run=run, status=EvaluationStatus.RUNNING).first()
    if running is not None:
        SkillEvaluation.objects.filter(pk=running.pk).update(tool_call_id=context.call.id)
        return _waiting(running)
    if SkillEvaluation.objects.filter(run=run).count() >= MAX_EVALUATIONS_PER_RUN:
        return ToolResult.error(
            f"The draft was already tested {MAX_EVALUATIONS_PER_RUN} times in this work. Do not test it again; tell "
            "the user the latest result and what is left."
        )
    cases = _chosen_cases(skill, arguments.get("cases") or [])
    if isinstance(cases, ToolResult):
        return cases
    try:
        evaluation = start_evaluation(
            skill,
            cases=cases,
            actor=run.owner,
            model_version=run.model_version,
            effort=run.effort,
            run=run,
            tool_call_id=context.call.id,
        )
    except CannotEvaluate as exc:
        return ToolResult.error(f"The draft cannot be tested: {exc}")
    return _waiting(evaluation)


def _chosen_cases(skill, names: list[str]) -> list[SkillEvaluationCase] | ToolResult:
    """評価するケース。名前の指定がなければ、必須のものを先に上限まで"""
    cases = list(SkillEvaluationCase.objects.filter(skill=skill).order_by("-required", "name"))
    if not cases:
        return ToolResult.error(
            "This skill has no test cases. Tell the user that test cases can be added on the skill's page, and that "
            "the draft was not tested."
        )
    if names:
        by_name = {c.name: c for c in cases}
        if unknown := [n for n in names if n not in by_name]:
            available = ", ".join(f'"{c.name}"' for c in cases)
            return ToolResult.error(f"Unknown test cases: {', '.join(unknown)}. The cases are: {available}.")
        cases = [by_name[n] for n in dict.fromkeys(names)]
    return cases[:MAX_CASES_PER_EVALUATION]


def _waiting(evaluation: SkillEvaluation) -> ToolResult:
    count = evaluation.results.count()
    return ToolResult.text(
        "",
        waits_for_user=True,
        value={
            "kind": ClarificationKind.EVALUATION,
            "questions": [],
            "context": {
                "evaluation": evaluation.uid,
                "skill": evaluation.skill.uid,
                "name": evaluation.skill.name,
                "revision": evaluation.draft_revision,
                "cases": count,
            },
        },
    )


def answer_result(clarification: "ClarificationRequest") -> dict[str, Any]:
    """終わった評価を、test_skill_draft の呼出しへの結果（RunEventの内容）にする"""
    evaluation = SkillEvaluation.objects.get(uid=clarification.context["evaluation"])
    results = list(evaluation.results.order_by("order"))
    passed = sum(1 for r in results if r.passed)
    if evaluation.status == EvaluationStatus.CANCELLED:
        head = (
            f"The test of the draft (revision {evaluation.draft_revision}) was stopped by the user. Do not test it "
            "again unless the user asks."
        )
    else:
        head = (
            f"The test of the draft (revision {evaluation.draft_revision}) finished: {passed} of {len(results)} cases "
            "passed the automatic checks."
        )
    lines = [head]
    for result in results:
        lines += _case_lines(result)
    lines.append(
        "The AI judge is for reference only. If a case failed because of the draft, fix the draft and test again. "
        "Tell the user the result in the final reply."
    )
    return {
        "call_id": clarification.tool_call_id,
        "name": NAME,
        "content": [{"type": "text", "text": "\n".join(lines)}],
        "is_error": False,
        "value": {
            "evaluation": evaluation.uid,
            "skill": evaluation.skill.uid,
            "status": evaluation.status,
            "passed": passed,
            "total": len(results),
        },
    }


def _case_lines(result) -> list[str]:
    check = result.draft_check or {}
    required = " (required)" if result.required else ""
    if result.passed:
        state = "passed"
    elif not check:
        state = "not checked"
    else:
        state = "FAILED: " + "; ".join(_problems(check))
    lines = [f'- "{result.case_name}"{required}: {state}']
    if result.verdict is not None:
        lines.append(f"  AI judge: the draft is {_verdict_word(result.verdict)} — {result.judge_reason}")
    elif result.judge_note:
        lines.append(f"  AI judge: {result.judge_note}")
    if reply := (check.get("reply") or "").strip():
        excerpt = reply if len(reply) <= REPLY_EXCERPT else reply[:REPLY_EXCERPT] + "…"
        lines.append(f"  Draft's final reply: {excerpt}")
    return lines


def _problems(check: dict[str, Any]) -> list[str]:
    problems = []
    if not check.get("completed"):
        problems.append(f"the work did not complete ({check.get('error') or check.get('status_display', '')})")
    if check.get("result_required") and not check.get("result_submitted"):
        problems.append("no result was submitted")
    for mismatch in (check.get("mismatches") or [])[:5]:
        problems.append(f"{mismatch['path']}: expected {mismatch['expected']!r}, got {mismatch.get('actual')!r}")
    return problems or ["unknown"]


def _verdict_word(verdict: int) -> str:
    return {
        EvaluationVerdict.BETTER: "better than the published version",
        EvaluationVerdict.SAME: "about the same as the published version",
        EvaluationVerdict.WORSE: "worse than the published version",
    }.get(EvaluationVerdict(verdict), "")


TEST_SKILL_DRAFT = Tool(NAME, DESCRIPTION, PARAMETERS, execute, Effect.INTERACT, alone=True)
