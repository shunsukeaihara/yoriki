import hashlib
import io

import pytest

from runtime.storage import LocalSnapshotStorage, StorageError, get_snapshot_storage


@pytest.fixture(name="storage")
def fixture_storage(tmp_path) -> LocalSnapshotStorage:
    return LocalSnapshotStorage(str(tmp_path / "snapshots"))


def test_put_and_read(storage):
    stored = storage.put_immutable("runs/r1/1.tar", io.BytesIO(b"data"))
    assert (stored.size, stored.sha256) == (4, hashlib.sha256(b"data").hexdigest())
    assert storage.stat("runs/r1/1.tar") == stored
    with storage.open_read("runs/r1/1.tar") as f:
        assert f.read() == b"data"


def test_does_not_overwrite_another_object(storage):
    storage.put_immutable("runs/r1/1.tar", io.BytesIO(b"data"))
    # 同じ内容の再送は受け付ける
    assert storage.put_immutable("runs/r1/1.tar", io.BytesIO(b"data")).size == 4
    with pytest.raises(StorageError):
        storage.put_immutable("runs/r1/1.tar", io.BytesIO(b"other"))
    with storage.open_read("runs/r1/1.tar") as f:
        assert f.read() == b"data"


@pytest.mark.parametrize("key", ["../x", "/abs", "runs/../x", "", "runs//x"])
def test_rejects_unsafe_keys(storage, key):
    with pytest.raises(StorageError):
        storage.put_immutable(key, io.BytesIO(b"x"))


def test_missing_object(storage):
    assert storage.stat("runs/none.tar") is None
    with pytest.raises(StorageError):
        storage.open_read("runs/none.tar")


def test_storage_id_is_stable_for_the_directory(tmp_path):
    first = LocalSnapshotStorage(str(tmp_path / "a")).storage_id
    assert LocalSnapshotStorage(str(tmp_path / "a")).storage_id == first
    assert LocalSnapshotStorage(str(tmp_path / "b")).storage_id != first


def test_backend_is_chosen_by_settings(settings, tmp_path):
    settings.YORIKI_SNAPSHOT_STORAGE = {
        "BACKEND": "runtime.storage.LocalSnapshotStorage",
        "OPTIONS": {"root": str(tmp_path)},
    }
    storage = get_snapshot_storage()
    assert isinstance(storage, LocalSnapshotStorage)
    assert storage.root == tmp_path
