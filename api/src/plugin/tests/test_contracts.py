"""本体のPluginを、自社のPluginと同じ適合テストの道具（plugin/testing.py）で確かめる（design.md §3.11.4）。
ConnectorのAdapterは、接続先を用意する connector/tests/test_adapters.py で確かめる"""

import hashlib
import hmac
import json
import socket
import time
from unittest import mock

import boto3
import httpx
import pytest
from beartype.roar import BeartypeDoorHintViolation
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from moto import mock_aws

from artifact.storage import LocalStorage, S3Storage, StorageBackend
from delivery.adapters import DeliveryRequest, WebhookAdapter
from delivery.channels.discord import DiscordAdapter
from delivery.channels.slack import SlackAdapter
from delivery.models import Destination
from plugin import builtin
from plugin.builtin_plugins import plugins
from plugin.testing import (
    ChannelContract,
    NotificationContract,
    PluginContract,
    StorageContract,
    TriggerContract,
    checked,
)
from task.triggers import TriggerRequest

SPECS = {spec.id: spec for spec in plugins()}


@pytest.mark.parametrize("plugin_id", sorted(SPECS))
def test_every_builtin_declaration_fits(plugin_id):
    class Contract(PluginContract):
        spec = SPECS[plugin_id]
        builtin = True

    Contract().test_declaration_fits_the_contract()


class TestLocalStorage(StorageContract):
    spec = SPECS[builtin.LOCAL_STORAGE]
    extension_id = builtin.LOCAL_STORAGE
    builtin = True

    @pytest.fixture
    def contract_storage(self, tmp_path):
        return LocalStorage("contract", root=str(tmp_path / "objects"))


class TestS3Storage(StorageContract):
    spec = SPECS[builtin.S3_STORAGE]
    extension_id = builtin.S3_STORAGE
    builtin = True

    @pytest.fixture
    def contract_storage(self, monkeypatch):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
        with mock_aws():
            boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="contract")
            yield S3Storage("contract", bucket="contract", prefix="p", region="us-east-1")


class TestWebhookTrigger(TriggerContract):
    spec = SPECS[builtin.WEBHOOK_TRIGGER]
    extension_id = builtin.WEBHOOK_TRIGGER
    builtin = True

    @pytest.fixture
    def contract_request(self):
        body = json.dumps({"event_id": "e1", "inputs": {"vendor": "ACME"}}).encode()
        return TriggerRequest(body=body, content_type="application/json")

    @pytest.fixture
    def contract_invalid_request(self):
        return TriggerRequest(body=b"{broken", content_type="application/json")


class _WebhookLike(NotificationContract):
    builtin = True
    url = ""

    @pytest.fixture(autouse=True)
    def fixture_receiver(self):
        info = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        transport = httpx.MockTransport(lambda request: httpx.Response(202))
        with (
            mock.patch("delivery.network.socket.getaddrinfo", return_value=info),
            mock.patch.object(WebhookAdapter, "transport", transport),
        ):
            yield

    @pytest.fixture
    def contract_request(self):
        return DeliveryRequest(
            # 外へ送る出口は配信の行を読まない
            delivery=None,  # type: ignore[arg-type]
            attempt=1,
            payload={"event_id": "evt-1", "event_type": "run.completed"},
            url=self.url,
            secret=Destination.new_secret(),
        )


class TestWebhookNotification(_WebhookLike):
    spec = SPECS[builtin.WEBHOOK]
    extension_id = builtin.WEBHOOK
    url = "https://hooks.example.com/yoriki"


class TestZapierNotification(_WebhookLike):
    spec = SPECS[builtin.ZAPIER]
    extension_id = builtin.ZAPIER
    url = "https://hooks.zapier.com/hooks/catch/1/abc/"


def test_checked_adapters_catch_wrong_return_types(tmp_path):
    class Sloppy(LocalStorage):
        def stat(self, key):
            return {"key": key}  # StoredObject ではない

    storage = checked(Sloppy("x", root=str(tmp_path)), StorageBackend)
    with pytest.raises(BeartypeDoorHintViolation):
        storage.stat("a")


class TestSlackChannel(ChannelContract):
    spec = SPECS[builtin.SLACK]
    extension_id = builtin.SLACK
    builtin = True

    @pytest.fixture(autouse=True)
    def fixture_slack(self):
        def respond(request: httpx.Request) -> httpx.Response:
            method = request.url.path.rsplit("/", 1)[-1]
            data: dict = {"ok": True, "ts": "1.0001", "team_id": "T1", "team": "t", "user_id": "UBOT"}
            if method == "conversations.info":
                data["channel"] = {"id": "C1", "name": "general", "is_member": True}
            if method == "conversations.list":
                data["channels"] = [{"id": "C1", "name": "general", "is_member": True}]
            return httpx.Response(200, json=data)

        with mock.patch.object(SlackAdapter, "transport", httpx.MockTransport(respond)):
            yield

    @pytest.fixture
    def contract_secrets(self):
        return {"bot_token": "xoxb-1", "signing_secret": "secret"}

    @pytest.fixture
    def contract_channel_id(self):
        return "C1"

    @pytest.fixture
    def contract_signed_request(self):
        body = json.dumps({"type": "url_verification", "challenge": "c"}).encode()
        timestamp = str(int(time.time()))
        digest = hmac.new(b"secret", b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256).hexdigest()
        headers = {
            "content-type": "application/json",
            "x-slack-request-timestamp": timestamp,
            "x-slack-signature": f"v0={digest}",
        }
        return headers, body


DISCORD_KEY = Ed25519PrivateKey.generate()


class TestDiscordChannel(ChannelContract):
    spec = SPECS[builtin.DISCORD]
    extension_id = builtin.DISCORD
    builtin = True

    @pytest.fixture(autouse=True)
    def fixture_discord(self):
        public = DISCORD_KEY.public_key().public_bytes_raw().hex()

        fixed = {
            ("GET", "users/@me"): {"id": "5"},
            ("GET", "applications/@me"): {"verify_key": public, "flags": 1 << 18},
            ("GET", "guilds/9"): {"id": "9", "name": "g", "owner_id": "5", "roles": []},
            ("GET", "guilds/9/members/5"): {"roles": []},
            ("GET", "channels/1"): {"id": "1", "type": 0, "name": "general", "guild_id": "9"},
            ("GET", "guilds/9/channels"): [{"id": "1", "type": 0, "name": "general", "guild_id": "9"}],
            ("GET", "channels/100/messages"): [],
        }

        def respond(request: httpx.Request) -> httpx.Response:
            key = (request.method, request.url.path.removeprefix("/api/v10/"))
            return httpx.Response(200, json=fixed.get(key, {"id": "100"}))

        with mock.patch.object(DiscordAdapter, "transport", httpx.MockTransport(respond)):
            yield

    @pytest.fixture
    def contract_secrets(self):
        return {"bot_token": "t", "public_key": DISCORD_KEY.public_key().public_bytes_raw().hex(), "guild_id": "9"}

    @pytest.fixture
    def contract_channel_id(self):
        return "1"

    @pytest.fixture
    def contract_signed_request(self):
        body = json.dumps({"type": 1}).encode()
        timestamp = str(int(time.time()))
        headers = {
            "x-signature-ed25519": DISCORD_KEY.sign(timestamp.encode() + body).hex(),
            "x-signature-timestamp": timestamp,
        }
        return headers, body
