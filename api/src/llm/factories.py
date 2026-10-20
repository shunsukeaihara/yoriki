import factory
from factory.django import DjangoModelFactory

from user.factories import UserFactory

from .models import LLMConnection, ModelProfile, ProviderKind


class LLMConnectionFactory(DjangoModelFactory[LLMConnection]):
    class Meta:
        model = LLMConnection

    name = factory.Sequence(lambda n: f"接続{n}")
    kind = ProviderKind.OPENAI
    api_key = "sk-test-0123456789abcd"
    created_by = factory.SubFactory(UserFactory)


class ModelProfileFactory(DjangoModelFactory[ModelProfile]):
    """モデル設定と最初の版を作る。版の項目は `version__connection=...` のように指定する"""

    class Meta:
        model = ModelProfile
        skip_postgeneration_save = True  # version() の revise() で保存する

    name = factory.Sequence(lambda n: f"モデル{n}")
    created_by = factory.SubFactory(UserFactory)

    @factory.post_generation
    def version(obj: ModelProfile, create, extracted, **kwargs):
        if not create:
            return
        kwargs.setdefault("connection", LLMConnectionFactory.create())
        kwargs.setdefault("model_name", "gpt-test")
        kwargs.setdefault("max_input_tokens", 128_000)
        kwargs.setdefault("max_output_tokens", 16_000)
        obj.revise(actor=obj.created_by, expected_version=0, **kwargs)
