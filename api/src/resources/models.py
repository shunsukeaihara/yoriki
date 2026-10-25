"""Skillの資料（参照資料・マスター・テンプレート・スクリプト）の原本と版（design.md §3.8、§3.10）。

Resourceは資料の論理ID、ResourceVersionは不変の原本の版。中身は成果物と同じBlobObject（Artifact Service）に置き、
Skill専用の保存先は作らない。原本のbytesはそのまま保持し、抽出したテキストやプレビューで置き換えない。
どのSkillのどの版が使うかは、Skillの資料の割当て（skill.SkillAssetBinding等）が持つ。
"""

from typing import IO

from auditlog.registry import auditlog
from django.conf import settings
from django.db import models, transaction
from django.db.models import Max
from django.utils.translation import gettext_lazy as _

from artifact.models import BlobObject, clean_filename, guess_content_type
from utils.fields import NanoidField


class Resource(models.Model):
    """資料の論理ID。置換しても同じResourceのまま、新しいResourceVersionを作る"""

    uid = NanoidField(unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("作成者")
    )

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("資料")
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.uid

    @classmethod
    @transaction.atomic
    def create_from_upload(cls, src: IO[bytes], name: str, *, actor) -> "ResourceVersion":
        """新しい資料と、その最初の版を作る"""
        resource = cls.objects.create(created_by=actor)
        return resource.add_version(src, name, actor=actor)

    @transaction.atomic
    def add_version(self, src: IO[bytes], name: str, *, actor) -> "ResourceVersion":
        """原本を保存し、次の版として記録する。bytesを先に書き、DBの参照はその後に確定する（design.md §3.10）"""
        name = clean_filename(name)
        blob = BlobObject.put(src)
        Resource.objects.select_for_update().filter(pk=self.pk).first()
        latest = self.versions.aggregate(n=Max("number"))["n"] or 0
        version = ResourceVersion.objects.create(
            resource=self,
            number=latest + 1,
            name=name,
            content_type=guess_content_type(name),
            blob=blob,
            created_by=actor,
        )
        self.save(update_fields=["updated"])
        return version


class ResourceVersion(models.Model):
    """資料の原本の版。作成後に変更・削除しない"""

    uid = NanoidField(unique=True)
    resource = models.ForeignKey(Resource, on_delete=models.PROTECT, related_name="versions", verbose_name=_("資料"))
    number = models.PositiveIntegerField(_("版"))
    name = models.CharField(_("ファイル名"), max_length=255)
    content_type = models.CharField(_("形式"), max_length=200)
    blob = models.ForeignKey(BlobObject, on_delete=models.PROTECT, related_name="+", verbose_name=_("中身"))
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("作成者")
    )

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("資料の版")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=["resource", "number"], name="uniq_resource_version_number"),
        ]

    def __str__(self):
        return f"{self.name} v{self.number}"

    @property
    def size(self) -> int:
        return self.blob.size

    @property
    def sha256(self) -> str:
        return self.blob.sha256

    def read(self) -> bytes:
        """原本のbytes（検証と差分に使う。大きさはアップロードの上限まで）"""
        with self.blob.open() as src:
            return src.read()


auditlog.register(Resource, exclude_fields=["updated"])
auditlog.register(ResourceVersion)
