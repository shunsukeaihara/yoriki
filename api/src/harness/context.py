"""RunEventからモデルへの要求を組み立てる（harness.md §5）。

同じRunEventの並びとモデル設定の版からは、どのWorkerでも同じ要求になる。圧縮は範囲の置き換えとして読む。
同じ会話の次の依頼（子のRun）は、親のRunの文脈をそのまま先頭に引き継ぐ（design.md §5.1、harness.md §5.6）。
"""

import functools
import io
import logging
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from django.utils import timezone

from artifact.models import BlobObject
from artifact.storage import StorageError
from llm.providers.base import ImagePart, Message, Part, TextPart, ToolCall
from run.models import Run, RunEvent, RunEventKind
from runtime.launcher import WORK_DIR

from .skills import catalog_text

logger = logging.getLogger(__name__)

ADDITIONAL_INSTRUCTION_PREFIX = "[Additional instruction during the work]"
RUNTIME_CONTEXT_TAG = "<runtime_context>"


# 1回の要求でモデルに見せる画像の数。古いものから文に置き換える（各社の1回の上限より十分に小さくする）
MAX_IMAGES_PER_REQUEST = 20
IMAGE_UNAVAILABLE = "[An image was here, but it is no longer available. Read the file again if you need it.]"
IMAGE_OMITTED = "[An older image was omitted to save context. Read the file again if you need it.]"
IMAGE_UNSUPPORTED = "[An image was here, but the current model does not accept images.]"


def parts_to_json(parts: Iterable[Part]) -> list[dict[str, Any]]:
    """RunEventに保存する形。画像のbytesは保存先に書き、BlobObjectの参照だけを残す（RunEventに大きなbytesを
    入れない）。書いてからRunEventを記録するまでに失敗したものは、参照のないobjectとして清掃で消す"""
    result: list[dict[str, Any]] = []
    for part in parts:
        if isinstance(part, TextPart):
            result.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            blob = BlobObject.put(io.BytesIO(part.data))
            result.append({"type": "image", "media_type": part.media_type, "blob": blob.uid, "size": blob.size})
        else:
            raise ValueError(f"unsupported part for storage: {type(part).__name__}")
    return result


@dataclass(frozen=True)
class StoredImagePart(ImagePart):
    """RunEventから読んだ画像の参照。bytesはまだ読まず、送る画像を決めてから fit_to_model() が読む"""

    blob: str = ""


def parts_from_json(data: list[dict[str, Any]]) -> tuple[Part, ...]:
    parts: list[Part] = []
    for item in data:
        if item.get("type") == "text":
            parts.append(TextPart(item["text"]))
        elif item.get("type") == "image":
            parts.append(StoredImagePart(item["media_type"], b"", item["blob"]))
    return tuple(parts)


def _blob_bytes(uid: str) -> bytes | None:
    try:
        return _read_blob(uid)
    except (BlobObject.DoesNotExist, StorageError, OSError):
        logger.warning("image blob %s is not available", uid)
        return None


# 要求のたびにRunEventから組み立て直すので、読んだ画像をWorkerのプロセスに残す（BlobObjectは不変）。
# 失敗は残さない（例外はcacheされない）ので、保存先の一時的な障害が直れば次の要求で読み直す
@functools.lru_cache(maxsize=MAX_IMAGES_PER_REQUEST * 2)
def _read_blob(uid: str) -> bytes:
    with BlobObject.objects.get(uid=uid).open() as src:
        return src.read()


def fit_to_model(messages: list[Message], *, images: bool) -> list[Message]:
    """モデルの能力と上限に合わせる（harness.md §5.4、§12）。画像に対応しないモデル（途中で切り替えた等）には
    画像の代わりに断りの文を、対応するモデルには新しい側の MAX_IMAGES_PER_REQUEST 枚までを送る。
    保存した画像のbytesは、送ると決めたものだけを読む"""
    keep = MAX_IMAGES_PER_REQUEST if images else 0
    result: list[Message] = []
    for message in reversed(messages):
        if not any(isinstance(p, ImagePart) for p in message.parts):
            result.append(message)
            continue
        parts: list[Part] = []
        for part in reversed(message.parts):
            if isinstance(part, ImagePart):
                keep -= 1
                if keep < 0:
                    parts.append(TextPart(IMAGE_OMITTED if images else IMAGE_UNSUPPORTED))
                    continue
                if isinstance(part, StoredImagePart):
                    content = _blob_bytes(part.blob)
                    parts.append(ImagePart(part.media_type, content) if content else TextPart(IMAGE_UNAVAILABLE))
                    continue
            parts.append(part)
        result.append(replace(message, content=tuple(reversed(parts))))
    return list(reversed(result))


