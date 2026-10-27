"""LLMのプロバイダ接続とモデル設定（design.md §4.8）。

API keyは `EncryptedSecretField` で暗号化してDBに保存する。平文はProvider Adapterの中でだけ復号し、
APIの応答・画面・ログ・Sandboxへは出さない。
"""

from typing import Any

from auditlog.registry import auditlog
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

from utils.fields import EncryptedSecretField, NanoidField

from .errors import VersionConflict


class ProviderKind(models.IntegerChoices):
    """接続の種別。違いは、クラウドサービスか、セルフホスト（ローカルLLM）かだけ"""

    OPENAI = 1, _("OpenAI")
    ANTHROPIC = 2, _("Anthropic（Claude）")
    GEMINI = 3, _("Google Gemini")
    # セルフホストのLLM（vLLM、Ollama、LM Studio、llama.cpp server等）。OpenAI互換のAPIで呼ぶ
    LOCAL = 4, _("ローカルLLM")

    @property
    def requires_api_key(self) -> bool:
        # ローカルLLMは、API keyなしで動くものがある
        return self != ProviderKind.LOCAL

    @property
    def uses_base_url(self) -> bool:
        # クラウドサービスの接続先は固定する。base URLを指定するのはローカルLLMだけ
        return self == ProviderKind.LOCAL


class LLMConnection(models.Model):
    """プロバイダ接続。permissionを持つ人（既定では基盤管理者）だけが登録・変更する。

    種別とbase URL（接続先）は作成した後は変えない（更新用のSerializerと管理画面に項目を置かない）。
    接続先ごとに接続を作り、API keyは差し替えられる。接続先を後から変えられると、登録済みのAPI keyが
    別のサーバーへ送られたり、モデル設定の版（進行中のRunが使う）の呼出し先が変わったりするため。
    同時実行数と要求数の上限は、Runnerが守る（Runの段階で実装する）。
    """

    uid = NanoidField(unique=True)
    name = models.CharField(_("名前"), max_length=100)
    kind = models.PositiveSmallIntegerField(_("種別"), choices=ProviderKind.choices)
    base_url = models.URLField(_("base URL"), max_length=500, blank=True)
    api_key = EncryptedSecretField(_("API key"))
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

    def is_in_use(self) -> bool:
        """削除されていないモデル設定の現在の版が、この接続を使っている"""
        return ModelProfile.objects.filter(deleted=False, current_version__connection=self).exists()


# 版に入れる項目（モデル設定の中身）。変えると新しい版になる
VERSION_FIELDS = (
    "connection",
    "model_name",
    "max_input_tokens",
    "max_output_tokens",
    "input_price",
    "cached_input_price",
    "output_price",
    "supports_tools",
    "supports_images",
    "supports_documents",
    "supports_audio",
    "supports_video",
    "supports_structured_output",
    "supports_prompt_caching",
    "supports_reasoning",
    "effort_values",
    "default_effort",
)


class ModelProfile(models.Model):
    """モデル設定。中身（接続、モデル名、上限、単価、能力）は不変の版（ModelProfileVersion）で持つ。

    APIからは1つの設定として見え、中身を変えるとサーバーが新しい版を作る。Runは受付時の版を固定して使い、
    利用量の金額はその版の単価で計算するので、版を変えても進行中のRunと過去の記録には影響しない。
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
    # 会話を始めるときに最初に選ばれているモデル設定。基盤管理者が1つだけ決める（design.md §4.8）
    is_default = models.BooleanField(_("既定"), default=False)
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
            models.UniqueConstraint(
                fields=["is_default"], condition=models.Q(is_default=True, deleted=False), name="uniq_default_model"
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def version_number(self) -> int:
        return self.current_version.number if self.current_version else 0

    @transaction.atomic
    def make_default(self) -> None:
        """既定のモデル設定にする。これまでの既定は外す。

        同時に別のモデル設定を既定にしても一意の制約に当たらない（500にならない）よう、すべての行をpkの順にロックして
        直列にする。既定の行だけをロックすると、既定がないときに2つの要求が両方とも進んでしまう
        """
        list(ModelProfile.objects.select_for_update().order_by("pk").values_list("pk", flat=True))
        ModelProfile.objects.filter(is_default=True).exclude(pk=self.pk).update(is_default=False)
        self.is_default = True
        self.save(update_fields=["is_default", "updated"])

    @classmethod
    @transaction.atomic
    def create_with_version(cls, *, name: str, actor, change_reason: str = "", **fields: Any) -> "ModelProfile":
        """モデル設定と最初の版を作る"""
        profile = cls.objects.create(name=name, created_by=actor)
        profile.revise(actor=actor, expected_version=0, change_reason=change_reason, **fields)
        return profile

    @transaction.atomic
    def revise(self, *, actor, expected_version: int, change_reason: str = "", **fields: Any) -> "ModelProfileVersion":
        """中身を変えた新しい版を作り、現在の版にする。

        編集を始めた後に他の人が版を作っていたら VersionConflict。中身が現在の版と同じなら版を作らず、現在の版を返す
        """
        current = ModelProfile.objects.select_for_update(of=("self",)).select_related("current_version").get(pk=self.pk)
        latest = current.current_version
        latest_number = latest.number if latest else 0
        if latest_number != expected_version:
            raise VersionConflict(f"expected={expected_version} latest={latest_number}")
        if latest and all(getattr(latest, name) == fields[name] for name in VERSION_FIELDS):
            self.current_version = latest
            return latest
        version = ModelProfileVersion.objects.create(
            profile=current, number=latest_number + 1, created_by=actor, change_reason=change_reason, **fields
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
    supports_documents = models.BooleanField(_("文書入力"), default=False)  # PDFをそのまま渡せる
    supports_audio = models.BooleanField(_("音声入力"), default=False)
    supports_video = models.BooleanField(_("動画入力"), default=False)
    supports_structured_output = models.BooleanField(_("構造化出力"), default=False)
    supports_prompt_caching = models.BooleanField(_("prompt caching"), default=False)
    supports_reasoning = models.BooleanField(_("推論の制御"), default=False)
    # 推論の制御に対応するモデルが受け付けるeffortの値。各社の指定そのまま（OpenAIの reasoning_effort の値、
    # Anthropic・Geminiのthinkingのtoken予算の数字等）で、Yoriki独自の段階には読み替えない（design.md §4.8）
    effort_values = ArrayField(models.CharField(max_length=50), verbose_name=_("effortの値"), default=list, blank=True)
    default_effort = models.CharField(_("既定のeffort"), max_length=50, blank=True)
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

    @property
    def is_local(self) -> bool:
        return self.connection.kind == ProviderKind.LOCAL


# API keyは暗号化した値でも監査ログに残さない
auditlog.register(LLMConnection, exclude_fields=["api_key", "updated"])
auditlog.register(ModelProfile, exclude_fields=["updated"])
auditlog.register(ModelProfileVersion)
