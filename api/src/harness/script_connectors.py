"""SkillのスクリプトからConnectorを使う（design.md §4.9.6、harness.md §10.2）。

スクリプトは `yoriki_connector`（Sandboxのイメージのライブラリ）で要求を送り、Sandboxの中の中継
（`yoriki-sandbox relay`）と `docker exec` の通信路を通ってRunnerに届く。要求は、エージェントのTool
（`datasource_query`・`datasource_describe`）と同じ処理（harness/tools/datasource.py）で実行する。Runに固定した
Connectorの一覧、操作のたびの権限の確かめ直し（依頼した人が今も使えるか、タスクのRunならタスクに固定したもので
範囲の中か、機密ならローカルLLMか）、読取り専用の強制、行数・大きさ・時間の上限はToolと同じで、スクリプトからの
呼出しだからといって範囲を広げない。呼べるのは、そのRunでエージェントにそのToolを出しているときだけ。

要求はRunnerのスレッドで1つずつ処理する（記録と接続の枠をDBで扱うため。Toolと同じ）。呼出しはToolInvocationに
記録し（`call_id` は `script-` で始まる）、モデルの文脈（RunEvent）には入れない。結果はモデルではなくスクリプトに
返す。スクリプトの不具合で社内DBに大量の問い合わせが流れないよう、1つのRunの回数と、1分あたりの頻度に上限を置く。
"""

import json
import logging
import time
from collections import deque
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from django.utils import timezone
from nanoid import generate

from llm.providers.base import TextPart, ToolCall
from run.errors import LeaseLost
from run.models import SCRIPT_CALL_PREFIX, ExecutionAttempt, Run, ToolInvocation, ToolStatus
from runtime.launcher import RELAY_MAX_RESPONSE, SandboxUnavailable

from .connectors import DATASOURCE_TOOL_NAMES, catalog_names
from .tools.base import ToolContext, ToolResult
from .tools.datasource import DATASOURCE_TOOLS, Described, Queried, describe_tables, run_query

logger = logging.getLogger(__name__)

DESCRIBE_NAME, QUERY_NAME = DATASOURCE_TOOL_NAMES
OPERATIONS = {"describe": DESCRIBE_NAME, "query": QUERY_NAME}
# 1つのRunでスクリプトから呼べる回数（Attemptをまたいで数える）と、1分あたりの回数
MAX_CALLS_PER_RUN = 1000
MAX_CALLS_PER_MINUTE = 120
# 引数（SQLと値）の大きさの上限（バイト）。ToolInvocationに記録するので、1つのRunの記録が1,000回×これまでに収まる
MAX_ARGUMENT_BYTES = 64 * 1024
# スクリプトへ返す応答の大きさの上限（バイト）。中継で返せる大きさ（包む分を除く）
MAX_RESPONSE_BYTES = RELAY_MAX_RESPONSE - 1024


