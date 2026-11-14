"""確定する前に落ちたsnapshotとサブエージェントへのコピーの、記録のない実体を保存先から清掃するコマンド
（design.md §8）。

同じ処理をQ2のSchedule（run.tasks.cleanup_objects）が1時間に1回行う。qclusterを止めている間に、手で流すために使う。

Usage:
    # 対象の件数だけを表示する
    python manage.py cleanup_objects --dry-run

    # 清掃する
    python manage.py cleanup_objects
"""

from django.core.management.base import BaseCommand

from harness.cleanup import cleanup_objects


class Command(BaseCommand):
    help = "確定する前に落ちたsnapshot等の、記録のない実体を保存先から清掃する"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="変更せずに対象の件数を表示する")

    def handle(self, *args, **options):
        result = cleanup_objects(dry_run=options["dry_run"])
        if options["dry_run"]:
            self.stdout.write(f"保存先の実体: {result.objects:,}件が対象")
            return
        self.stdout.write(self.style.SUCCESS(f"保存先の実体: {result.objects:,}件を消しました"))
        if result.failed:
            self.stdout.write(self.style.WARNING(f"消せずに残ったもの: {result.failed:,}件（次の清掃でやり直します）"))