def provider_key(run: Run) -> dict[str, str]:
    version = run.model_version
    return {"connection": version.connection.uid, "model": version.model_name}


@dataclass(frozen=True)
class Item:
    """モデルに見せる履歴の1件と、その元のRunEvent"""

    sequence: int
    kind: int
    message: Message


# 引き継いだ文脈の項目の sequence。このRunの記録（1から）より前に並び、圧縮では1つのまとまりとして扱う
INHERITED_SEQUENCE = 0
UNFINISHED_CALL = (
    "[The earlier request ended before this tool call finished, so its result is unknown. Check the current state "
    "before relying on it.]"
)


def build_items(run: Run, events: Iterable[RunEvent], inherited: Iterable[Item] = ()) -> list[Item]:
    """モデルに見せる履歴。`inherited`（inherited_items()）を先頭に置く。
    推論の途中経過は、同じ接続・同じモデルの応答のものだけを戻す（harness.md §5.5）。画像は今のモデルに合わせる
    （圧縮の見積もりも、実際に送る画像で数える）"""
    items = _build(provider_key(run), events, list(inherited))
    messages = fit_to_model([i.message for i in items], images=run.model_version.supports_images)
    return [replace(item, message=message) for item, message in zip(items, messages, strict=True)]


def inherited_items(run: Run) -> list[Item]:
    """親のRun（同じ会話の前の依頼）の、最後にモデルへ送った文脈と同じ履歴。親のさらに前も、親が引き継いだものとして
    含む。親は終わっていて記録は変わらないので、Runnerは1回だけ読む。結果のないまま終わったTool呼出しには、
    結果が分からないことを結果として添える（プロバイダは呼出しと結果の対応を求める）"""
    return [Item(INHERITED_SEQUENCE, item.kind, item.message) for item in _ancestor_items(run, provider_key(run))]


def _ancestor_items(run: Run, key: dict[str, str]) -> list[Item]:
    parent = run.parent
    if parent is None:
        return []
    events = list(RunEvent.objects.filter(run=parent).order_by("sequence"))
    # 親が引き継いだ部分をまるごと圧縮していれば、さらに前は読まなくてよい
    covered = any(e.kind == RunEventKind.COMPACTION and e.data["from"] <= INHERITED_SEQUENCE for e in events)
    above = [] if covered else [Item(INHERITED_SEQUENCE, i.kind, i.message) for i in _ancestor_items(parent, key)]
    return _close_unfinished_calls(_build(key, events, above))


def _close_unfinished_calls(items: list[Item]) -> list[Item]:
    result: list[Item] = []
    pending: dict[str, tuple[Item, str]] = {}

    def flush() -> None:
        for call_id, (owner, name) in pending.items():
            message = Message("tool", UNFINISHED_CALL, tool_call_id=call_id, tool_name=name, is_error=True)
            result.append(Item(owner.sequence, RunEventKind.TOOL_RESULT, message))
        pending.clear()

    for item in items:
        if item.message.role == "tool":
            pending.pop(item.message.tool_call_id, None)
        else:
            flush()
        result.append(item)
        for call in item.message.tool_calls:
            pending[call.id] = (item, call.name)
    flush()
    return result


