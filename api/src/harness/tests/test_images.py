"""`read` の画像（harness.md §7.8、§12）。補助コマンドは偽のSandboxで差し替え、保存とモデルへの入力を確かめる"""

import base64
import io

import pytest

from artifact.models import BlobObject
from conversation.dispatch import MessageMode, post_message
from conversation.factories import ConversationFactory
from harness import compaction
from harness import context as context_module
from harness.context import (
    IMAGE_OMITTED,
    IMAGE_UNAVAILABLE,
    IMAGE_UNSUPPORTED,
    MAX_IMAGES_PER_REQUEST,
    Item,
    build_items,
    fit_to_model,
    parts_from_json,
)
from harness.runner import Runner
from llm.providers.base import ImagePart, Message, TextPart
from run.models import Run, RunEvent, RunEventKind, RunStatus
from run.tasks import claim_run

from .fakes import FakeAdapter, FakeSession, calls, reply

pytestmark = [pytest.mark.usefixtures("default_runtime"), pytest.mark.django_db]

PNG = b"\x89PNG\r\n\x1a\nfake-page-image"
SCANNED = {
    "ok": True,
    "path": "/workspace/input/scan.pdf",
    "size": 2048,
    "sha256": "a" * 64,
    "kind": "pdf",
    "total_pages": 7,
    "pages": [1, 2, 3, 4, 5, 6, 7],
    "pages_without_text": [1, 2, 3, 4, 5, 6],
    "text": "--- page 1 / 7 ---\n(no text layer on this page; it must be read as an image)",
}


class HelperSession(FakeSession):
    """補助コマンドの `read` だけに答えるSandbox。画像は求められたときだけ返す"""

    def __init__(self, run, attempt, *, result: dict, **kwargs):
        super().__init__(run, attempt, **kwargs)
        self.result = result
        self.requests: list[dict] = []

    def helper(self, command: str, request: dict) -> dict:
        assert command == "read"
        self.requests.append(request)
        result = dict(self.result)
        if request["images"]:
            pages = result.get("pages_without_text", [None])[:5]
            data = base64.b64encode(PNG).decode()
            result["images"] = [
                {"mime": "image/png", "width": 800, "height": 1131, "data": data} | ({"page": p} if p else {})
                for p in pages
            ]
        return result


@pytest.fixture(autouse=True)
def fixture_clear_cache():
    context_module._blob_bytes.cache_clear()
    yield
    context_module._blob_bytes.cache_clear()


def start(*, images: bool, owner) -> Run:
    conversation = ConversationFactory.create(owner=owner, model_profile__version__supports_images=images)
    post_message(conversation, body="読んで", client_message_id="", mode=MessageMode.AUTO, actor=owner)
    return Run.objects.get(conversation=conversation)


def execute(run: Run, adapter: FakeAdapter, result: dict = SCANNED) -> list[dict]:
    attempt = claim_run(run.pk)
    assert attempt is not None
    sessions: list[HelperSession] = []

    def factory(*args, **kwargs):
        sessions.append(HelperSession(*args, result=result, **kwargs))
        return sessions[-1]

    Runner(attempt, adapter_factory=adapter, sleep=lambda _s: None, session_factory=factory).run()
    run.refresh_from_db()
    return sessions[0].requests


def tool_result(run: Run) -> dict:
    return RunEvent.objects.get(run=run, kind=RunEventKind.TOOL_RESULT).data


def test_scanned_pages_are_shown_as_images(app_user):
    run = start(images=True, owner=app_user)
    adapter = FakeAdapter([calls(("c1", "read", {"path": "/workspace/input/scan.pdf"})), reply("読みました。")])
    requests = execute(run, adapter)

    assert run.status == RunStatus.COMPLETED
    assert requests[0]["images"] is True
    data = tool_result(run)
    images = [p for p in data["content"] if p["type"] == "image"]
    assert len(images) == 5
    # bytesはRunEventに入れず、保存先のBlobObjectを参照する
    assert all("data" not in p for p in images)
    with BlobObject.objects.get(uid=images[0]["blob"]).open() as src:
        assert src.read() == PNG
    assert data["value"] == {"file": {"path": "/workspace/input/scan.pdf", "sha256": "a" * 64}}
    assert [i["page"] for i in data["meta"]["images"]] == [1, 2, 3, 4, 5]
    text = "".join(p["text"] for p in data["content"] if p["type"] == "text")
    assert "The images of pages [1, 2, 3, 4, 5] follow." in text
    assert "Pages [6] are not shown" in text
    assert "[page 1]" in text

    # 次の要求で、RunEventから読み戻した画像をTool結果としてモデルに送る
    sent = adapter.requests[1].messages[-1]
    assert sent.role == "tool"
    assert [p.data for p in sent.parts if isinstance(p, ImagePart)] == [PNG] * 5


