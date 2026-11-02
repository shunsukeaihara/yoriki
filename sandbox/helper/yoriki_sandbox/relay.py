"""Skillのスクリプトからの要求を、Runnerへ中継する（design.md §4.9.6、sandbox/CONTRACT.md §4.9）。

Sandboxにはネットワークがないので、スクリプト（`yoriki_connector`）はSandboxの中のUnixドメインソケットへ要求を送り、
このコマンドが標準出力でRunnerへ渡す。Runnerの応答は標準入力で受け取り、要求したスクリプトへ返す。Runnerは
`docker exec` でこのコマンドを始めるので、Sandboxの外への通信路は増えない。要求を受けるか、何をするかはRunnerが
決める（ここでは中身を確かめない。Sandboxの中のものなので、権限の正本にしない）。
"""

import contextlib
import json
import os
import socket
import sys
import threading
from pathlib import Path
from typing import Any

SOCKET_PATH = Path("/tmp/yoriki/connector.sock")
# 1つの要求の大きさの上限（バイト）。SQLと引数だけなので、これで足りる
MAX_REQUEST_BYTES = 1024 * 1024
# 同時に待てる要求の数。Runnerは1つずつ処理するので、多く待たせても速くならない
MAX_PENDING = 16
# 要求を読み終えるまで待つ時間（秒）
READ_TIMEOUT = 30


class Relay:
    def __init__(self, path: Path = SOCKET_PATH):
        self.path = path
        self.lock = threading.Lock()
        self.next_id = 0
        # 要求のID → (応答を待つEvent, 応答)
        self.pending: dict[int, list[Any]] = {}

    def serve(self) -> int:
        server = self._listen()
        threading.Thread(target=self._accept, args=(server,), daemon=True).start()
        _notify({"event": "ready", "socket": str(self.path)})
        # 標準入力が閉じられたら（Runnerが通信路を閉じた）終わる。待っている要求には応答を返さない
        for line in sys.stdin.buffer:
            try:
                message = json.loads(line)
                waiter = self.pending.get(message["id"])
            except (ValueError, KeyError, TypeError):
                continue
            if waiter is not None:
                waiter[1] = message.get("response")
                waiter[0].set()
        return 0

    def _listen(self) -> socket.socket:
        self.path.parent.mkdir(mode=0o700, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.path))
        os.chmod(self.path, 0o600)
        server.listen(MAX_PENDING)
        return server

    def _accept(self, server: socket.socket) -> None:
        while True:
            conn, _addr = server.accept()
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            try:
                request = _read_request(conn)
            except ValueError as exc:
                _send(conn, _error("invalid_argument", str(exc)))
                return
            except OSError:
                return
            with self.lock:
                if len(self.pending) >= MAX_PENDING:
                    busy = True
                else:
                    busy = False
                    self.next_id += 1
                    request_id = self.next_id
                    waiter: list[Any] = [threading.Event(), None]
                    self.pending[request_id] = waiter
            if busy:
                _send(conn, _error("busy", f"Too many requests are waiting (up to {MAX_PENDING}). Retry later."))
                return
            try:
                with self.lock:
                    line = json.dumps({"id": request_id, "request": request}, ensure_ascii=False) + "\n"
                    sys.stdout.buffer.write(line.encode())
                    sys.stdout.buffer.flush()
                waiter[0].wait()
                _send(conn, waiter[1] if isinstance(waiter[1], dict) else _error("internal", "No response."))
            except OSError:
                pass
            finally:
                with self.lock:
                    self.pending.pop(request_id, None)


def _read_request(conn: socket.socket) -> dict[str, Any]:
    conn.settimeout(READ_TIMEOUT)
    data = b""
    while b"\n" not in data:
        chunk = conn.recv(64 * 1024)
        if not chunk:
            break
        data += chunk
        if len(data) > MAX_REQUEST_BYTES:
            raise ValueError(f"The request is larger than {MAX_REQUEST_BYTES} bytes.")
    conn.settimeout(None)
    try:
        request = json.loads(data.split(b"\n", 1)[0] or b"{}")
    except ValueError as exc:
        raise ValueError(f"The request is not valid JSON: {exc}") from exc
    if not isinstance(request, dict):
        raise ValueError("The request must be a JSON object.")
    return request


def _send(conn: socket.socket, response: dict[str, Any]) -> None:
    conn.sendall(json.dumps(response, ensure_ascii=False).encode() + b"\n")


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


def _notify(event: dict[str, Any]) -> None:
    sys.stderr.write(json.dumps(event) + "\n")
    sys.stderr.flush()


def run_relay() -> int:
    return Relay().serve()
