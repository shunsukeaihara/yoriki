"""応答の途中での打ち切り（llm/providers/cancel.py）。黙ったままのサーバーに実際につないで確かめる"""

import contextlib
import re
import socket
import threading
import time
from collections.abc import Iterator

import httpx
import httpx2
import pytest

from llm.factories import LLMConnectionFactory
from llm.models import ProviderKind
from llm.providers import get_adapter
from llm.providers.base import ChatRequest, Message, ProviderError
from llm.providers.cancel import StreamCanceller

HEADERS = b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n"


class SilentServer:
    """要求を受けた後、（`headers` なら応答のヘッダーだけを送って）黙る"""

    def __init__(self, *, headers: bool):
        self.headers = headers
        self.received = threading.Event()
        self.closed = threading.Event()
        self.socket = socket.create_server(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{self.socket.getsockname()[1]}"
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        # 要求を送り終える前に切られた（つなぐ前の打ち切り）
        with contextlib.suppress(ConnectionError):
            self._serve_once()

    def _serve_once(self) -> None:
        peer, _address = self.socket.accept()
        with peer:
            data = b""
            while b"\r\n\r\n" not in data:
                data += peer.recv(65536)
            head, _sep, body = data.partition(b"\r\n\r\n")
            length = re.search(rb"content-length: (\d+)", head.lower())
            while length and len(body) < int(length[1]):
                body += peer.recv(65536)
            if self.headers:
                peer.sendall(HEADERS)
            self.received.set()
            peer.settimeout(20)
            if not peer.recv(1):
                self.closed.set()


@pytest.fixture(name="server", params=[False, True], ids=["before-headers", "after-headers"])
def fixture_server(request) -> Iterator[SilentServer]:
    server = SilentServer(headers=request.param)
    yield server
    server.socket.close()


@pytest.mark.django_db
@pytest.mark.parametrize("kind", list(ProviderKind), ids=lambda k: k.name)
def test_cancel_ends_a_silent_stream(server, kind, monkeypatch):
    # 公式のAPIの接続先は固定なので、SDKの環境変数で差し替える
    monkeypatch.setenv("OPENAI_BASE_URL", f"{server.url}/v1")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
    monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", server.url)
    base_url = f"{server.url}/v1" if kind == ProviderKind.LOCAL else ""
    connection = LLMConnectionFactory.create(kind=kind, base_url=base_url, timeout_seconds=15)
    adapter = get_adapter(connection)
    canceller = adapter.canceller = StreamCanceller()

    def cancel() -> None:
        assert server.received.wait(10)
        time.sleep(0.2)
        canceller.cancel()

    thread = threading.Thread(target=cancel)
    thread.start()
    started = time.monotonic()
    with pytest.raises(ProviderError):
        list(adapter.stream(ChatRequest(model="m", messages=[Message("user", "こんにちは")])))
    assert time.monotonic() - started < 8
    thread.join()
    canceller.close()
    assert server.closed.wait(5)


@pytest.mark.parametrize("client_class", [httpx.Client, httpx2.Client])
def test_cancel_before_connecting(client_class):
    server = SilentServer(headers=False)
    canceller = StreamCanceller()
    client = canceller.client(client_class)
    canceller.cancel()
    with pytest.raises((httpx.TransportError, httpx2.TransportError)):
        client.post(server.url, content=b"x", timeout=15)
    canceller.close()
    server.socket.close()


def test_cancel_after_the_end_is_harmless():
    canceller = StreamCanceller()
    canceller.cancel()
    canceller.close()
    canceller.cancel()