def _build(key: dict[str, str], events: Iterable[RunEvent], items: list[Item]) -> list[Item]:
    for event in events:
        data = event.data
        match event.kind:
            case RunEventKind.USER_MESSAGE:
                text = data["text"]
                if data.get("skills_text"):
                    text = f"{text}\n\n{data['skills_text']}".strip()
                if data.get("attachments"):
                    text = f"{text}\n\n{attachments_text(data['attachments'])}".strip()
                if data.get("earlier_attachments"):
                    text = f"{text}\n\n{attachments_text(data['earlier_attachments'], earlier=True)}".strip()
                items.append(Item(event.sequence, event.kind, Message("user", text)))
            case RunEventKind.CONTEXT | RunEventKind.JOB_FINISHED:
                items.append(Item(event.sequence, event.kind, Message("user", data["text"])))
            case RunEventKind.ASSISTANT_MESSAGE:
                calls = tuple(ToolCall(c["id"], c["name"], c["arguments"]) for c in data.get("tool_calls", []))
                state = data.get("provider_state") if data.get("provider") == key else None
                message = Message("assistant", data.get("text", ""), calls, provider_state=state)
                items.append(Item(event.sequence, event.kind, message))
            case RunEventKind.TOOL_RESULT:
                message = Message(
                    "tool",
                    parts_from_json(data["content"]),
                    tool_call_id=data["call_id"],
                    tool_name=data["name"],
                    is_error=data.get("is_error", False),
                )
                items.append(Item(event.sequence, event.kind, message))
            case RunEventKind.COMPACTION:
                start, end = data["from"], data["to"]
                position = sum(1 for item in items if item.sequence < start)
                items = [item for item in items if not start <= item.sequence <= end]
                items.insert(position, Item(start, event.kind, Message("user", data["text"])))
    return items


def build_messages(run: Run, events: Iterable[RunEvent]) -> list[Message]:
    return [item.message for item in build_items(run, events)]


def pending_tool_calls(events: list[RunEvent]) -> list[dict[str, Any]]:
    """結果のないTool呼出し（新しい順ではなく呼出しの順）"""
    answered = {e.data["call_id"] for e in events if e.kind == RunEventKind.TOOL_RESULT}
    calls = []
    for event in events:
        if event.kind == RunEventKind.ASSISTANT_MESSAGE:
            calls += [c for c in event.data.get("tool_calls", []) if c["id"] not in answered]
    return calls


def attachments_text(attachments: list[dict[str, Any]], *, earlier: bool = False) -> str:
    """利用者が添付したファイルの一覧。中身はSandboxの /workspace/input に読取り専用で置く。
    `earlier` は同じ会話の前の依頼で添付したもの"""
    lines = [f"- {a['path']} ({a.get('content_type', '')}, {_size(a.get('size', 0))})" for a in attachments]
    if earlier:
        head = (
            '<attachments scope="earlier">\nFiles attached earlier in this conversation (read-only). Files you made in '
            "earlier requests are still in /workspace/work and /workspace/output:\n"
        )
    else:
        head = "<attachments>\nThe user attached these files (read-only):\n"
    return head + "\n".join(lines) + "\n</attachments>"


def _size(size: int) -> str:
    if size < 1024:
        return f"{size} bytes"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def runtime_context(run: Run, *, user_name: str) -> str:
    """実行時の文脈（harness.md §6.2）。日時は分の単位にし、変わったときだけ追記する"""
    version = run.model_version
    now = timezone.localtime().strftime("%Y-%m-%d %H:%M %Z")
    lines = [
        RUNTIME_CONTEXT_TAG,
        f"  <now>{now}</now>",
        f'  <user name="{_escape(user_name)}" />',
        f"  <mode>{'conversation' if run.is_interactive else 'task'}</mode>",
        f'  <model profile="{_escape(version.profile.name)}" effort="{_escape(run.effort)}" />',
        (
            f'  <sandbox available="true" workdir="{WORK_DIR}" />'
            if run.runtime_version_id
            else '  <sandbox available="false" />'
        ),
        f"  <confidential>{'true' if run.handles_sensitive_data else 'false'}</confidential>",
    ]
    # エージェントが選べるSkillの一覧（Run開始時に固定したもの。`/` で指定したRunにはない）
    catalog = ((run.manifest or {}).get("fixed") or {}).get("skill_catalog")
    if catalog and catalog.get("skills"):
        lines.append(catalog_text(catalog))
    lines.append("</runtime_context>")
    return "\n".join(lines)


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
