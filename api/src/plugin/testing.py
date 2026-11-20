"""適合テストの道具（design.md §3.11.4。使い方は plugin/CONTRACT.md）。

拡張種別ごとの適合テストを、pytestのテストのクラスとして用意する。Pluginの作者は、自分のリポジトリでクラスを継承し、
宣言（`spec`・`extension_id`）と、相手の要るもの（`contract_*` のfixture）を用意して流す。テストはcapabilityごとに
分け、宣言していないcapabilityのテストは飛ばす。Adapterの呼出しは、基底クラスの型ヒントで引数と戻り値を確かめる
（beartypeが入っていれば。本番では確かめない）。本体のPluginも同じ道具でテストする。

    class TestAcmeChat(NotificationContract):
        spec = ACME
        extension_id = "acme.chat.notification"

        @pytest.fixture
        def contract_request(self, ...):
            return DeliveryRequest(...)
"""

import functools
import inspect
import io
import operator
import types
import typing
from typing import Any, ClassVar

import pytest

from artifact.storage import StorageBackend, StorageError
from connector.adapters.base import DatasourceAdapter, QueryResult, TableInfo
from connector.errors import ConnectorError
from delivery.adapters import DeliveryRequest, NotificationAdapter, Outcome
from delivery.channels.base import (
    ChannelAdapter,
    ChannelChoice,
    ChannelInfo,
    ChannelList,
    ConnectionInfo,
    InboundEvent,
    InboundRequest,
    MessageKind,
    QuestionView,
    ThreadMessage,
)
from delivery.models import AttemptOutcome
from delivery.network import BlockedAddress
from task.triggers import TriggerAdapter, TriggerRequest
from task.webhook import Payload, PayloadError

from .interface import _interface_methods, check_plugin
from .spec import Extension, PluginSpec

try:
    from beartype.door import die_if_unbearable
except ImportError:  # pragma: no cover  beartypeがなければ、型ヒントの確かめだけを飛ばす
    die_if_unbearable = None  # type: ignore[assignment]


def checked(adapter: Any, base: type) -> Any:
    """`adapter` のインターフェースのメソッドを、基底クラスの型ヒントで引数と戻り値を確かめる形に包む"""
    if die_if_unbearable is None:
        return adapter
    for name in _interface_methods(base):
        if name == "__init__":
            continue
        expected = getattr(base, name)
        try:
            hints = typing.get_type_hints(expected)
        except Exception:  # 型ヒントを解決できない（TYPE_CHECKINGの中の型等）メソッドは確かめない
            continue
        hints = {key: _normalize(hint) for key, hint in hints.items()}
        setattr(adapter, name, _checked_method(getattr(adapter, name), inspect.signature(expected), hints))
    return adapter


def _normalize(hint: Any) -> Any:
    """beartypeは typing.IO を実際のファイル（BytesIO等）で満たせないProtocolとして扱うので、io.IOBase に読み替える"""
    if hint in (typing.IO, typing.BinaryIO, typing.TextIO) or typing.get_origin(hint) is typing.IO:
        return io.IOBase
    args = typing.get_args(hint)
    if args and typing.get_origin(hint) in (typing.Union, types.UnionType):
        return functools.reduce(operator.or_, [_normalize(a) for a in args])
    return hint


def _checked_method(method, signature: inspect.Signature, hints: dict[str, Any]):
    @functools.wraps(method)
    def wrapper(*args, **kwargs):
        bound = signature.bind(None, *args, **kwargs)  # 先頭は self
        for name, value in bound.arguments.items():
            if name in hints:
                die_if_unbearable(value, hints[name])
        result = method(*args, **kwargs)
        if "return" in hints:
            die_if_unbearable(result, hints["return"])
        return result

    return wrapper


class PluginContract:
    """Pluginの宣言（形・ID・名前空間）"""

    spec: ClassVar[PluginSpec]
    # 本体のPluginか（`yoriki.` の名前空間を使えるか）
    builtin: ClassVar[bool] = False

    def test_declaration_fits_the_contract(self):
        assert check_plugin(self.spec, entry_point=self.spec.id, builtin=self.builtin) == []


