"""Plugin Registry（design.md §3.11.1）。

行は配備されているコード（本体のPluginと、entry pointsで宣言した自社のPlugin）から作り（plugin/registry.py の
`sync_plugins()`）、DBが持つのは有効か・配備されているか等の状態だけ。DBの値からimportしない。配備から消えた
Pluginの行は消さない（入口・出口・Connectorの接続がextension_idで指しているため）。
"""

from auditlog.registry import auditlog
from django.db import models
from django.utils.translation import gettext_lazy as _

from .spec import ExtensionType


class Plugin(models.Model):
    plugin_id = models.CharField(_("ID"), max_length=200, unique=True)
    name = models.CharField(_("名前"), max_length=200)
    description = models.TextField(_("説明"), blank=True)
    # 配備されているコードの版（本体のPluginはYorikiの版）
    version = models.CharField(_("版"), max_length=100)
    builtin = models.BooleanField(_("本体のPlugin"), default=False)
    # 基盤管理者が切り替える。初めて見つかったときは、本体のものは有効、それ以外は停止
    enabled = models.BooleanField(_("有効"), default=False)
    # 今の配備にあるか（なければ停止と同じに扱う）
    deployed = models.BooleanField(_("配備されている"), default=True)
    # 登録の確かめ（形・宣言）に通らなかった理由。空でなければ停止と同じに扱う
    problem = models.TextField(_("登録できない理由"), blank=True)
    # 廃止の予告の理由と、代わりのplugin_id
    deprecated = models.TextField(_("廃止の予告"), blank=True)
    replaced_by = models.CharField(_("代わりのPlugin"), max_length=200, blank=True)
    has_self_check = models.BooleanField(_("自己診断がある"), default=False)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Plugin")
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.plugin_id

    @property
    def usable(self) -> bool:
        return self.enabled and self.deployed and not self.problem


class PluginExtension(models.Model):
    """Pluginに属するAdapter。入口・出口・Connectorの接続は extension_id で指す"""

    extension_id = models.CharField(_("ID"), max_length=200, unique=True)
    plugin = models.ForeignKey(Plugin, on_delete=models.CASCADE, related_name="extensions")
    extension_type = models.CharField(_("拡張種別"), max_length=40, choices=[(t.value, t.value) for t in ExtensionType])
    name = models.CharField(_("名前"), max_length=200)
    capabilities = models.JSONField(_("対応能力"), default=list)
    config_schema = models.JSONField(_("設定のSchema"), default=dict)
    # 今のPluginの宣言にあるか（Pluginの版を上げて外したAdapterは残して、使えなくする）
    deployed = models.BooleanField(_("配備されている"), default=True)

    class Meta:
        verbose_name = _("PluginのAdapter")
        verbose_name_plural = verbose_name
        ordering = ["extension_id"]

    def __str__(self):
        return self.extension_id


class PluginFailure(models.Model):
    """実行時の観察（design.md §3.11.4）。Yorikiが呼ぶ境界で捕まえた予期しない失敗と時間切れを、AdapterとPluginの版、
    日ごとに数える"""

    extension = models.ForeignKey(PluginExtension, on_delete=models.CASCADE, related_name="failures")
    plugin_version = models.CharField(_("Pluginの版"), max_length=100)
    day = models.DateField(_("日"))
    unexpected = models.PositiveIntegerField(_("予期しない失敗"), default=0)
    timeouts = models.PositiveIntegerField(_("時間切れ"), default=0)
    last_error = models.CharField(_("最後のエラー"), max_length=500, blank=True)
    last_at = models.DateTimeField(_("最後の日時"), null=True, blank=True)

    class Meta:
        verbose_name = _("Pluginの失敗")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=["extension", "plugin_version", "day"], name="uniq_plugin_failure_day")
        ]

    def __str__(self):
        return f"{self.extension_id} {self.plugin_version} {self.day}"


auditlog.register(Plugin, include_fields=["enabled"])
