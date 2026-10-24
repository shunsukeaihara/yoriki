"""モデルにつながらないときの切り替えの確認（design.md §4.8、harness.md §10.8）"""

from django.utils.translation import gettext as _

from llm.models import ModelProfile, ProviderKind
from run.models import ClarificationKind, ClarificationRequest, Run, RunEvent, RunEventKind

from .skills import unsupported_skills

RETRY_VALUE = "retry"


def switch_candidates(run: Run) -> list[ModelProfile]:
    """切り替えてよいモデル設定。機密の会話はローカルLLMだけ。Toolを使うのでTool呼出しに対応するものだけ。
    使っているSkillの必要な能力を持つものだけ"""
    profiles = (
        ModelProfile.objects.filter(
            deleted=False,
            is_active=True,
            current_version__connection__is_active=True,
            current_version__connection__deleted=False,
            current_version__supports_tools=True,
        )
        .exclude(pk=run.model_version.profile_id)
        .select_related("current_version__connection")
        .order_by("-is_default", "name")
    )
    if run.handles_sensitive_data:
        profiles = profiles.filter(current_version__connection__kind=ProviderKind.LOCAL)
    usable = [p for p in profiles if p.current_version and not unsupported_skills(run, p.current_version)]
    return usable[:3]


def request_model_switch(run: Run, detail: str) -> ClarificationRequest:
    """呼び出し側でRunの行ロックを取っていること"""
    options = [
        {"label": p.name, "description": _("このモデルで続けます"), "value": p.uid} for p in switch_candidates(run)
    ]
    options.append({"label": _("同じモデルでもう一度試す"), "description": "", "value": RETRY_VALUE})
    question = _("このモデルにつながりません（%(detail)s）。別のモデルで続けますか。") % {"detail": detail}
    clarification = ClarificationRequest.objects.create(
        run=run,
        kind=ClarificationKind.MODEL_SWITCH,
        questions=[{"header": _("モデル"), "question": question, "options": options, "multi_select": False}],
    )
    RunEvent.record(
        run,
        RunEventKind.CLARIFICATION_REQUESTED,
        {
            "clarification": clarification.uid,
            "kind": ClarificationKind.MODEL_SWITCH,
            "questions": clarification.questions,
        },
    )
    return clarification


def apply_model_switch_answer(run: Run, clarification: ClarificationRequest, actor) -> None:
    """回答に合わせてモデルを切り替える（同じtransactionの中）。候補にないモデル設定は使わない"""
    selected = (clarification.answers[0].get("selected") or [""])[0] if clarification.answers else ""
    options = {o["label"]: o["value"] for o in clarification.questions[0]["options"]}
    value = options.get(selected, RETRY_VALUE)
    if value == RETRY_VALUE:
        return
    profile = next((p for p in switch_candidates(run) if p.uid == value), None)
    if profile is None or profile.current_version is None:
        return
    version = profile.current_version
    run.switch_model(version, version.default_effort, actor=actor, trigger="user")
    conversation = run.conversation
    conversation.model_profile = profile
    conversation.effort = ""
    conversation.save(update_fields=["model_profile", "effort", "updated"])
