import factory
from factory.django import DjangoModelFactory

from conversation.factories import ConversationFactory

from .models import ClarificationKind, ClarificationRequest, Run, RunStatus


class RunFactory(DjangoModelFactory[Run]):
    class Meta:
        model = Run

    conversation = factory.SubFactory(ConversationFactory)
    owner = factory.SelfAttribute("conversation.owner")
    model_version = factory.LazyAttribute(lambda o: o.conversation.model_profile.current_version)
    status = RunStatus.QUEUED


class ClarificationRequestFactory(DjangoModelFactory[ClarificationRequest]):
    class Meta:
        model = ClarificationRequest

    run = factory.SubFactory(RunFactory, status=RunStatus.WAITING_USER)
    kind = ClarificationKind.QUESTION
    tool_call_id = factory.Sequence(lambda n: f"call_{n}")
    questions = factory.LazyFunction(
        lambda: [
            {
                "header": "取引先",
                "question": "どちらの取引先ですか",
                "options": [{"label": "本社", "description": ""}, {"label": "大阪支店", "description": ""}],
                "multi_select": False,
            }
        ]
    )
