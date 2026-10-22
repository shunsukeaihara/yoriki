"""Workspaceのsnapshotの保存先（design.md §7.8・§7.9）。

保存先は `settings.YORIKI_SNAPSHOT_STORAGE` で選び、Runnerはこの抽象だけを使う。契約はdesign.md §7.8のStorage Backendに
合わせる（put_immutable、open_read、stat、delete）。初期の実装はWorkerのローカルディスクだけで、別のWorkerからは
読めない。複数のWorkerで再開できるようにするときは、成果物の段階で作るStorage Backend（S3等）の実装を足す。
"""

import hashlib
import os
import re
import shutil
import tempfile
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from django.conf import settings
from django.utils.module_loading import import_string

CHUNK = 1024 * 1024
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_.-]+)*$")


class StorageError(Exception):
    pass


@dataclass(frozen=True)
class StoredObject:
    key: str
    size: int
    sha256: str


class SnapshotStorage(ABC):
    @property
    @abstractmethod
    def storage_id(self) -> str:
        """保存先を識別する文字列。Checkpointに記録し、取り出すときに同じ保存先から読めるかを確かめる"""

    @abstractmethod
    def put_immutable(self, key: str, src: IO[bytes]) -> StoredObject:
        """新しいkeyへ保存し、大きさとSHA-256を返す。同じkeyに別の内容があれば上書きせずに StorageError"""

    @abstractmethod
    def open_read(self, key: str) -> IO[bytes]:
        """なければ StorageError"""

    @abstractmethod
    def stat(self, key: str) -> StoredObject | None:
        pass

    @abstractmethod
    def delete(self, key: str) -> None:
        pass


def get_snapshot_storage() -> SnapshotStorage:
    config: dict[str, Any] = settings.YORIKI_SNAPSHOT_STORAGE
    return import_string(config["BACKEND"])(**config.get("OPTIONS", {}))


def validate_key(key: str) -> str:
    """keyはサーバーが作る。利用者の入力をそのままkeyにしない（design.md §7.8）"""
    if not KEY_PATTERN.match(key) or ".." in key.split("/"):
        raise StorageError(f"invalid key: {key!r}")
    return key


class LocalSnapshotStorage(SnapshotStorage):
    """Workerのローカルディスク。保存先のIDはディレクトリに置いたIDのファイルで決め、コンテナを作り直しても変わらない"""

    def __init__(self, root: str):
        self.root = Path(root)

    @property
    def storage_id(self) -> str:
        id_file = self.root / ".storage-id"
        if not id_file.exists():
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self.root / f".storage-id.{uuid.uuid4().hex}"
            tmp.write_text(uuid.uuid4().hex)
            # 同時に作られても、先に作られた方を使う
            try:
                os.link(tmp, id_file)
            except FileExistsError:
                pass
            finally:
                tmp.unlink()
        return f"local:{id_file.read_text().strip()}"

    def _path(self, key: str) -> Path:
        return self.root / validate_key(key)

    def put_immutable(self, key: str, src: IO[bytes]) -> StoredObject:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        digest, size = hashlib.sha256(), 0
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".tmp-", delete=False) as tmp:
            try:
                while chunk := src.read(CHUNK):
                    tmp.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                tmp.flush()
                os.fsync(tmp.fileno())
            except BaseException:
                os.unlink(tmp.name)
                raise
        stored = StoredObject(key, size, digest.hexdigest())
        try:
            # 書き終わったファイルだけを、そのkeyの名前で見せる。既にあれば置き換えない
            os.link(tmp.name, path)
        except FileExistsError:
            existing = self.stat(key)
            if existing is None or existing.sha256 != stored.sha256:
                raise StorageError(f"another object exists at {key}") from None
        finally:
            os.unlink(tmp.name)
        return stored

    def open_read(self, key: str) -> IO[bytes]:
        try:
            return self._path(key).open("rb")
        except FileNotFoundError as exc:
            raise StorageError(f"not found: {key}") from exc

    def stat(self, key: str) -> StoredObject | None:
        path = self._path(key)
        if not path.is_file() or path.is_symlink():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as f:
            while chunk := f.read(CHUNK):
                digest.update(chunk)
        return StoredObject(key, path.stat().st_size, digest.hexdigest())

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