class ScriptConnectors:
    """1つのAttemptで、スクリプトからの要求を処理する。Runnerのスレッドからだけ呼ぶ"""

    def __init__(
        self,
        run: Run,
        attempt: ExecutionAttempt,
        *,
        session,
        exposed: Callable[[], frozenset[str]],
        current_call: Callable[[], str],
        clock: Callable[[], float] = time.monotonic,
    ):
        self.run = run
        self.attempt = attempt
        self.session = session
        # このRunに今出しているToolの名前と、今実行しているToolの呼出しのID（あれば）
        self.exposed = exposed
        self.current_call = current_call
        self.clock = clock
        self.recent: deque[float] = deque()
        self.count: int | None = None

    def available(self) -> bool:
        """このRunでスクリプトからConnectorを呼べるか（中継を始めるかの判断）"""
        return bool(set(DATASOURCE_TOOL_NAMES) & self.exposed())

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        """要求を処理して、スクリプトへ返す応答を作る。頻度の上限は、断る要求も含めて数える（中継はSandboxの
        中のもので、不正な要求を流し続けられるため）"""
        if limited := self._rate_limit():
            return limited
        checked = self._check(request)
        if isinstance(checked, dict):
            return checked
        if limited := self._run_limit():
            return limited
        return self._execute(*checked)

    def _check(self, request: dict[str, Any]) -> tuple[str, dict[str, Any]] | dict[str, Any]:
        """（Toolの名前、引数）か、断る応答"""
        operation = request.get("op")
        name = OPERATIONS.get(operation) if isinstance(operation, str) else None
        if name is None:
            return _error("invalid_argument", 'op must be "query" or "describe".')
        names = catalog_names(self.run)
        if name not in self.exposed() or not names:
            return _error(
                "unavailable",
                f"Connectors cannot be used with {operation}() in this work: the {name} tool is not available.",
            )
        # 引数はToolと同じSchemaで確かめる（知らない項目も断る）
        arguments = {key: value for key, value in request.items() if key != "op"}
        if len(json.dumps(arguments, ensure_ascii=False).encode()) > MAX_ARGUMENT_BYTES:
            # 引数は記録に残すので、Sandboxから大きな引数を送られても記録を大きくしない
            return _error("invalid_argument", f"The SQL and parameters must be at most {MAX_ARGUMENT_BYTES} bytes.")
        tool = next(t for t in DATASOURCE_TOOLS if t.name == name)
        if problem := tool.validate(arguments):
            return _error("invalid_argument", problem)
        if arguments["connector"] not in names:
            return _error("invalid_argument", f"No such connector. Available connectors: {', '.join(names[:20])}.")
        return name, arguments

    def _rate_limit(self) -> dict[str, Any] | None:
        now = self.clock()
        if self.count is None:
            # 前のAttemptの直近1分の呼出し（記録したもの）も数える（Attemptをやり直しても数え直さない）
            since = timezone.now() - timedelta(seconds=60)
            for created in (
                ToolInvocation.objects.filter(run=self.run, call_id__startswith=SCRIPT_CALL_PREFIX, created__gt=since)
                .values_list("created", flat=True)
                .order_by("created")
            ):
                self.recent.append(now - (timezone.now() - created).total_seconds())
            self.count = ToolInvocation.objects.filter(run=self.run, call_id__startswith=SCRIPT_CALL_PREFIX).count()
        while self.recent and now - self.recent[0] >= 60:
            self.recent.popleft()
        if len(self.recent) >= MAX_CALLS_PER_MINUTE:
            retry_after = max(60 - (now - self.recent[0]), 0.1)
            return _error(
                "rate_limited",
                f"Too many connector calls from scripts (up to {MAX_CALLS_PER_MINUTE} per minute).",
                retry_after=round(retry_after, 1),
            )
        self.recent.append(now)
        return None

    def _run_limit(self) -> dict[str, Any] | None:
        assert self.count is not None  # _rate_limit() で読んだ
        if self.count >= MAX_CALLS_PER_RUN:
            return _error(
                "limit_exceeded",
                f"This work has reached the limit of {MAX_CALLS_PER_RUN} connector calls from scripts. Fetch "
                "the rows you need with fewer queries (for example WHERE ... IN (...) or one query for the whole "
                "table), then join them in the script.",
            )
        self.count += 1
        return None

    def _execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        call = ToolCall(f"{SCRIPT_CALL_PREFIX}{generate(size=16)}", name, arguments)
        origin = {"via": "script", "during": self.current_call()}
        with self.run.fenced(self.attempt) as run:
            invocation = ToolInvocation.objects.create(
                run=run,
                attempt=self.attempt,
                call_id=call.id,
                name=name,
                arguments=arguments,
                status=ToolStatus.RUNNING,
                value=origin,
            )
        context = ToolContext(
            self.run, self.attempt, call, invocation=invocation.uid, session=self.session, exposed=self.exposed()
        )
        outcome: Described | Queried | ToolResult
        try:
            if name == DESCRIBE_NAME:
                outcome = describe_tables(arguments, context)
            else:
                # 行はファイルで渡すので、メモリには残さない
                outcome = run_query(arguments, context, keep=0)
        except (LeaseLost, SandboxUnavailable):
            # Attemptを終える。実行中の記録は、Attemptの終わりに結果不明として閉じる
            raise
        except Exception:
            logger.exception("run %s: a connector call from a script failed", self.run.uid)
            outcome = ToolResult.text("The request failed.", is_error=True, value={"error": "internal"})
        error = ""
        if isinstance(outcome, ToolResult):
            value = origin | (outcome.value or {})
            error = _text(outcome)
            response = _error(str(value.get("error") or "unavailable"), error)
            status = ToolStatus.FAILED
        else:
            value = origin | outcome.value()
            response = _described(outcome) if isinstance(outcome, Described) else _queried(outcome)
            status = ToolStatus.SUCCEEDED
            if len(json.dumps(response, ensure_ascii=False).encode()) > MAX_RESPONSE_BYTES:
                # 中継で返せる大きさを超える（表・列の多いdescribe）。記録も失敗にする
                error = "The result is too large to return to the script. Use datasource_describe as a tool instead."
                response = _error("too_large", error)
                value["error"] = "too_large"
                status = ToolStatus.FAILED
        with self.run.fenced(self.attempt):
            # 失敗の理由（スクリプトへ返した説明）は、画面の作業の記録に出す
            ToolInvocation.objects.filter(pk=invocation.pk).update(
                status=status, value=value, error=error, ended=timezone.now()
            )
        return response


def _described(described: Described) -> dict[str, Any]:
    return {
        "ok": True,
        "connector": described.connector.name,
        "version": described.version.number,
        "tables": [
            {
                "schema": table.schema,
                "name": table.name,
                "kind": table.kind,
                "description": table.description,
                "columns": [
                    {"name": column.name, "type": column.type, "description": column.description}
                    for column in table.columns
                ],
            }
            for table in described.tables
        ],
    }


def _queried(queried: Queried) -> dict[str, Any]:
    result = queried.result
    return {
        "ok": True,
        "connector": queried.connector.name,
        "version": queried.version.number,
        "path": queried.path,
        "columns": list(result.columns),
        "row_count": result.row_count,
        "truncated": result.truncated,
    }


def _text(result: ToolResult) -> str:
    return "".join(part.text for part in result.content if isinstance(part, TextPart))


def _error(code: str, message: str, **detail: Any) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, **detail}}
