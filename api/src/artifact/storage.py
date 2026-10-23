"""ファイルの保存先（Storage Backend。design.md §7.8）。

bytesだけを扱い、所有者・権限・参照・保持期限は Artifact Service（artifact/models.py）が管理する。保存先は
`settings.YORIKI_STORAGE` に保存先のIDごとに書き、新しく保存するときは既定（`DEFAULT`）を使う。保存したものは保存先の
IDとkeyで記録し（BlobLocation、Checkpoint）、既定を変えても、記録した保存先から読む（design.md §7.10）。
Workspaceのsnapshotも同じ保存先に置く。S3のように別のWorkerからも読める保存先なら、別のWorkerで再開できる。
"""

import base64
import hashlib
import os
import re
import shutil
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.utils.module_loading import import_string

CHUNK = 1024 * 1024
# Storage Backendを入れる前のsnapshotの保存先のID（`local:<UUID>`）
LEGACY_SNAPSHOT_PREFIX = "local:"
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_.-]+)*$")


class StorageError(Exception):
    pass


@dataclass(frozen=True)
class StoredObject:
    key: str
    size: int
    sha256: str


class StorageBackend(ABC):
    """design.md §7.8の契約（put_immutable、open_read、stat、delete）"""

    def __init__(self, backend_id: str):
        # 保存先のID。保存したものの記録に残すので、物理的な保存先を変えるときは別のIDにする（design.md §7.10）
        self.backend_id = backend_id

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

    def accel_redirect(self, key: str) -> str | None:
        """nginxの内部locationから返せるなら、そのパス（`X-Accel-Redirect`。deployment.md §4.1）"""
        return None


def get_storage(backend_id: str | None = None) -> StorageBackend:
    """保存先のIDの保存先。省けば新しく保存するときの既定"""
    config: dict[str, Any] = settings.YORIKI_STORAGE
    backend_id = backend_id or config["DEFAULT"]
    if backend_id.startswith(LEGACY_SNAPSHOT_PREFIX):
        # Storage Backendを入れる前のCheckpoint。Workerの作業領域の snapshots/ に置いていた（読むだけ）
        return LocalStorage(backend_id, root=str(Path(settings.YORIKI_WORKSPACES_DIR) / "snapshots"))
    backend = config["BACKENDS"].get(backend_id)
    if backend is None:
        raise StorageError(f"unknown storage backend: {backend_id}")
    return import_string(backend["BACKEND"])(backend_id, **backend.get("OPTIONS", {}))


def validate_key(key: str) -> str:
    """keyはサーバーが作る。利用者の入力をそのままkeyにしない（design.md §7.8）"""
    if not KEY_PATTERN.match(key) or ".." in key.split("/"):
        raise StorageError(f"invalid key: {key!r}")
    return key


def hash_stream(src: IO[bytes], dest: IO[bytes] | None = None) -> tuple[str, int]:
    """SHA-256と大きさ。`dest` があれば、読んだものを書き写す"""
    digest, size = hashlib.sha256(), 0
    while chunk := src.read(CHUNK):
        if dest is not None:
            dest.write(chunk)
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


class LocalStorage(StorageBackend):
    """サーバーのディスク（永続volume）。別のサーバーのWorkerからは読めない（deployment.md §6）"""

    def __init__(self, backend_id: str, *, root: str, accel_redirect: str = ""):
        super().__init__(backend_id)
        self.root = Path(root)
        # nginxの内部location（`/_protected/` 等）。空なら、apiがファイルを返す
        self.accel_prefix = accel_redirect

    def _path(self, key: str) -> Path:
        return self.root / validate_key(key)

    def put_immutable(self, key: str, src: IO[bytes]) -> StoredObject:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".tmp-", delete=False) as tmp:
            try:
                sha256, size = hash_stream(src, tmp)
                tmp.flush()
                os.fsync(tmp.fileno())
                # nginxが `X-Accel-Redirect` で返せるように、読めるようにする（書けるのは保存した利用者だけ）
                os.fchmod(tmp.fileno(), 0o644)
            except BaseException:
                os.unlink(tmp.name)
                raise
        stored = StoredObject(key, size, sha256)
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
        path = self._path(key)
        if path.is_symlink():
            raise StorageError(f"not a regular file: {key}")
        try:
            return path.open("rb")
        except FileNotFoundError as exc:
            raise StorageError(f"not found: {key}") from exc

    def stat(self, key: str) -> StoredObject | None:
        path = self._path(key)
        if not path.is_file() or path.is_symlink():
            return None
        with path.open("rb") as f:
            sha256, size = hash_stream(f)
        return StoredObject(key, size, sha256)

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)

    def accel_redirect(self, key: str) -> str | None:
        return f"{self.accel_prefix.rstrip('/')}/{validate_key(key)}" if self.accel_prefix else None


class S3Storage(StorageBackend):
    """S3（非公開のbucket）。資格情報はIAM role等からboto3の標準の方法で読み、設定やSandboxには置かない。
    上書きしないことは条件付きの書込み（`If-None-Match: *`）で守る。S3互換の製品は、この動きを確かめてから使う"""

    def __init__(self, backend_id: str, *, bucket: str, prefix: str = "", region: str = "", endpoint_url: str = ""):
        super().__init__(backend_id)
        if not bucket:
            raise StorageError("the S3 bucket is not configured")
        self.bucket = bucket
        self.prefix = f"{prefix.strip('/')}/" if prefix.strip("/") else ""
        self.client = boto3.client("s3", region_name=region or None, endpoint_url=endpoint_url or None)

    def _key(self, key: str) -> str:
        return self.prefix + validate_key(key)

    def put_immutable(self, key: str, src: IO[bytes]) -> StoredObject:
        # 送る前にhashを計算する（S3でも同じSHA-256で確かめさせ、metadataにも残す）
        with tempfile.TemporaryFile() as spool:
            if src.seekable():
                start = src.tell()
                sha256, size = hash_stream(src)
                src.seek(start)
                body = src
            else:
                sha256, size = hash_stream(src, spool)
                spool.seek(0)
                body = spool
            stored = StoredObject(key, size, sha256)
            try:
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=self._key(key),
                    Body=body,
                    ContentLength=size,
                    IfNoneMatch="*",
                    ChecksumSHA256=base64.b64encode(bytes.fromhex(sha256)).decode(),
                    Metadata={"sha256": sha256},
                )
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") not in (
                    "PreconditionFailed",
                    "ConditionalRequestConflict",
                ):
                    raise StorageError(f"cannot store {key}: {exc}") from exc
                existing = self.stat(key)
                if existing is None or existing.sha256 != stored.sha256:
                    raise StorageError(f"another object exists at {key}") from None
            except BotoCoreError as exc:
                raise StorageError(f"cannot store {key}: {exc}") from exc
        return stored

    def open_read(self, key: str) -> IO[bytes]:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self._key(key))["Body"]  # type: ignore[return-value]
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"cannot read {key}: {exc}") from exc

    def stat(self, key: str) -> StoredObject | None:
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=self._key(key))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise StorageError(f"cannot stat {key}: {exc}") from exc
        except BotoCoreError as exc:
            raise StorageError(f"cannot stat {key}: {exc}") from exc
        sha256 = (head.get("Metadata") or {}).get("sha256", "")
        if not sha256:
            return None  # Yorikiが保存したものではない
        return StoredObject(key, head["ContentLength"], sha256)

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=self._key(key))
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"cannot delete {key}: {exc}") from exc
