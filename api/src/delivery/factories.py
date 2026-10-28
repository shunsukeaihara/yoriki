import factory
from factory.django import DjangoModelFactory

from user.factories import UserFactory

from .models import Destination, DestinationKind, DestinationScope


class DestinationFactory(DjangoModelFactory[Destination]):
    """全社の範囲のWebhookの出口と最初の版。版の項目は `revision__url=...` のように指定する（送り先の確認はしない）"""

    class Meta:
        model = Destination
        skip_postgeneration_save = True  # revision() の revise() で保存する

    kind = DestinationKind.WEBHOOK
    scope = DestinationScope.COMPANY  # 部門の範囲にするときは department も渡す
    name = factory.Sequence(lambda n: f"出口{n}")
    signing_secret = factory.LazyFunction(Destination.new_secret)
    created_by = factory.SubFactory(UserFactory)

    @factory.post_generation
    def revision(obj: Destination, create, extracted, **kwargs):
        if not create:
            return
        kwargs.setdefault("url", "https://hooks.example.com/yoriki")
        kwargs.setdefault("allows_sensitive_results", False)
        obj.revise(actor=obj.created_by, expected_version=0, **kwargs)
