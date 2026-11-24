"""プロバイダの応答を、途中で打ち切る（harness.md §10.8）。

応答を待つスレッドは次のデータが届くまで戻らず、HTTPクライアントを別のスレッドから閉じても待ちは終わらない。
ソケットを `shutdown` すると、待っている読み取りがすぐに終わる。ソケットは、httpcoreの `trace` 拡張
（requestの `extensions`）で、つながったときに受け取る。
"""

import contextlib
import socket
import threading
from collections.abc import Callable
from typing import Any

CONNECTED_EVENTS = ("connect_tcp.complete", "connect_unix_socket.complete", "start_tls.complete")


class StreamCanceller:
    """1回の呼出しの打ち切り。`cancel()` は別のスレッドから呼んでよい"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._socket: Any = None
        self._cancelled = False
        self._client: Any = None

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            self._shutdown()

    def client(self, client_class: Callable[..., Any]) -> Any:
        """打ち切れるHTTPクライアント（httpx・httpx2）を作る"""
        self._client = client_class(event_hooks={"request": [self._on_request]})
        return self._client

    def close(self) -> None:
        """呼出しが終わったら、応答を受けたスレッドで呼ぶ"""
        if self._client is not None:
            self._client.close()

    def _on_request(self, request: Any) -> None:
        request.extensions = {**request.extensions, "trace": self._on_trace}

    def _on_trace(self, name: str, info: dict[str, Any]) -> None:
        if not name.endswith(CONNECTED_EVENTS):
            return
        with self._lock:
            # TLSでは、つないだソケットが包んだものに替わるので、最後のものを持つ
            self._socket = info["return_value"].get_extra_info("socket")
            # つなぐ途中に打ち切られた
            if self._cancelled:
                self._shutdown()

    def _shutdown(self) -> None:
        if self._socket is not None:
            # 応答を受け終えて、もう閉じている
            with contextlib.suppress(OSError):
                self._socket.shutdown(socket.SHUT_RDWR)
