"""利用量の記録（design.md §9）。予算・クオータの上限は後で加える"""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

MILLION = Decimal(1_000_000)


class UsagePurpose(models.IntegerChoices):
    STEP = 1, _("エージェントのステップ")
    COMPACTION = 2, _("文脈の圧縮")


class UsageLedger(models.Model):
    """1回のLLM呼出しの利用量と金額。作成後に変更しない。金額は実際に使ったモデル設定の版の単価で計算する"""

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    run = models.ForeignKey("run.Run", on_delete=models.PROTECT, related_name="usages")
    model_version = models.ForeignKey("llm.ModelProfileVersion", on_delete=models.PROTECT, related_name="+")
    purpose = models.PositiveSmallIntegerField(_("用途"), choices=UsagePurpose.choices)
    input_tokens = models.PositiveIntegerField(_("入力token"))
    cached_input_tokens = models.PositiveIntegerField(_("cacheから読んだ入力token"), default=0)
    cache_write_tokens = models.PositiveIntegerField(_("cacheへ書いた入力token"), default=0)
    output_tokens = models.PositiveIntegerField(_("出力token"))
    reasoning_tokens = models.PositiveIntegerField(_("推論のtoken"), default=0)  # 出力tokenに含まれる
    cost = models.DecimalField(_("金額（米ドル）"), max_digits=14, decimal_places=8)
    request_id = models.CharField(_("プロバイダの要求ID"), max_length=200, blank=True)

    created = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _("利用量")
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.run_id}:{self.cost}"

    @classmethod
    def record(cls, *, run, model_version, purpose: UsagePurpose, usage, request_id: str = "") -> "UsageLedger":
        uncached = max(usage.input_tokens - usage.cached_input_tokens, 0)
        cost = (
            uncached * model_version.input_price
            + usage.cached_input_tokens * model_version.cached_input_price
            + usage.output_tokens * model_version.output_price
        ) / MILLION
        return cls.objects.create(
            owner_id=run.owner_id,
            run=run,
            model_version=model_version,
            purpose=purpose,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            output_tokens=usage.output_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            cost=cost,
            request_id=request_id,
        )