class AdapterContract(PluginContract):
    extension_id: ClassVar[str]
    base: ClassVar[type]

    @property
    def extension(self) -> Extension:
        return next(e for e in self.spec.extensions if e.id == self.extension_id)

    def requires(self, capability: str) -> None:
        if capability not in self.extension.capabilities:
            pytest.skip(f"capability {capability!r} is not declared")

    def wrap(self, adapter: Any) -> Any:
        assert isinstance(adapter, self.extension.adapter)
        return checked(adapter, self.base)


class StorageContract(AdapterContract):
    """保存先（design.md §7.8）。`contract_storage` に、空の保存先のAdapterを用意する"""

    base = StorageBackend

    @pytest.fixture(name="storage")
    def fixture_contract_storage(self, contract_storage) -> StorageBackend:
        return self.wrap(contract_storage)

    def test_put_read_stat_delete(self, storage):
        stored = storage.put_immutable("contract/a.txt", io.BytesIO(b"hello"))
        assert (stored.key, stored.size) == ("contract/a.txt", 5)
        with storage.open_read("contract/a.txt") as src:
            assert src.read() == b"hello"
        assert storage.stat("contract/a.txt").sha256 == stored.sha256
        assert storage.exists("contract/a.txt")
        storage.delete("contract/a.txt")
        assert storage.stat("contract/a.txt") is None

    def test_does_not_overwrite_another_content(self, storage):
        storage.put_immutable("contract/b.txt", io.BytesIO(b"one"))
        with pytest.raises(StorageError):
            storage.put_immutable("contract/b.txt", io.BytesIO(b"two"))

    def test_missing_objects(self, storage):
        assert storage.stat("contract/none.txt") is None
        with pytest.raises(StorageError):
            storage.open_read("contract/none.txt")

    def test_lists_keys_under_a_prefix(self, storage):
        storage.put_immutable("contract/list/1", io.BytesIO(b"1"))
        storage.put_immutable("contract/other/2", io.BytesIO(b"2"))
        assert sorted(storage.list_keys("contract/list/")) == ["contract/list/1"]


class TriggerContract(AdapterContract):
    """入口。`contract_request` に受け付ける要求、`contract_invalid_request` に断る要求を用意する"""

    base = TriggerAdapter

    @pytest.fixture(name="trigger")
    def fixture_contract_trigger(self) -> TriggerAdapter:
        return self.wrap(self.extension.adapter())

    def test_parses_a_request(self, trigger, contract_request: TriggerRequest):
        payload = trigger.parse(contract_request)
        assert isinstance(payload, Payload)
        assert payload.file_bytes
        key = trigger.dedup_key(payload, "", contract_request.idempotency_key)
        assert key is None or isinstance(key, str)
        assert isinstance(trigger.input_values(payload, [], []), dict)

    def test_refuses_with_payload_error(self, trigger, contract_invalid_request: TriggerRequest):
        with pytest.raises(PayloadError):
            trigger.parse(contract_invalid_request)


class NotificationContract(AdapterContract):
    """出口。`contract_request` に、受け手が受け付ける送信（受け手はテストの中で用意する）を用意する"""

    base = NotificationAdapter

    @pytest.fixture(name="notification")
    def fixture_contract_notification(self) -> NotificationAdapter:
        return self.wrap(self.extension.adapter())

    def test_delivers_and_returns_an_outcome(self, notification, contract_request: DeliveryRequest):
        assert isinstance(notification.deliver(contract_request), Outcome)

    def test_url_destinations_refuse_plain_http(self, notification):
        self.requires("url")
        with pytest.raises(BlockedAddress):
            notification.validate_url("http://hooks.example.com/yoriki")


