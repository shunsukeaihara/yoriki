from types import SimpleNamespace

from harness.compaction import estimate_tokens, threshold
from harness.context import Item
from llm.providers.base import Message
from run.models import RunEvent, RunEventKind


def test_threshold_is_positive_for_small_windows():
    version = SimpleNamespace(max_input_tokens=8_192, max_output_tokens=4_096)
    assert threshold(version) >= 4_096  # type: ignore[arg-type]
    large = SimpleNamespace(max_input_tokens=200_000, max_output_tokens=16_000)
    assert threshold(large) == 150_000  # type: ignore[arg-type]


def test_estimate_uses_the_compacted_history_after_compaction():
    usage = {"input_tokens": 90_000, "output_tokens": 1_000}
    events = [
        RunEvent(sequence=1, kind=RunEventKind.USER_MESSAGE, data={"text": "依頼"}),
        RunEvent(sequence=2, kind=RunEventKind.ASSISTANT_MESSAGE, data={"text": "", "usage": usage}),
        RunEvent(sequence=3, kind=RunEventKind.COMPACTION, data={"from": 1, "to": 2, "text": "要約"}),
    ]
    items = [Item(1, RunEventKind.COMPACTION, Message("user", "要約"))]
    assert estimate_tokens(events, items) == 2
    # 圧縮の前なら、直前の要求の実際のtoken数から数える
    assert estimate_tokens(events[:2], items) == 91_000
