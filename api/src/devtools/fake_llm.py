"""確認用の偽のLLM。OpenAI互換のChat Completions（ストリーミング）とモデルの一覧を返す。

台本（JSON）のルールを上から順に照らし、最初に合ったルールの応答を返す。台本は要求ごとに読み直す（更新日時が
変わったときだけ）ので、サーバーを止めずに書き換えられる。台本の書き方は docs/development.md §6.2
"""

import hashlib
import json
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from llm.providers.openai import TOOL_IMAGES_LABEL

DEFAULT_SCENARIO = Path(__file__).with_name("scenarios") / "default.json"
DEFAULT_MODELS = ["fake", "fake-reasoning"]

WHEN_KEYS = {"prompt", "step", "tools", "called", "result", "system", "model", "match"}
REPLY_KEYS = {"text", "tool_calls", "finish", "delay", "chunk_delay", "status", "error"}
FINISH_REASONS = {"stop", "length", "tool_calls"}
VARIABLE = re.compile(r"\$\{([^}]+)\}")
# Toolの結果の画像を移した利用者の発言（llm.providers.openai）。利用者の発言としては数えない
IMAGES_LABEL_PREFIX = TOOL_IMAGES_LABEL.split("{id}")[0]
CHUNK_CHARS = 20
FALLBACK_REPLY = {"text": "（偽のLLM）合うルールがありませんでした。"}


class ScenarioError(ValueError):
    pass


