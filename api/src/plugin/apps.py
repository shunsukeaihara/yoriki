from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _sync(sender, **kwargs):
    from .registry import sync_plugins  # noqa: PLC0415  アプリを読み込んだ後に読む

    sync_plugins()


class PluginConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "plugin"
    verbose_name = "Plugin"

    def ready(self):
        # 配備のたびに流すmigrateの後に、読み込んだPluginをDBへ登録する（design.md §3.11.1）
        post_migrate.connect(_sync, sender=self)
