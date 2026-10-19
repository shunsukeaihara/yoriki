"""LLMのプロバイダ接続とモデル設定（design.md §4.8）。

API keyは暗号化してDBに保存する（utils/crypto.py）。平文はProvider Adapterの中でだけ復号し、
APIの応答・画面・ログ・Sandboxへは出さない。
"""

from typing import Any

from auditlog.registry import auditlog
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

from common.choices import DataClassification
from utils.crypto import decrypt_secret, encrypt_secret, rotate_secret, secret_hint
from utils.fields import NanoidField

from .errors import VersionConflict


class ProviderKind(models.IntegerChoices):
    OPENAI = 1, _("OpenAI")
    ANTHROPIC = 2, _("Anthropic")
    GEMINI = 3, _("Google Gemini")
    OPENAI_COMPATIBLE = 4, _("OpenAI互換")

    @property
    def requires_api_key(self) -> bool:
        # OpenAI互換のローカルLLM（Ollama等）は、API keyなしで動くものがある
        return self != ProviderKind.OPENAI_COMPATIBLE

    @property
    def uses_base_url(self) -> bool:
        # 公式APIの接続先は固定する。接続先を変えられると、API keyを別のサーバーへ送れてしまう
        return self == ProviderKind.OPENAI_COMPATIBLE


# 接続先を変えようとしたときの文（LLMConnection.check_target_change() と LLMConnectionSerializer で使う）
TARGET_IS_FIXED = _("作成した後は変更できません。接続先ごとに、新しいプロバイダ接続を作ってください")


class LLMConnection(models.Model):
    """プロバイダ接続。種別・接続先・API key・同時実行数等を持つ。基盤管理者だけが登録・変更する"""

    uid = NanoidField(unique=True)
    name = models.CharField(_("名前"), max_length=100)
    kind = models.PositiveSmallIntegerField(_("種別"), choices=ProviderKind.choices)
    base_url = models.URLField(_("base URL"), max_length=500, blank=True)
    api_key_encrypted = models.TextField(_("API key（暗号化）"), blank=True, editable=False)
    api_key_hint = models.CharField(_("API keyの末尾"), max_length=4, blank=True, editable=False)
    timeout_seconds = models.PositiveIntegerField(_("タイムアウト（秒）"), default=120)
    max_concurrency = models.PositiveIntegerField(_("同時実行数の上限"), default=4)
    requests_per_minute = models.PositiveIntegerField(_("1分あたりの要求数の上限"), null=True, blank=True)
    is_active = models.BooleanField(_("有効"), default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("作成者")
    )

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True)
    deleted = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("LLMのプロバイダ接続")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=["name"], condition=models.Q(deleted=False), name="uniq_llm_connection_name"
            ),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.check_target_change()
        super().save(*args, **kwargs)
        self._saved_target = (self.kind, self.base_url)

    @classmethod
    def from_db(cls, db, field_names, values, *args, **kwargs):
        instance = super().from_db(db, field_names, values, *args, **kwargs)
        # 保存されている接続先を覚えておき、save() で変更を確かめる（API・Django管理画面・shellのどこから保存しても）
        if {"kind", "base_url"} <= set(field_names):
            instance._saved_target = (instance.kind, instance.base_url)
        return instance

    def check_target_change(self) -> None:
        """種別とbase URLは、作成した後は変えられない。違反すれば ValidationError（フィールド名ごと）

        接続先ごとに接続を作る（API keyは差し替えられる）。接続先を後から変えられると、モデル設定の版
        （進行中のRunが使う）の意味が変わり、「社内のみ」の版が社外のサーバーへ向いたり、登録済みのAPI keyが
        別のサーバーへ送られたりする
        """
        saved = getattr(self, "_saved_target", None)
        if saved is None:
            return
        kind, base_url = saved
        errors = {}
        if self.kind != kind:
            errors["kind"] = TARGET_IS_FIXED
        if self.base_url != base_url:
            errors["base_url"] = TARGET_IS_FIXED
        if errors:
            raise ValidationError(errors)

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key_encrypted)

    def set_api_key(self, value: str) -> None:
        """API keyを暗号化して持つ。空文字ならAPI keyなしにする（保存は呼び出し元で行う）"""
        self.api_key_encrypted = encrypt_secret(value) if value else ""
        self.api_key_hint = secret_hint(value) if value else ""

    def get_api_key(self) -> str:
        """Provider Adapterの中でだけ呼ぶ。戻り値をログや応答に含めない"""
        return decrypt_secret(self.api_key_encrypted) if self.api_key_encrypted else ""

    def rotate_api_key(self) -> bool:
        """先頭の鍵で暗号化し直す。変更したらTrue（保存は呼び出し元で行う）"""
        if not self.api_key_encrypted:
            return False
        self.api_key_encrypted = rotate_secret(self.api_key_encrypted)
        return True

    def is_in_use(self) -> bool:
        """削除されていないモデル設定の現在の版が、この接続を使っている"""
        return ModelProfile.objects.filter(deleted=False, current_version__connection=self).exists()


