import json

import factory
from factory.django import DjangoModelFactory

from plugin import builtin
from user.factories import UserFactory

from .models import ChannelConnection, Destination, DestinationScope


class DestinationFactory(DjangoModelFactory[Destination]):
    """全社の範囲のWebhookの出口と最初の版。版の項目は `revision__url=...` のように指定する（送り先の確認はしない）"""

    class Meta:
        model = Destination
        skip_postgeneration_save = True  # revision() の revise() で保存する

    extension = builtin.WEBHOOK
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


class ChannelConnectionFactory(DjangoModelFactory[ChannelConnection]):
    """Slackの接続（接続の確認はしない）"""

    class Meta:
        model = ChannelConnection

    extension = builtin.SLACK
    name = factory.Sequence(lambda n: f"Slack{n}")
    secrets = json.dumps({"bot_token": "xoxb-test", "signing_secret": "test-signing-secret"})
    workspace_id = "T001"
    workspace_name = "テストのWorkspace"
    bot_user_id = "UBOT"
    created_by = factory.SubFactory(UserFactory)


def channel_destination(connection: ChannelConnection | None = None, **kwargs) -> Destination:
    """チャネルの対話の出口（全社。既定はSlack。チャネルの確認はしない）"""
    connection = connection or ChannelConnectionFactory.create()
    kwargs.setdefault("revision__channel_id", "C001")
    kwargs.setdefault("revision__channel_name", "#経理")
    return DestinationFactory.create(
        extension=connection.extension, revision__url="", revision__connection=connection, **kwargs
    )
