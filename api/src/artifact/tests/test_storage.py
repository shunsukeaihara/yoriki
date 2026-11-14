import hashlib
import io
from unittest import mock

import boto3
import pytest
from moto import mock_aws

from artifact.storage import LocalStorage, S3Storage, StorageBackend, StorageError, StoredObject, get_storage

BUCKET = "yoriki-test"


@pytest.fixture(name="s3")
def fixture_s3(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        yield


@pytest.fixture(name="storage", params=["local", "s3"])
def fixture_storage(request, tmp_path) -> StorageBackend:
    """Local・S3の両方で、同じ契約（design.md §7.8）を確かめる"""
    if request.param == "local":
        return LocalStorage("local-main", root=str(tmp_path / "objects"))
    request.getfixturevalue("s3")
    return S3Storage("s3-main", bucket=BUCKET, prefix="production", region="us-east-1")


def test_put_and_read(storage):
    stored = storage.put_immutable("runs/r1/1.tar", io.BytesIO(b"data"))
    assert (stored.size, stored.sha256) == (4, hashlib.sha256(b"data").hexdigest())
    assert storage.stat("runs/r1/1.tar") == stored
    with storage.open_read("runs/r1/1.tar") as f:
        assert f.read() == b"data"


def test_exists_without_reading(storage):
    assert not storage.exists("a/missing")
    storage.put_immutable("a/present", io.BytesIO(b"x"))
    with mock.patch("artifact.storage.hash_stream", side_effect=AssertionError("read")):
        assert storage.exists("a/present")


def test_does_not_overwrite_another_object(storage):
    storage.put_immutable("runs/r1/1.tar", io.BytesIO(b"data"))
    # 同じ内容の再送は受け付ける
    assert storage.put_immutable("runs/r1/1.tar", io.BytesIO(b"data")).size == 4
    with pytest.raises(StorageError):
        storage.put_immutable("runs/r1/1.tar", io.BytesIO(b"other"))
    with storage.open_read("runs/r1/1.tar") as f:
        assert f.read() == b"data"


def test_reads_a_stream_that_cannot_seek(storage):
    class Stream(io.RawIOBase):
        def __init__(self):
            self.data = io.BytesIO(b"x" * 3_000_000)

        def readable(self):
            return True

        def readinto(self, b):
            chunk = self.data.read(len(b))
            b[: len(chunk)] = chunk
            return len(chunk)

    stored = storage.put_immutable("blobs/big", Stream())
    assert stored.size == 3_000_000
    assert storage.stat("blobs/big") == stored


@pytest.mark.parametrize("key", ["../x", "/abs", "runs/../x", "", "runs//x"])
def test_rejects_unsafe_keys(storage, key):
    with pytest.raises(StorageError):
        storage.put_immutable(key, io.BytesIO(b"x"))


def test_missing_object(storage):
    assert storage.stat("runs/none.tar") is None
    with pytest.raises(StorageError):
        storage.open_read("runs/none.tar")


def test_delete(storage):
    storage.put_immutable("blobs/a", io.BytesIO(b"a"))
    storage.delete("blobs/a")
    assert storage.stat("blobs/a") is None


def test_s3_keys_are_under_the_prefix(s3):
    storage = S3Storage("s3-main", bucket=BUCKET, prefix="/production/", region="us-east-1")
    storage.put_immutable("blobs/a", io.BytesIO(b"a"))
    keys = [o["Key"] for o in boto3.client("s3", region_name="us-east-1").list_objects_v2(Bucket=BUCKET)["Contents"]]
    assert keys == ["production/blobs/a"]


def test_s3_object_without_the_hash_is_not_ours(s3):
    boto3.client("s3", region_name="us-east-1").put_object(Bucket=BUCKET, Key="blobs/a", Body=b"a")
    assert S3Storage("s3-main", bucket=BUCKET, region="us-east-1").stat("blobs/a") is None


def test_local_files_are_readable_by_nginx(tmp_path):
    storage = LocalStorage("local-main", root=str(tmp_path), accel_redirect="/_protected/")
    storage.put_immutable("blobs/2026/10/a", io.BytesIO(b"a"))
    assert (tmp_path / "blobs/2026/10/a").stat().st_mode & 0o777 == 0o644
    assert storage.accel_redirect("blobs/2026/10/a") == "/_protected/blobs/2026/10/a"
    assert LocalStorage("local-main", root=str(tmp_path)).accel_redirect("blobs/2026/10/a") is None


def test_backend_is_chosen_by_settings(settings, tmp_path):
    settings.YORIKI_STORAGE = {
        "DEFAULT": "new",
        "BACKENDS": {
            "old": {"BACKEND": "artifact.storage.LocalStorage", "OPTIONS": {"root": str(tmp_path / "old")}},
            "new": {"BACKEND": "artifact.storage.LocalStorage", "OPTIONS": {"root": str(tmp_path / "new")}},
        },
    }
    storage = get_storage()
    assert isinstance(storage, LocalStorage)
    assert (storage.backend_id, storage.root) == ("new", tmp_path / "new")
    # 記録した保存先からは、既定を変えた後も読める
    assert get_storage("old").root == tmp_path / "old"  # type: ignore[attr-defined]
    with pytest.raises(StorageError):
        get_storage("unknown")


def test_snapshots_saved_before_the_storage_backend_are_readable(settings, tmp_path):
    """Storage Backendを入れる前のCheckpoint（保存先のIDが `local:<UUID>`）は、作業領域の snapshots/ から読む"""
    settings.YORIKI_WORKSPACES_DIR = str(tmp_path)
    (tmp_path / "snapshots/runs/r1").mkdir(parents=True)
    (tmp_path / "snapshots/runs/r1/1-abc.tar").write_bytes(b"tar")
    storage = get_storage("local:0123abcd")
    assert storage.stat("runs/r1/1-abc.tar") == StoredObject("runs/r1/1-abc.tar", 3, hashlib.sha256(b"tar").hexdigest())


def test_list_keys_under_a_prefix(storage):
    for key in ("snapshots/runs/r1/1.tar", "snapshots/runs/r2/a/2.tar", "subagents/r1/c.tar", "blobs/x"):
        storage.put_immutable(key, io.BytesIO(b"x"))
    assert sorted(storage.list_keys("snapshots/runs/")) == ["snapshots/runs/r1/1.tar", "snapshots/runs/r2/a/2.tar"]
    assert list(storage.list_keys("missing/")) == []
    with pytest.raises(StorageError):
        list(storage.list_keys("../"))


def test_local_list_keys_skips_temporary_files_and_symlinks(tmp_path):
    storage = LocalStorage("local-main", root=str(tmp_path / "objects"))
    storage.put_immutable("snapshots/runs/r1/1.tar", io.BytesIO(b"x"))
    folder = tmp_path / "objects" / "snapshots" / "runs" / "r1"
    (folder / ".tmp-abc").write_bytes(b"x")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.tar").write_bytes(b"x")
    (folder / "link.tar").symlink_to(tmp_path / "outside" / "secret.tar")
    (tmp_path / "objects" / "snapshots" / "runs" / "r2").symlink_to(tmp_path / "outside")
    assert list(storage.list_keys("snapshots/runs/")) == ["snapshots/runs/r1/1.tar"]


def test_s3_list_keys_pages_and_strips_the_prefix(s3):
    storage = S3Storage("s3-main", bucket=BUCKET, prefix="production", region="us-east-1")
    for i in range(3):
        storage.put_immutable(f"snapshots/runs/r1/{i}.tar", io.BytesIO(b"x"))
    # 別のprefixのものは返さない
    S3Storage("s3-other", bucket=BUCKET, prefix="staging", region="us-east-1").put_immutable(
        "snapshots/runs/r9/0.tar", io.BytesIO(b"x")
    )
    with mock.patch.object(storage.client, "get_paginator", wraps=storage.client.get_paginator) as paginator:
        assert sorted(storage.list_keys("snapshots/runs/")) == [f"snapshots/runs/r1/{i}.tar" for i in range(3)]
    paginator.assert_called_once_with("list_objects_v2")
