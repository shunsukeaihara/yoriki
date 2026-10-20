"""DBに保存したSecretを、YORIKI_SECRET_ENCRYPTION_KEYS の先頭の鍵で暗号化し直すコマンド。

鍵を替える手順（deployment.md §5）：
1. 新しい鍵を YORIKI_SECRET_ENCRYPTION_KEYS の先頭に足して再起動する（古い鍵は残す）
2. このコマンドで暗号化し直す
3. 古い鍵を外して再起動する

対象は、EncryptedSecretField を持つすべてのModel（utils.fields.ENCRYPTED_SECRET_FIELDS）。

Usage:
    # 対象の件数だけを表示する
    python manage.py rotate_secret_encryption --dry-run

    # 暗号化し直す
    python manage.py rotate_secret_encryption
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from utils.crypto import InvalidToken, rotate_secret
from utils.fields import ENCRYPTED_SECRET_FIELDS, SecretValue


class Command(BaseCommand):
    help = "DBに保存したSecretを、先頭の鍵で暗号化し直す"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="変更せずに対象の件数を表示する")

    def handle(self, *args, **options):
        for model, field_name in ENCRYPTED_SECRET_FIELDS:
            count = self._rotate(model, field_name, dry_run=options["dry_run"])
            label = "件が対象" if options["dry_run"] else "件を暗号化し直しました"
            self.stdout.write(self.style.SUCCESS(f"{model._meta.label}.{field_name}: {count:,}{label}"))

    @transaction.atomic
    def _rotate(self, model, field_name: str, *, dry_run: bool) -> int:
        rows = model.objects.select_for_update().exclude(**{field_name: ""})
        if dry_run:
            return rows.count()
        count = 0
        for row in rows:
            value: SecretValue = getattr(row, field_name)
            try:
                setattr(row, field_name, SecretValue(rotate_secret(value.ciphertext)))
            except InvalidToken as exc:
                label = f"{model._meta.label} pk={row.pk} の {field_name}"
                raise CommandError(f"{label} を復号できません。古い鍵を外していないか確認してください") from exc
            row.save(update_fields=[field_name])
            count += 1
        return count