def test_model_without_images_gets_a_notice(app_user):
    run = start(images=False, owner=app_user)
    adapter = FakeAdapter([calls(("c1", "read", {"path": "/workspace/input/scan.pdf"})), reply("読めません。")])
    requests = execute(run, adapter)

    assert requests[0]["images"] is False
    data = tool_result(run)
    assert [p["type"] for p in data["content"]] == ["text"]
    assert "the current model does not accept images" in data["content"][0]["text"]
    assert "images" not in data["meta"]


def test_image_file(app_user):
    result = {"ok": True, "path": "/workspace/input/写真.png", "size": 10, "sha256": "b" * 64, "kind": "image"}
    result |= {"format": "PNG", "width": 3000, "height": 1500, "text": "Image PNG 3000x1500"}
    run = start(images=True, owner=app_user)
    adapter = FakeAdapter([calls(("c1", "read", {"path": "/workspace/input/写真.png"})), reply("赤い画像です。")])
    execute(run, adapter, result)

    content = tool_result(run)["content"]
    assert [p["type"] for p in content] == ["text", "image"]
    assert "The image follows (shown at 800x1131)." in content[0]["text"]


def test_switching_to_a_model_without_images_replaces_them():
    messages = [Message("user", "読んで"), Message("tool", (TextPart("page 1"), ImagePart("image/png", PNG)))]
    fitted = fit_to_model(messages, images=False)
    assert fitted[0] == messages[0]
    assert fitted[1].parts == (TextPart("page 1"), TextPart(IMAGE_UNSUPPORTED))
    assert fit_to_model(messages, images=True) == messages


def test_only_the_newest_images_are_sent():
    older = Message("tool", tuple(ImagePart("image/png", b"old") for _ in range(15)))
    newer = Message("tool", tuple(ImagePart("image/png", b"new") for _ in range(10)))
    fitted = fit_to_model([older, newer], images=True)
    assert fitted[1] == newer
    assert fitted[0].parts[:5] == (TextPart(IMAGE_OMITTED),) * 5
    assert all(isinstance(p, ImagePart) for p in fitted[0].parts[5:])


def test_missing_blob_becomes_a_notice():
    parts = parts_from_json([{"type": "image", "media_type": "image/png", "blob": "missing"}])
    assert fit_to_model([Message("tool", parts)], images=True)[0].parts == (TextPart(IMAGE_UNAVAILABLE),)


def stored_images(count: int) -> Message:
    blobs = [BlobObject.put(io.BytesIO(PNG + bytes([n]))) for n in range(count)]
    data = [{"type": "image", "media_type": "image/png", "blob": b.uid} for b in blobs]
    return Message("tool", parts_from_json(data))


def test_only_the_images_to_send_are_read(monkeypatch):
    opened = []
    original = BlobObject.open

    def counted(self):
        opened.append(self.uid)
        return original(self)

    monkeypatch.setattr(BlobObject, "open", counted)
    message = stored_images(25)
    fitted = fit_to_model([message], images=True)[0]
    assert len(opened) == MAX_IMAGES_PER_REQUEST
    assert [p.data[-1] for p in fitted.parts if isinstance(p, ImagePart)] == list(range(5, 25))
    assert fit_to_model([message], images=False)[0].parts == (TextPart(IMAGE_UNSUPPORTED),) * 25
    assert len(opened) == MAX_IMAGES_PER_REQUEST


def test_estimate_counts_only_the_images_to_send(app_user):
    run = start(images=False, owner=app_user)
    event = RunEvent.record(
        run, RunEventKind.TOOL_RESULT, {"call_id": "c1", "name": "read", "content": [], "is_error": False}
    )
    with_images = start(images=True, owner=app_user)
    message = stored_images(3)
    for target, expected in ((run, 0), (with_images, 3 * compaction.IMAGE_TOKENS)):
        items = build_items(target, [], [Item(1, RunEventKind.TOOL_RESULT, message)])
        assert compaction.estimate_tokens([event], items) - len(items[0].message.text) == expected


def test_repeat_notice_keeps_the_images(app_user, monkeypatch):
    from harness import runner as runner_module  # noqa: PLC0415

    monkeypatch.setattr(runner_module, "REPEAT_WARN", 1)
    run = start(images=True, owner=app_user)
    adapter = FakeAdapter([calls(("c1", "read", {"path": "/workspace/input/scan.pdf"})), reply("読みました。")])
    execute(run, adapter)
    content = tool_result(run)["content"]
    assert sum(1 for p in content if p["type"] == "image") == 5


def test_blob_bytes_are_read_once(app_user, monkeypatch):
    """要求のたびにRunEventから組み立て直すが、同じ画像は保存先から1回だけ読む"""
    run = start(images=True, owner=app_user)
    adapter = FakeAdapter(
        [
            calls(("c1", "read", {"path": "/workspace/input/scan.pdf"})),
            calls(("c2", "todo_write", {"todos": []})),
            reply("読みました。"),
        ]
    )
    opened = []
    original = BlobObject.open

    def counted(self):
        opened.append(self.uid)
        return original(self)

    monkeypatch.setattr(BlobObject, "open", counted)
    execute(run, adapter)
    assert len(opened) == len(set(opened)) == 5