class ModelProfile(models.Model):
    """モデル設定。中身（接続、モデル名、上限、単価、能力、データ区分）は不変の版（ModelProfileVersion）で持つ。
    Runは受付時の版を固定して使うので、版を変えても進行中のRunには影響しない
    """

    uid = NanoidField(unique=True)
    name = models.CharField(_("名前"), max_length=100)
    current_version = models.ForeignKey(
        "ModelProfileVersion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("現在の版"),
    )
    is_active = models.BooleanField(_("有効"), default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("作成者")
    )

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True)
    deleted = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("モデル設定")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=["name"], condition=models.Q(deleted=False), name="uniq_model_profile_name"),
        ]

    def __str__(self):
        return self.name

    @classmethod
    @transaction.atomic
    def create_with_version(cls, *, name: str, actor, **version_fields: Any) -> "ModelProfile":
        """モデル設定と最初の版を作る"""
        profile = cls.objects.create(name=name, created_by=actor)
        profile.revise(actor=actor, expected_version=0, **version_fields)
        return profile

    @transaction.atomic
    def revise(self, *, actor, expected_version: int, **version_fields: Any) -> "ModelProfileVersion":
        """新しい版を作り、現在の版にする。編集を始めた後に他の人が版を作っていたら VersionConflict"""
        current = ModelProfile.objects.select_for_update(of=("self",)).select_related("current_version").get(pk=self.pk)
        latest = current.current_version.number if current.current_version else 0
        if latest != expected_version:
            raise VersionConflict(f"expected={expected_version} latest={latest}")
        version = ModelProfileVersion.objects.create(
            profile=current, number=latest + 1, created_by=actor, **version_fields
        )
        current.current_version = version
        current.save(update_fields=["current_version", "updated"])
        self.current_version = version
        return version


class ModelProfileVersion(models.Model):
    """モデル設定の版。作成後に変更・削除しない（切り戻しも新しい版を作る）"""

    uid = NanoidField(unique=True)
    profile = models.ForeignKey(
        ModelProfile, on_delete=models.PROTECT, related_name="versions", verbose_name=_("モデル設定")
    )
    number = models.PositiveIntegerField(_("版"))
    connection = models.ForeignKey(
        LLMConnection, on_delete=models.PROTECT, related_name="model_versions", verbose_name=_("プロバイダ接続")
    )
    model_name = models.CharField(_("モデル名"), max_length=200)
    max_input_tokens = models.PositiveIntegerField(_("最大入力token"))
    max_output_tokens = models.PositiveIntegerField(_("最大出力token"))
    # 単価は100万tokenあたりの米ドル（各社の料金表と同じ単位）。ローカルLLMは0か、設定した原価
    input_price = models.DecimalField(_("入力の単価"), max_digits=12, decimal_places=6, default=0)
    cached_input_price = models.DecimalField(
        _("キャッシュされた入力の単価"), max_digits=12, decimal_places=6, default=0
    )
    output_price = models.DecimalField(_("出力の単価"), max_digits=12, decimal_places=6, default=0)
    supports_tools = models.BooleanField(_("Tool呼出し"), default=False)
    supports_images = models.BooleanField(_("画像入力"), default=False)
    supports_structured_output = models.BooleanField(_("構造化出力"), default=False)
    supports_prompt_caching = models.BooleanField(_("prompt caching"), default=False)
    supports_reasoning = models.BooleanField(_("推論の制御"), default=False)
    data_classification = models.PositiveSmallIntegerField(
        _("データ区分"), choices=DataClassification.choices, default=DataClassification.EXTERNAL_ALLOWED
    )
    change_reason = models.CharField(_("変更理由"), max_length=500, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("作成者")
    )

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("モデル設定の版")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=["profile", "number"], name="uniq_model_profile_version_number"),
        ]

    def __str__(self):
        return f"{self.profile} v{self.number}"


# API keyは暗号化した値でも監査ログに残さない
auditlog.register(LLMConnection, exclude_fields=["api_key_encrypted", "updated"])
auditlog.register(ModelProfile, exclude_fields=["updated"])
auditlog.register(ModelProfileVersion)
