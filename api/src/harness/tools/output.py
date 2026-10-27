"""Tool結果の上限と退避（harness.md §7.7）。

1つのTool結果がモデルの文脈を占める量に上限を設け、超えたら一部だけを見せて、全体はSandboxの
`/workspace/work/.yoriki/outputs/{invocation}.txt` へ退避する（`read`・`grep` で読める）。
"""

from collections.abc import Callable
from dataclasses import dataclass

from ..untrusted import untrusted
from .base import ToolContext

# Tool結果の既定の上限（token）。モデルの文脈長の5%を超えない
DEFAULT_LIMIT = 8_000
CONTEXT_RATIO = 0.05
OUTPUTS_DIR = "/workspace/work/.yoriki/outputs"
# 退避するファイルの大きさの上限（文字）。超えた分は退避しない
SPILL_MAX_CHARS = 5_000_000
NO_OUTPUT = "(no output)"


def limit_for(context: ToolContext) -> int:
    window = context.run.model_version.max_input_tokens
    return max(1_000, min(DEFAULT_LIMIT, int(window * CONTEXT_RATIO)))


def estimate_tokens(text: str) -> int:
    """保守的な見積もり。ASCIIは4文字を1 token、それ以外（日本語等）は1文字を1 tokenとして数える"""
    ascii_chars = sum(1 for c in text if c < "\x80")
    return (ascii_chars + 3) // 4 + (len(text) - ascii_chars)


def _cost(c: str) -> float:
    return 0.25 if c < "\x80" else 1.0


def _head(text: str, budget: int) -> str:
    used = 0.0
    for index, c in enumerate(text):
        used += _cost(c)
        if used > budget:
            return text[:index]
    return text


def _tail(text: str, budget: int) -> str:
    used = 0.0
    for index in range(len(text) - 1, -1, -1):
        used += _cost(text[index])
        if used > budget:
            return text[index + 1 :]
    return text


@dataclass(frozen=True)
class Clipped:
    text: str
    truncated: bool
    total_tokens: int
    head_tokens: int = 0
    tail_tokens: int = 0


def clip(text: str, budget: int, *, head_ratio: float) -> Clipped:
    """先頭と末尾を残して切る。`head_ratio` が1なら先頭だけ"""
    total = estimate_tokens(text)
    if total <= budget:
        return Clipped(text, False, total)
    head_budget = int(budget * head_ratio)
    head = _head(text, head_budget)
    tail = _tail(text, budget - head_budget) if head_ratio < 1 else ""
    marker = "\n…\n" if tail else "\n…"
    return Clipped(head + marker + tail, True, total, estimate_tokens(head), estimate_tokens(tail))


def spill_path(context: ToolContext) -> str:
    return f"{OUTPUTS_DIR}/{context.invocation or context.call.id}.txt"


def spill(context: ToolContext, text: str) -> tuple[str, bool]:
    """全体をSandboxへ退避し、（場所、全体を退避できたか）を返す。退避できなければ場所は空文字"""
    path = spill_path(context)
    content = text if len(text) <= SPILL_MAX_CHARS else text[:SPILL_MAX_CHARS]
    result = context.sandbox_session().helper("write", {"path": path, "content": content})
    return (path if result.get("ok") else ""), content is text


def limited(
    context: ToolContext,
    text: str,
    *,
    head_ratio: float = 0.5,
    full_text: str | None = None,
    hint: str = "",
    spill_to: Callable[[str], bool] | None = None,
    source: str = "",
    reserve: int = 0,
) -> str:
    """上限を超えたら切って退避し、そのことを末尾に書く。`full_text` があれば、それを退避する
    （表示用の見出しを除く等）。`spill_to` があれば、退避はそれに任せる（ジョブの出力をそのまま書く等）。
    `source` があれば、見せる部分を信頼しない内容として囲む（harness.md §6.3）。切ったことの案内は囲みの外に書く。
    `reserve` は、同じ結果に別に書く見出し等のtoken。上限からその分を引く"""
    if not text.strip():
        return NO_OUTPUT
    limit = limit_for(context)
    clipped = clip(text, max(limit - reserve, limit // 2), head_ratio=head_ratio)
    body = untrusted(clipped.text, source, block=True) if source else clipped.text
    if not clipped.truncated:
        return body
    if spill_to is not None:
        path = spill_path(context)
        path, complete = (path if spill_to(path) else ""), True
    else:
        path, complete = spill(context, full_text if full_text is not None else text)
    if head_ratio < 1:
        shown = f"the first {clipped.head_tokens:,} and last {clipped.tail_tokens:,}"
    else:
        shown = f"the first {clipped.head_tokens:,}"
    lines = [body, f"[output truncated: showing {shown} of about {clipped.total_tokens:,} tokens]"]
    if path:
        context.spilled.append(path)
    if path and complete:
        lines.append(f"[full output: {path} — use read with offset/limit, or grep, to look inside]")
    elif path:
        lines.append(f"[the first {SPILL_MAX_CHARS:,} characters of the output: {path}; the rest was not saved]")
    if hint:
        lines.append(hint)
    return "\n".join(lines)
