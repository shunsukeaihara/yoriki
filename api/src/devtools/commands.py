from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class DevCommand(BaseCommand):
    """DEBUG のときだけ動く管理コマンド。本番の設定で確認用のデータやサーバーを作らないため"""

    def execute(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("開発用のコマンドです。YORIKI_DEBUG=true のときだけ動きます")
        return super().execute(*args, **options)
