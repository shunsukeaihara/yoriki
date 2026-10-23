"""成果物とファイル（Artifact Service。design.md §5.1、§7.8）。

Artifactは利用者に見せるファイルの単位（会話の添付、エージェントの成果物）で、中身は不変のBlobObjectを参照する。
BlobObjectの置き場所はBlobLocation（保存先のIDとkey）に記録し、保存先を変えても同じArtifactのIDで扱う。
保存は、先にbytesを書いて大きさとhashを確かめてから、DBの参照を確定する（ファイルとDBを1つのtransactionとはみなさない。
DBに記録する前に失敗したbytesは、参照のないobjectとして清掃で消す）。
"""

import mimetypes
import posixpath
import re
import unicodedata
from datetime import timedelta
from typing import IO, Self

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from utils.fields import NanoidField, generate_uid

from .storage import StoredObject, get_storage

# ファイル名に使わない文字（パスの区切り、制御文字、Windowsで使えない文字）
UNSAFE_NAME_CHARS = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]')
MAX_NAME_LENGTH = 200


def clean_filename(name: str) -> str:
    """利用者・エージェントが付けたファイル名を、表示とSandboxの中の名前に使える形にする。パスは含めない"""
    name = unicodedata.normalize("NFC", name.replace("\\", "/").rsplit("/", 1)[-1])
    name = UNSAFE_NAME_CHARS.sub("_", name).strip().lstrip(".").strip()
    if len(name) > MAX_NAME_LENGTH:
        stem, ext = posixpath.splitext(name)
        name = stem[: MAX_NAME_LENGTH - len(ext)] + ext if len(ext) <= 20 else name[:MAX_NAME_LENGTH]
    return name or "file"


def guess_content_type(name: str) -> str:
    """ファイル名から決める。ブラウザが送るContent-Typeは使わない（`text/html` と名乗らせない）"""
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


class BlobObject(models.Model):
    """不変のbytes。作成後に変更しない"""

    uid = NanoidField(unique=True)
    sha256 = models.CharField(_("SHA-256"), max_length=64, db_index=True)
    size = models.PositiveBigIntegerField(_("大きさ"))

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("ファイルの中身")
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.uid

    @staticmethod
    def new_key() -> str:
        """保存先のkey。サーバーが作り、ファイル名は使わない（design.md §7.8）"""
        now = timezone.now()
        return f"blobs/{now:%Y}/{now:%m}/{generate_uid()}"

    @classmethod
    def register(cls, stored: StoredObject, backend_id: str) -> "BlobObject":
        """保存し終えたbytesを記録する。呼び出し側のtransactionの中で呼ぶ"""
        blob = cls.objects.create(sha256=stored.sha256, size=stored.size)
        BlobLocation.objects.create(blob=blob, backend=backend_id, key=stored.key)
        return blob

    @classmethod
    def put(cls, src: IO[bytes]) -> "BlobObject":
        """既定の保存先に書いてから記録する"""
        storage = get_storage()
        stored = storage.put_immutable(cls.new_key(), src)
        return cls.register(stored, storage.backend_id)

    def location(self) -> "BlobLocation":
        return self.locations.order_by("-pk")[0]

    def open(self) -> IO[bytes]:
        location = self.location()
        return get_storage(location.backend).open_read(location.key)


class BlobLocation(models.Model):
    """BlobObjectを置いた場所。保存先を移すときは、新しい場所を足してから読む先を切り替える（design.md §7.10）"""

    blob = models.ForeignKey(BlobObject, on_delete=models.CASCADE, related_name="locations")
    backend = models.CharField(_("保存先"), max_length=100)
    key = models.CharField(_("保存先のkey"), max_length=300)

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("ファイルの場所")
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=["backend", "key"], name="uniq_blob_location")]

    def __str__(self):
        return f"{self.backend}:{self.key}"


class ArtifactKind(models.IntegerChoices):
    ATTACHMENT = 1, _("添付")
    OUTPUT = 2, _("成果物")


class ArtifactQuerySet(models.QuerySet):
    def owned_by(self, user) -> Self:
        return self.filter(owner=user, deleted=False)

    def sent_inputs(self, conversation_id: int) -> Self:
        """会話で送り、Runが取り込んだ添付（/workspace/input に置くもの）。まだ取り込んでいない依頼（受付済みの
        追加指示・次の依頼）と取り消した依頼のメッセージの添付は、その依頼が始まるまで見せない"""
        from conversation.models import InstructionState  # noqa: PLC0415  conversationがartifactを参照するため

        pending = (InstructionState.RECEIVED, InstructionState.DISCARDED)
        return (
            self.filter(conversation_id=conversation_id, kind=ArtifactKind.ATTACHMENT, deleted=False)
            .exclude(path="")
            .exclude(messages__instruction__state__in=pending)
        )


