import factory
from factory.django import DjangoModelFactory

from llm.factories import ModelProfileFactory
from user.factories import UserFactory

from .models import Conversation, ConversationMessage, MessageRole


class ConversationFactory(DjangoModelFactory[Conversation]):
    class Meta:
        model = Conversation

    owner = factory.SubFactory(UserFactory)
    model_profile = factory.SubFactory(ModelProfileFactory, version__supports_tools=True)
    title = factory.Sequence(lambda n: f"会話{n}")


class ConversationMessageFactory(DjangoModelFactory[ConversationMessage]):
    class Meta:
        model = ConversationMessage

    conversation = factory.SubFactory(ConversationFactory)
    sender = factory.SelfAttribute("conversation.owner")
    role = MessageRole.USER
    body = "依頼です"
