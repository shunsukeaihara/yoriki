"""Runに結び付かなかった対話の受信の記録（照合できない・取り込まない）を、受けてから30日で消すコマンド
（design.md §8）。

同じ処理をQ2のSchedule（delivery.tasks.cleanup_receipts）が1日に1回行う。qclusterを止めている間に、手で流すために使う。

Usage:
    # 対象の件数だけを表示する
    python manage.py cleanup_channel_receipts --dry-run

    # 清掃する
    python manage.py cleanup_channel_receipts
"""

from django.core.management.base import BaseCommand

from delivery.cleanup import cleanup_receipts


class Command(BaseCommand):
    help = "Runに結び付かなかった対話の受信の記録を、受けてから30日で消す"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="変更せずに対象の件数を表示する")

    def handle(self, *args, **options):
        count = cleanup_receipts(dry_run=options["dry_run"])
        if options["dry_run"]:
            self.stdout.write(f"対話の受信の記録: {count:,}件が対象")
            return
        self.stdout.write(self.style.SUCCESS(f"対話の受信の記録: {count:,}件を消しました"))
