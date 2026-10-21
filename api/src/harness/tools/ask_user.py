"""利用者に確かめる（harness.md §10.3）。呼ぶとRunはWAITING_USERになり、回答がこの呼出しの結果になる"""

from typing import TYPE_CHECKING, Any

from django.utils import timezone

from .base import Effect, Tool, ToolContext, ToolResult

if TYPE_CHECKING:
    from run.models import ClarificationRequest

DESCRIPTION = """Ask the user to decide something you cannot determine yourself, and wait for the answer.

Use it only for business decisions that would change the result and cannot be inferred: ambiguous conditions, \
choosing between candidates, missing information. Do not use it to ask permission to run commands or to confirm \
routine steps; proceed and state the assumption you made instead.

- Ask 1 to 3 short questions. Each question has 2 to 4 mutually exclusive options.
- Put the recommended option first and add " (Recommended)" to its label.
- Do not add an "Other" option; the user can always answer in free text.
- Call this tool alone, not together with other tools. Your turn ends until the user answers; the answers arrive \
as this tool's result."""

PARAMETERS = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "minItems": 1,
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "header": {"type": "string", "maxLength": 12, "description": 'Very short label, e.g. "取引先".'},
                    "question": {"type": "string", "minLength": 1, "maxLength": 500, "description": "One sentence."},
                    "options": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 4,
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string", "minLength": 1, "maxLength": 60},
                                "description": {
                                    "type": "string",
                                    "maxLength": 200,
                                    "description": "What happens if this is chosen.",
                                },
                            },
                            "required": ["label"],
                            "additionalProperties": False,
                        },
                    },
                    "multi_select": {"type": "boolean"},
                },
                "required": ["header", "question", "options"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}


def execute(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    from run.models import ClarificationKind, ClarificationRequest  # noqa: PLC0415  run.modelsがこのモジュールを使うため
    from run.tasks import CLARIFICATION_TTL  # noqa: PLC0415

    questions = [
        {
            "header": q["header"],
            "question": q["question"],
            "options": [{"label": o["label"], "description": o.get("description", "")} for o in q["options"]],
            "multi_select": q.get("multi_select", False),
        }
        for q in arguments["questions"]
    ]
    clarification = ClarificationRequest.objects.create(
        run=context.run,
        kind=ClarificationKind.QUESTION,
        tool_call_id=context.call.id,
        questions=questions,
        expires=timezone.now() + CLARIFICATION_TTL,
    )
    return ToolResult.text("", waits_for_user=True, value={"clarification": clarification.uid})


def answer_result(clarification: "ClarificationRequest", actor) -> dict[str, Any]:
    """回答を、ask_user の呼出しへの結果（RunEventの内容）にする"""
    lines = []
    for question, answer in zip(clarification.questions, clarification.answers, strict=False):
        chosen = ", ".join(answer.get("selected", []))
        text = answer.get("text", "")
        value = "; ".join(v for v in (chosen, text) if v) or "(no answer)"
        lines.append(f'"{question["question"]}" = {value}')
    if clarification.comment:
        lines.append(f"Additional comment from the user: {clarification.comment}")
    name = getattr(actor, "display_name", "") or getattr(actor, "email", "") or "the user"
    text = f"{name} answered:\n" + "\n".join(lines) + "\nContinue with these answers in mind."
    return {
        "call_id": clarification.tool_call_id,
        "name": "ask_user",
        "content": [{"type": "text", "text": text}],
        "is_error": False,
        "value": {"clarification": clarification.uid, "answers": clarification.answers},
    }


ASK_USER = Tool("ask_user", DESCRIPTION, PARAMETERS, execute, Effect.INTERACT, alone=True)
