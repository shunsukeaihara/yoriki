"""Toolの定義（harness.md §7.1）。モデルに送るのは名前・説明・引数のSchemaだけで、ほかはサーバーだけが使う"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Any

import jsonschema

from llm.providers.base import Part, TextPart, ToolCall, ToolDefinition

if TYPE_CHECKING:
    from run.models import ExecutionAttempt, Run


class Effect(IntEnum):
    READ = 1  # 読むだけ
    WRITE_SANDBOX = 2  # Sandboxの中だけを変える
    WRITE_EXTERNAL = 3  # 外部の確定した変更
    INTERACT = 4  # 利用者とのやり取り


@dataclass(frozen=True)
class ToolContext:
    run: "Run"
    attempt: "ExecutionAttempt"
    call: ToolCall


@dataclass(frozen=True)
class ToolResult:
    content: tuple[Part, ...]
    is_error: bool = False
    value: Any = None  # 型のある値。ToolInvocationに保存し、画面と業務結果に使う
    meta: dict[str, Any] = field(default_factory=dict)  # 画面用の情報
    # 利用者の回答を待つ（ask_user）。Runnerは結果を記録せずにWAITING_USERにする（harness.md §10.3）
    waits_for_user: bool = False

    @classmethod
    def text(cls, text: str, **kwargs) -> "ToolResult":
        return cls((TextPart(text),), **kwargs)

    @classmethod
    def error(cls, text: str) -> "ToolResult":
        return cls((TextPart(text),), is_error=True)


def _never(_args: dict[str, Any]) -> bool:
    return False


@dataclass(frozen=True)
class Tool:
    name: str
    description: str  # モデル向けの説明（英語。harness.md §6.1）
    parameters: dict[str, Any]
    execute: Callable[[dict[str, Any], ToolContext], ToolResult]
    effect: Effect
    # この引数なら並列に実行してよいか（harness.md §7.6）
    concurrent: Callable[[dict[str, Any]], bool] = _never
    # 障害の後に、同じ引数で自動でやり直してよいか（harness.md §11.1）
    replay_safe: bool = False
    # 同じ応答の中で、ほかのToolと一緒に呼ばせない（ask_user、submit_result）
    alone: bool = False

    def definition(self) -> ToolDefinition:
        return ToolDefinition(self.name, self.description, self.parameters)

    def validate(self, arguments: dict[str, Any]) -> str:
        """引数の誤りを、モデルが直せる文にして返す。誤りがなければ空文字"""
        if "_raw" in arguments:
            return f"The arguments are not valid JSON: {arguments['_raw'][:500]}"
        errors = sorted(jsonschema.Draft202012Validator(self.parameters).iter_errors(arguments), key=str)
        if not errors:
            return ""
        lines = [f'Invalid arguments for tool "{self.name}":']
        for error in errors[:5]:
            path = ".".join(str(p) for p in error.absolute_path) or "(root)"
            lines.append(f"- {path}: {error.message}")
        lines.append(f"Arguments provided: {_short_json(arguments)}")
        lines.append("Fix the arguments and call the tool again.")
        return "\n".join(lines)


def _short_json(value: Any) -> str:
    import json  # noqa: PLC0415  エラーの文を作るときだけ使う

    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 1000 else text[:1000] + "…"
