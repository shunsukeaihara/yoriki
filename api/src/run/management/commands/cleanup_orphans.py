"""終わったAttemptのSandboxのコンテナ・作業領域と、確定する前に落ちたsnapshot等の実体を清掃するコマンド
（design.md §8）。

同じ処理をQ2のSchedule（run.tasks.cleanup_orphans）が1時間に1回行う。コンテナと作業領域は、コマンドを流したサーバーの
ものだけを見る。qclusterを止めている間や、Workerサーバーが複数あるときに、各サーバーで流すために使う。

Usage:
    # 対象の件数だけを表示する
    python manage.py cleanup_orphans --dry-run

    # 清掃する
    python manage.py cleanup_orphans
"""

from django.core.management.base import BaseCommand

from harness.cleanup import cleanup_orphans


class Command(BaseCommand):
    help = "終わったAttemptのコンテナ・作業領域と、記録のない保存先の実体を清掃する"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="変更せずに対象の件数を表示する")

    def handle(self, *args, **options):
        result = cleanup_orphans(dry_run=options["dry_run"])
        rows = [
            ("Sandboxのコンテナ", result.sandboxes),
            ("作業領域", result.workspaces),
            ("保存先の実体", result.objects),
        ]
        if options["dry_run"]:
            for label, count in rows:
                self.stdout.write(f"{label}: {count:,}件が対象")
            return
        for label, count in rows:
            self.stdout.write(self.style.SUCCESS(f"{label}: {count:,}件を消しました"))
        if result.failed:
            self.stdout.write(self.style.WARNING(f"消せずに残ったもの: {result.failed:,}件（次の清掃でやり直します）"))
