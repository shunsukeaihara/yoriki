import factory
from factory.django import DjangoModelFactory

from user.factories import UserFactory

from .models import LLMConnection, ModelProfile, ModelProfileVersion, ProviderKind


class LLMConnectionFactory(DjangoModelFactory[LLMConnection]):
    class Meta:
        model = LLMConnection
        skip_postgeneration_save = True  # api_key() の中で保存する

    name = factory.Sequence(lambda n: f"接続{n}")
    kind = ProviderKind.OPENAI
    created_by = factory.SubFactory(UserFactory)

    @factory.post_generation
    def api_key(obj: LLMConnection, create, extracted, **kwargs):
        obj.set_api_key("sk-test-0123456789abcd" if extracted is None else extracted)
        if create:
            obj.save()


class ModelProfileFactory(DjangoModelFactory[ModelProfile]):
    class Meta:
        model = ModelProfile
        skip_postgeneration_save = True  # version() の revise() で保存する

    name = factory.Sequence(lambda n: f"モデル{n}")
    created_by = factory.SubFactory(UserFactory)

    @factory.post_generation
    def version(obj: ModelProfile, create, extracted, **kwargs):
        """現在の版を作る。`version__connection=...` 等で版の項目を指定できる"""
        if not create:
            return
        kwargs.setdefault("connection", LLMConnectionFactory.create())
        kwargs.setdefault("model_name", "gpt-test")
        kwargs.setdefault("max_input_tokens", 128_000)
        kwargs.setdefault("max_output_tokens", 16_000)
        obj.revise(actor=obj.created_by, expected_version=0, **kwargs)


class ModelProfileVersionFactory(DjangoModelFactory[ModelProfileVersion]):
    class Meta:
        model = ModelProfileVersion