class Artifact(models.Model):
    """利用者に見せるファイル。中身（blob）は変えず、変わったファイルは新しいArtifactにする"""

    uid = NanoidField(unique=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="artifacts", verbose_name=_("所有者")
    )
    conversation = models.ForeignKey(
        "conversation.Conversation",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="artifacts",
        verbose_name=_("会話"),
    )
    # 成果物を作ったRun。添付では空
    run = models.ForeignKey(
        "run.Run", on_delete=models.CASCADE, null=True, blank=True, related_name="artifacts", verbose_name=_("実行")
    )
    blob = models.ForeignKey(BlobObject, on_delete=models.PROTECT, related_name="+", verbose_name=_("中身"))
    kind = models.PositiveSmallIntegerField(_("種別"), choices=ArtifactKind.choices)
    name = models.CharField(_("ファイル名"), max_length=255)
    # Sandboxの中の場所。添付は /workspace/input の中の名前（メッセージで送ったときに決める）、
    # 成果物は /workspace/output からの相対パス
    path = models.CharField(_("パス"), max_length=1000, blank=True)
    content_type = models.CharField(_("形式"), max_length=200)
    expires = models.DateTimeField(_("保持期限"), null=True, blank=True)

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    deleted = models.BooleanField(default=False)

    objects = ArtifactQuerySet.as_manager()

    class Meta:
        verbose_name = _("成果物・添付")
        verbose_name_plural = verbose_name
        indexes = [models.Index(fields=["conversation", "kind", "-created"])]

    def __str__(self):
        return self.name

    @property
    def size(self) -> int:
        return self.blob.size

    @property
    def sha256(self) -> str:
        return self.blob.sha256

    @staticmethod
    def default_expires():
        return timezone.now() + timedelta(days=settings.YORIKI_ARTIFACT_RETENTION_DAYS)

    @classmethod
    def upload(cls, src: IO[bytes], name: str, *, owner, conversation) -> "Artifact":
        """会話に添付するファイルを保存する。Sandboxの中の名前は、メッセージで送ったときに決める（attach()）"""
        name = clean_filename(name)
        blob = BlobObject.put(src)
        return cls.objects.create(
            owner=owner,
            conversation=conversation,
            blob=blob,
            kind=ArtifactKind.ATTACHMENT,
            name=name,
            content_type=guess_content_type(name),
            expires=cls.default_expires(),
        )

    @classmethod
    def attach(cls, conversation, artifacts: list["Artifact"]) -> None:
        """メッセージで送る添付に、/workspace/input の中の名前を決める。会話の行ロックの中で呼ぶ。
        同じ名前の添付が既にあれば「名前 (2).拡張子」のようにする（前の添付を置き換えない）"""
        used = set(
            cls.objects.filter(conversation=conversation, kind=ArtifactKind.ATTACHMENT)
            .exclude(path="")
            .values_list("path", flat=True)
        )
        for artifact in artifacts:
            stem, ext = posixpath.splitext(artifact.name)
            path, number = artifact.name, 1
            while path in used:
                number += 1
                path = f"{stem} ({number}){ext}"
            used.add(path)
            artifact.path = path
            artifact.save(update_fields=["path"])

    @classmethod
    def create_output(cls, run, path: str, blob: BlobObject) -> "Artifact":
        """Sandboxの /workspace/output から回収したファイルを、Runの成果物として記録する"""
        name = clean_filename(posixpath.basename(path))
        return cls.objects.create(
            owner=run.owner,
            conversation_id=run.conversation_id,
            run=run,
            blob=blob,
            kind=ArtifactKind.OUTPUT,
            name=name,
            path=path,
            content_type=guess_content_type(name),
            expires=cls.default_expires(),
        )

    def describe(self) -> dict[str, object]:
        """RunEventに残す添付の一覧の1件（モデルへの説明と画面の表示に使う）"""
        return {
            "id": self.uid,
            "name": self.name,
            "path": f"/workspace/input/{self.path}",
            "size": self.size,
            "content_type": self.content_type,
        }
