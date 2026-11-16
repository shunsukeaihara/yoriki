from django.core.management.base import BaseCommand

from plugin.models import Plugin
from plugin.registry import sync_plugins


class Command(BaseCommand):
    help = "配備されているPluginを確かめてDBへ登録する（migrateの後にも自動で流れる。design.md §3.11.1）"

    def handle(self, *args, **options):
        sync_plugins()
        for plugin in Plugin.objects.order_by("plugin_id"):
            state = "enabled" if plugin.usable else "disabled"
            if not plugin.deployed:
                state = "not deployed"
            elif plugin.problem:
                state = "invalid"
            self.stdout.write(f"{plugin.plugin_id} {plugin.version} {state}")
            if plugin.problem:
                self.stdout.write(f"  {plugin.problem}")
