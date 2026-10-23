"""成果物の回収と、モデルに見せる添付の一覧のテスト。Dockerは使わない"""

import hashlib
import io
import tarfile

import pytest

from artifact.storage import get_storage
from harness.context import build_messages
from harness.sandbox import MAX_OUTPUT_FILES, collect_outputs
from run.factories import RunFactory
from run.models import RunEvent, RunEventKind


def archive(entries: list[tuple[str, bytes | None, str]]) -> io.BytesIO:
    """(名前, 中身, 種類) のtar。種類は file・dir・symlink"""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, data, kind in entries:
            info = tarfile.TarInfo(name)
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif kind == "symlink":
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                tar.addfile(info)
            else:
                info.size = len(data or b"")
                tar.addfile(info, io.BytesIO(data or b""))
    buffer.seek(0)
    return buffer


def test_collects_only_regular_visible_files_in_output(tmp_path):
    tar = archive(
        [
            ("work/a.txt", b"work", "file"),
            ("output/", None, "dir"),
            ("output/結果.csv", b"result", "file"),
            ("output/sub/b.txt", b"b", "file"),
            ("output/.cache/x", b"hidden", "file"),
            ("output/link", None, "symlink"),
            ("output/../escape.txt", b"x", "file"),
        ]
    )
    collected = collect_outputs(tar, get_storage(), {})
    assert [(c.path, c.stored.size) for c in collected] == [("結果.csv", 6), ("sub/b.txt", 1)]
    with get_storage().open_read(collected[0].stored.key) as f:
        assert f.read() == b"result"


def test_unchanged_files_are_not_collected_again():
    tar = archive([("output/a.txt", b"same", "file"), ("output/b.txt", b"new", "file")])
    known = {"a.txt": hashlib.sha256(b"same").hexdigest(), "b.txt": hashlib.sha256(b"old").hexdigest()}
    assert [c.path for c in collect_outputs(tar, get_storage(), known)] == ["b.txt"]


def test_number_of_files_is_limited():
    tar = archive([(f"output/{i}.txt", b"x", "file") for i in range(MAX_OUTPUT_FILES + 5)])
    assert len(collect_outputs(tar, get_storage(), {})) == MAX_OUTPUT_FILES


@pytest.mark.django_db
def test_model_sees_the_attachment_list():
    run = RunFactory.create()
    attachments = [
        {
            "id": "a",
            "name": "請求書.pdf",
            "path": "/workspace/input/請求書.pdf",
            "size": 2048,
            "content_type": "application/pdf",
        }
    ]
    RunEvent.record(run, RunEventKind.USER_MESSAGE, {"text": "取り込んで", "attachments": attachments})
    [message] = build_messages(run, RunEvent.objects.filter(run=run))
    assert message.text == (
        "取り込んで\n\n<attachments>\nThe user attached these files (read-only):\n"
        "- /workspace/input/請求書.pdf (application/pdf, 2.0 KB)\n</attachments>"
    )
    RunEvent.record(run, RunEventKind.USER_MESSAGE, {"text": "集計も", "earlier_attachments": attachments})
    second = build_messages(run, RunEvent.objects.filter(run=run))[1]
    assert second.text.startswith('集計も\n\n<attachments scope="earlier">\nFiles attached earlier')
    assert "- /workspace/input/請求書.pdf" in second.text