@dataclass(frozen=True)
class Turn:
    """要求から読み取った、ルールと照らす値。

    「今の依頼」は、Toolを呼ばずに終わった最後のassistantの発言（前の依頼への答え）より後。利用者の発言の形の
    Runnerの知らせ（`<runtime_context>`、ジョブの終了等）も、今の依頼の発言に含める。「歩」は今の依頼の中の
    assistantの発言の数
    """

    model: str
    system: str
    prompt: str  # 今の依頼の利用者の発言（Toolの結果の画像を移した発言を除く）をつないだもの
    first_prompt: str  # 今の依頼の最初の利用者の発言
    step: int
    tools: dict[str, dict[str, Any]]  # 名前 → parameters
    called: str  # 直前のassistantの発言が呼んだToolの名前（複数なら最後のもの）
    result: str  # 今の依頼の最後のToolの結果
    texts: str  # 今の依頼のすべての文。`match` の対象
    key: str  # 要求の中身のhash。同じ要求の再試行を数える

    @classmethod
    def parse(cls, body: dict[str, Any]) -> "Turn":
        messages = body.get("messages") or []
        system = "\n".join(_text(m.get("content")) for m in messages if m.get("role") == "system")
        answered = max(
            (i for i, m in enumerate(messages) if m.get("role") == "assistant" and not m.get("tool_calls")), default=-1
        )
        turn = [m for m in messages[answered + 1 :] if m.get("role") != "system"]
        prompts = [_text(m.get("content")) for m in turn if _is_prompt(m)]
        assistants = [m for m in turn if m.get("role") == "assistant"]
        calls = (assistants[-1].get("tool_calls") or []) if assistants else []
        results = [m for m in turn if m.get("role") == "tool"]
        tools = {
            t["function"]["name"]: t["function"].get("parameters") or {}
            for t in body.get("tools") or []
            if t.get("type") == "function"
        }
        return cls(
            model=str(body.get("model", "")),
            system=system,
            prompt="\n".join(prompts),
            first_prompt=prompts[0] if prompts else "",
            step=len(assistants),
            tools=tools,
            called=calls[-1]["function"]["name"] if calls else "",
            result=_text(results[-1].get("content")) if results else "",
            texts="\n".join(_text(m.get("content")) for m in turn),
            key=hashlib.sha256(
                json.dumps([body.get("model"), messages], ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest(),
        )

    def variables(self) -> dict[str, str]:
        first_line = self.first_prompt.strip().splitlines()[0] if self.first_prompt.strip() else ""
        return {
            "prompt": first_line[:100],
            "step": str(self.step),
            "called": self.called,
            "result": self.result[:1000],
            "model": self.model,
        }

    def enum(self, tool: str, parameter: str) -> str | None:
        """Toolの引数のenumの最初の値（Skillの名前、Connectorの名前）"""
        values = (self.tools.get(tool, {}).get("properties") or {}).get(parameter, {}).get("enum") or []
        return str(values[0]) if values else None


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _is_prompt(message: dict[str, Any]) -> bool:
    return message.get("role") == "user" and not _text(message.get("content")).startswith(IMAGES_LABEL_PREFIX)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


@dataclass(frozen=True)
class Rule:
    index: int
    name: str
    when: dict[str, Any]
    reply: dict[str, Any]
    times: int | None  # 同じ要求に、最初の何回だけ合うか（再試行の確認）

    def match(self, turn: Turn) -> dict[str, str] | None:
        """合えば、`match` の名前付きのグループの値を返す"""
        captured: dict[str, str] = {}
        for name, expected in self.when.items():
            match name:
                case "prompt":
                    ok = all(s in turn.prompt for s in _as_list(expected))
                case "result":
                    ok = all(s in turn.result for s in _as_list(expected))
                case "system":
                    ok = all(s in turn.system for s in _as_list(expected))
                case "tools":
                    ok = all(t in turn.tools for t in _as_list(expected))
                case "step":
                    ok = turn.step in _as_list(expected)
                case "called":
                    ok = turn.called in _as_list(expected)
                case "model":
                    ok = turn.model in _as_list(expected)
                case "match":
                    found = re.search(expected, turn.texts)
                    ok = found is not None
                    if found:
                        captured |= {k: v for k, v in found.groupdict().items() if v is not None}
            if not ok:
                return None
        return captured


class Scenario:
    def __init__(self, data: Any, *, source: str = ""):
        if not isinstance(data, dict):
            raise ScenarioError(f"{source}: 台本はオブジェクトにしてください")
        unknown = set(data) - {"models", "rules", "default"}
        if unknown:
            raise ScenarioError(f"{source}: 知らない項目があります：{', '.join(sorted(unknown))}")
        self.models: list[str] = data.get("models") or DEFAULT_MODELS
        if not isinstance(self.models, list) or not all(isinstance(m, str) for m in self.models):
            raise ScenarioError(f"{source}: models は文字列の配列にしてください")
        rules = data.get("rules", [])
        if not isinstance(rules, list):
            raise ScenarioError(f"{source}: rules は配列にしてください")
        self.rules = [self._rule(i, r, source) for i, r in enumerate(rules)]
        self.default = _check_reply(data.get("default", FALLBACK_REPLY), f"{source}: default")

    @classmethod
    def load(cls, path: Path) -> "Scenario":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ScenarioError(f"{path}: 読めません：{exc}") from exc
        return cls(data, source=str(path))

    @staticmethod
    def _rule(index: int, data: Any, source: str) -> Rule:
        label = f"{source}: rules[{index}]"
        if not isinstance(data, dict) or set(data) - {"name", "when", "reply", "times"}:
            raise ScenarioError(f"{label}: name・when・reply・times だけを書けます")
        when = data.get("when", {})
        if not isinstance(when, dict) or set(when) - WHEN_KEYS:
            raise ScenarioError(f"{label}: when に書けるのは {', '.join(sorted(WHEN_KEYS))} です")
        if "match" in when:
            try:
                re.compile(when["match"])
            except (re.error, TypeError) as exc:
                raise ScenarioError(f"{label}: match の正規表現が正しくありません：{exc}") from exc
        times = data.get("times")
        if times is not None and (not isinstance(times, int) or times < 1):
            raise ScenarioError(f"{label}: times は1以上の整数にしてください")
        name = data.get("name") or f"rules[{index}]"
        return Rule(index, name, when, _check_reply(data.get("reply", {}), label), times)


def _check_reply(reply: Any, label: str) -> dict[str, Any]:
    if not isinstance(reply, dict) or set(reply) - REPLY_KEYS:
        raise ScenarioError(f"{label}: reply に書けるのは {', '.join(sorted(REPLY_KEYS))} です")
    for call in reply.get("tool_calls", []):
        if not isinstance(call, dict) or not isinstance(call.get("name"), str):
            raise ScenarioError(f"{label}: tool_calls の要素には name を書いてください")
    if reply.get("finish", "stop") not in FINISH_REASONS:
        raise ScenarioError(f"{label}: finish は {', '.join(sorted(FINISH_REASONS))} のどれかにしてください")
    return reply


def substitute(value: Any, turn: Turn, captured: dict[str, str]) -> Any:
    """文字列の中の `${名前}` を置き換える。知らない名前はそのまま残す（シェルの変数を書けるように）"""
    if isinstance(value, str):
        known = turn.variables() | captured

        def replace(found: re.Match[str]) -> str:
            name = found[1]
            if name.startswith("enum:") and "." in name:
                tool, parameter = name.removeprefix("enum:").split(".", 1)
                resolved = turn.enum(tool, parameter)
                return found[0] if resolved is None else resolved
            return known.get(name, found[0])

        return VARIABLE.sub(replace, value)
    if isinstance(value, list):
        return [substitute(v, turn, captured) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, turn, captured) for k, v in value.items()}
    return value


class Responder:
    """台本を読み、要求に応答を選ぶ。HTTPから切り離してテストする"""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._mtime = 0.0
        self._scenario: Scenario | None = None
        self._seen: dict[tuple[int, str], int] = {}

    def scenario(self) -> Scenario:
        with self._lock:
            mtime = self.path.stat().st_mtime
            if self._scenario is None or mtime != self._mtime:
                self._scenario = Scenario.load(self.path)
                self._mtime = mtime
                self._seen.clear()
            return self._scenario

    def choose(self, body: dict[str, Any]) -> tuple[str, Turn, dict[str, Any]]:
        """（ルールの名前、要求、変数を置き換えた応答）"""
        scenario = self.scenario()
        turn = Turn.parse(body)
        for rule in scenario.rules:
            captured = rule.match(turn)
            if captured is None:
                continue
            if rule.times is not None:
                with self._lock:
                    count = self._seen.get((rule.index, turn.key), 0)
                    if count >= rule.times:
                        continue
                    self._seen[(rule.index, turn.key)] = count + 1
            return rule.name, turn, substitute(rule.reply, turn, captured)
        return "default", turn, substitute(scenario.default, turn, {})


def chunks(reply: dict[str, Any], *, model: str, prompt_tokens: int) -> Iterator[dict[str, Any]]:
    """応答を `chat.completion.chunk` の並びにする（最後はusageだけの要素）"""
    base = {"id": f"chatcmpl-{uuid.uuid4().hex[:12]}", "object": "chat.completion.chunk", "created": int(time.time())}
    base["model"] = model

    def delta(value: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
        return base | {"choices": [{"index": 0, "delta": value, "finish_reason": finish}]}

    text = str(reply.get("text", ""))
    size = CHUNK_CHARS if reply.get("chunk_delay") else max(len(text), 1)
    for i in range(0, len(text), size):
        yield delta({"role": "assistant", "content": text[i : i + size]})
    calls = reply.get("tool_calls", [])
    for i, call in enumerate(calls):
        arguments = call.get("arguments", {})
        yield delta(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": i,
                        "id": f"call_{uuid.uuid4().hex[:12]}",
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": arguments
                            if isinstance(arguments, str)
                            else json.dumps(arguments, ensure_ascii=False),
                        },
                    }
                ],
            }
        )
    yield delta({}, reply.get("finish") or ("tool_calls" if calls else "stop"))
    completion_tokens = max(1, (len(text) + len(json.dumps(calls, ensure_ascii=False))) // 4)
    yield base | {
        "choices": [],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


class FakeLLMServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        responder: Responder,
        *,
        log: Callable[[str], None] = print,
        dump_dir: Path | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        super().__init__(address, Handler)
        self.responder = responder
        self.log = log
        self.dump_dir = dump_dir
        self.sleep = sleep
        self._count = 0
        self._count_lock = threading.Lock()

    def next_number(self) -> int:
        with self._count_lock:
            self._count += 1
            return self._count


class Handler(BaseHTTPRequestHandler):
    server: FakeLLMServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        pass  # 要求ごとの1行は do_POST で出す

    def do_GET(self) -> None:
        if not self.path.rstrip("/").endswith("/models"):
            return self._json(404, {"error": {"message": "not found"}})
        try:
            models = self.server.responder.scenario().models
        except ScenarioError as exc:
            return self._json(500, {"error": {"message": str(exc)}})
        self._json(
            200, {"object": "list", "data": [{"id": m, "object": "model", "owned_by": "yoriki"} for m in models]}
        )

    def do_POST(self) -> None:
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._json(404, {"error": {"message": "not found"}})
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            body = json.loads(raw)
            name, turn, reply = self.server.responder.choose(body)
        except (json.JSONDecodeError, ScenarioError) as exc:
            self.server.log(f"エラー：{exc}")
            return self._json(500, {"error": {"message": str(exc)}})
        number = self.server.next_number()
        self.server.log(
            f"#{number} {turn.model} 歩={turn.step} 直前={turn.called or '-'} "
            f"発言={turn.variables()['prompt'][:30]!r} → {name}"
        )
        if self.server.dump_dir is not None:
            path = self.server.dump_dir / f"{number:04d}.json"
            path.write_text(json.dumps({"rule": name, "request": body}, ensure_ascii=False, indent=2), encoding="utf-8")
        self.server.sleep(float(reply.get("delay", 0)))
        if reply.get("status"):
            message = reply.get("error") or "fake error"
            return self._json(int(reply["status"]), {"error": {"message": message, "type": "fake_error"}})
        if not body.get("stream"):
            return self._json(400, {"error": {"message": "偽のLLMはストリーミングの要求だけに答えます"}})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        chunk_delay = float(reply.get("chunk_delay", 0))
        try:
            for chunk in chunks(reply, model=turn.model, prompt_tokens=max(1, len(raw) // 4)):
                self.wfile.write(b"data: " + json.dumps(chunk, ensure_ascii=False).encode() + b"\n\n")
                self.wfile.flush()
                if chunk_delay and chunk["choices"]:
                    self.server.sleep(chunk_delay)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            self.server.log(f"#{number} 応答の途中で接続が切れました")

    def _json(self, status: int, data: dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