class ChannelContract(AdapterContract):
    """対話の出口（design.md §4.6）。`contract_secrets` に接続の資格情報、`contract_channel_id` に投稿できるチャネル、
    `contract_signed_request` に認証に通る受信の要求（ヘッダーと本文）を用意する（相手はテストの中で用意する）"""

    base = ChannelAdapter

    @pytest.fixture(name="channel")
    def fixture_contract_channel(self) -> ChannelAdapter:
        return self.wrap(self.extension.adapter())

    def test_checks_the_connection_and_the_channel(self, channel, contract_secrets, contract_channel_id):
        assert isinstance(channel.check_connection(contract_secrets), ConnectionInfo)
        assert isinstance(channel.check_channel(contract_secrets, contract_channel_id), ChannelInfo)

    def test_posts_into_a_thread_and_updates(self, channel, contract_secrets, contract_channel_id):
        parent = channel.post(contract_secrets, contract_channel_id, ThreadMessage(MessageKind.PARENT, "parent"))
        assert parent.outcome == AttemptOutcome.ACCEPTED and parent.message_ref
        view = QuestionView("q1", [{"header": "h", "question": "q?", "options": [{"label": "a"}, {"label": "b"}]}])
        message = ThreadMessage(MessageKind.QUESTION, "", question=view)
        posted = channel.post(contract_secrets, contract_channel_id, message, thread_ref=parent.message_ref)
        assert posted.outcome == AttemptOutcome.ACCEPTED and posted.message_ref
        updated = channel.update(contract_secrets, contract_channel_id, posted.message_ref, message)
        assert isinstance(updated, Outcome)

    def test_verifies_and_parses_requests(self, channel, contract_secrets, contract_signed_request):
        headers, body = contract_signed_request
        assert channel.verify(contract_secrets, headers, body)
        assert not channel.verify(contract_secrets, headers, body + b" ")
        assert isinstance(channel.parse(headers, body), InboundRequest)

    def test_lists_channels(self, channel, contract_secrets, contract_channel_id):
        """チャネルの一覧を取れるもの（capability list_channels）は、投稿できるチャネルを選べるものとして返す"""
        self.requires("list_channels")
        found = channel.list_channels(contract_secrets)
        assert isinstance(found, ChannelList)
        assert all(isinstance(c, ChannelChoice) for c in found.channels)
        assert any(c.channel_id == contract_channel_id and not c.unavailable for c in found.channels)

    def test_reads_replies_again(self, channel, contract_secrets, contract_channel_id):
        """常時接続で受けるもの（capability listen）は、つなぎ直した後にスレッドの返信を読み直せる"""
        self.requires("listen")
        parent = channel.post(contract_secrets, contract_channel_id, ThreadMessage(MessageKind.PARENT, "parent"))
        replies = channel.fetch_replies(contract_secrets, contract_channel_id, parent.message_ref, parent.message_ref)
        assert isinstance(replies, list)
        assert all(isinstance(r, InboundEvent) for r in replies)


class ConnectorContract(AdapterContract):
    """Connector。`contract_connector` に、表を1つ以上公開した、つながるConnectorを用意する"""

    base = DatasourceAdapter

    @pytest.fixture(name="datasource")
    def fixture_contract_datasource(self, contract_connector) -> DatasourceAdapter:
        adapter = self.extension.adapter(contract_connector.connection, contract_connector.current_version)
        return self.wrap(adapter)

    def test_connects(self, datasource):
        datasource.check()

    def test_lists_and_describes_tables(self, datasource, contract_connector):
        assert all(isinstance(t, TableInfo) for t in datasource.list_tables())
        described = datasource.describe()
        published = {(t["schema"] or "", t["name"]) for t in contract_connector.current_version.tables}
        assert {(t.schema or "", t.name) for t in described} <= published

    def test_queries_published_tables_only(self, datasource, contract_connector):
        table = contract_connector.current_version.tables[0]
        name = f"{table['schema']}.{table['name']}" if table["schema"] else table["name"]
        assert isinstance(datasource.query(f"SELECT * FROM {name}", keep_rows=1), QueryResult)
        with pytest.raises(ConnectorError):
            datasource.query(f"DELETE FROM {name}")
