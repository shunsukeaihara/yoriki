"""SkillのスクリプトからConnector（社内DB等）を使う（design.md §4.9.6、sandbox/CONTRACT.md §1・§4.9）。

    import yoriki_connector

    result = yoriki_connector.query("販売DB", "SELECT code, name FROM vendors WHERE code = ?", ["A001"])
    for row in result.rows:
        print(row["code"], row["name"])

Sandboxにはネットワークがないので、要求はSandboxの中のUnixドメインソケットを通してRunnerへ届き、Runnerが
エージェントのTool（`datasource_query`・`datasource_describe`）と同じ確かめ方・上限で実行する。使えるConnectorは、
このRunでエージェントに見せているもの（`<connectors>` の名前）だけ。結果の全体はCSVのファイル
（/workspace/input/connectors/<呼出し>/result.csv。読取り専用）に置かれ、`rows` はそれを読んだもの。

Runnerは要求を、コマンドの終わりを待つ間（`shell`・`job_output`）に1つずつ処理する。バックグラウンドのジョブから
呼ぶと、エージェントが次に待つまで応答が返らないことがある。
"""

import csv
import datetime
import decimal
import json
import socket
import time
from dataclasses import dataclass, field
from typing import Any

__all__ = ["ConnectorError", "QueryResult", "describe", "query"]

SOCKET_PATH = "/tmp/yoriki/connector.sock"
# 応答の大きさの上限（バイト）。行はファイルで受け取るので、応答は小さい
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
# 頻度の上限で断られたときに、やり直すまでに待つ最長の時間（秒）
MAX_RETRY_WAIT = 60


class ConnectorError(Exception):
    """Connectorを使えなかった。`code` は理由の種類（`unavailable`・`rate_limited`・`limit_exceeded`・
    `invalid_argument`・Connectorのエラーの種類等）、`message` は直し方を含む説明"""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class QueryResult:
    # 結果の列の名前（SQLのとおり。重なることがある）
    columns: list[str]
    # 行。キーは列の名前で、同じ名前の列は2つめから `名前_2`・`名前_3`
    rows: list[dict[str, str]] = field(repr=False)
    # 読んだ行の数。上限で打ち切ったときは truncated が True（残りの行は読んでいない）
    row_count: int
    truncated: bool
    # 結果の全体のCSV（UTF-8、見出しの行つき）
    path: str


def query(
    connector: str, sql: str, params: list[Any] | tuple[Any, ...] = (), *, timeout: float | None = None
) -> QueryResult:
    """読取りだけのSELECT文を1つ実行する。値は `?` で書いて `params` に順に渡す（文字列として渡るので、
    数値・日付と比べるときはSQLの中でCASTする）。結果の値はすべて文字列（NULLは空文字）"""
    request = {"op": "query", "connector": connector, "sql": sql, "params": [_param(v) for v in params]}
    response = _call(request, timeout)
    path = response["path"]
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        columns = next(reader, [])
        keys = _unique(columns)
        rows = [dict(zip(keys, values, strict=False)) for values in reader]
    return QueryResult(
        columns=columns,
        rows=rows,
        row_count=response.get("row_count", len(rows)),
        truncated=bool(response.get("truncated")),
        path=path,
    )


def _unique(columns: list[str]) -> list[str]:
    """`rows` のキー。同じ名前の列（`SELECT a.id, b.id` 等）は、2つめから `id_2`・`id_3` とする"""
    keys: list[str] = []
    for name in columns:
        key, n = name, 1
        while key in keys:
            n += 1
            key = f"{name}_{n}"
        keys.append(key)
    return keys


def describe(connector: str, *, timeout: float | None = None) -> list[dict[str, Any]]:
    """Connectorが公開している表・ビューと列（名前・型・説明）"""
    return _call({"op": "describe", "connector": connector}, timeout)["tables"]


def _param(value: Any) -> str:
    if value is None:
        raise ConnectorError("invalid_argument", "A parameter is None. Write IS NULL in the SQL instead.")
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, str | int | float | decimal.Decimal):
        return str(value)
    if isinstance(value, datetime.date | datetime.datetime | datetime.time):
        return value.isoformat()
    raise ConnectorError("invalid_argument", f"A parameter of type {type(value).__name__} cannot be passed.")


def _call(request: dict[str, Any], timeout: float | None) -> dict[str, Any]:
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        response = _send(request, None if deadline is None else max(deadline - time.monotonic(), 0.1))
        if response.get("ok"):
            return response
        error = response.get("error") or {}
        code, message = str(error.get("code", "internal")), str(error.get("message", "The request failed."))
        if code in ("rate_limited", "busy"):
            # 頻度の上限：少し待ってやり直す
            wait = min(float(error.get("retry_after") or 1), MAX_RETRY_WAIT)
            if deadline is None or time.monotonic() + wait < deadline:
                time.sleep(wait)
                continue
        raise ConnectorError(code, message)


def _send(request: dict[str, Any], timeout: float | None) -> dict[str, Any]:
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        conn.settimeout(timeout)
        try:
            conn.connect(SOCKET_PATH)
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            raise ConnectorError(
                "unavailable", "Connectors cannot be used from scripts in this work (no connector is available)."
            ) from exc
        conn.sendall(json.dumps(request, ensure_ascii=False).encode() + b"\n")
        data = b""
        while b"\n" not in data:
            chunk = conn.recv(64 * 1024)
            if not chunk:
                break
            data += chunk
            if len(data) > MAX_RESPONSE_BYTES:
                raise ConnectorError("internal", "The response is too large.")
    except TimeoutError as exc:
        raise ConnectorError("timeout", "No response from the connector in time.") from exc
    finally:
        conn.close()
    if not data:
        raise ConnectorError("unavailable", "The connection was closed before a response. Try again.")
    response = json.loads(data.split(b"\n", 1)[0])
    if not isinstance(response, dict):
        raise ConnectorError("internal", "The response is not valid.")
    return response
