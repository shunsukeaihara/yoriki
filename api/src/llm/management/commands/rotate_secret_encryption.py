"""DBに保存したSecretを、YORIKI_SECRET_ENCRYPTION_KEYS の先頭の鍵で暗号化し直すコマンド。

鍵を替える手順（deployment.md §5）：
1. 新しい鍵を YORIKI_SECRET_ENCRYPTION_KEYS の先頭に足して再起動する（古い鍵は残す）
2. このコマンドで暗号化し直す
3. 古い鍵を外して再起動する

Usage:
    # 対象の件数だけを表示する
    python manage.py rotate_secret_encryption --dry-run

    # 暗号化し直す
    python manage.py rotate_secret_encryption
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from llm.models import LLMConnection
from utils.crypto import InvalidToken


class Command(BaseCommand):
    help = "DBに保存したSecretを、先頭の鍵で暗号化し直す"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="変更せずに対象の件数を表示する")

    def handle(self, *args, **options):
        # Secretを持つModelを増やしたら、ここに追加する（Connector等）
        count = self._rotate_llm_connections(dry_run=options["dry_run"])
        label = "対象" if options["dry_run"] else "暗号化し直しました"
        self.stdout.write(self.style.SUCCESS(f"LLMのプロバイダ接続: {count:,}件 {label}"))

    @transaction.atomic
    def _rotate_llm_connections(self, *, dry_run: bool) -> int:
        connections = LLMConnection.objects.select_for_update().exclude(api_key_encrypted="")
        if dry_run:
            return connections.count()
        count = 0
        for connection in connections:
            try:
                connection.rotate_api_key()
            except InvalidToken as exc:
                raise CommandError(
                    f"LLMConnection {connection.uid} を復号できません。古い鍵を外していないか確認してください"
                ) from exc
            connection.save(update_fields=["api_key_encrypted"])
            count += 1
        return count
