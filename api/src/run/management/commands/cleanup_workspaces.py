"""保存期限を過ぎたワークスペース（snapshot）と、消し残したsnapshotを清掃するコマンド（design.md §8）。

同じ処理をQ2のSchedule（run.tasks.cleanup_workspaces）が1日に1回行う。qclusterを止めている間や、保存期限を
変えた直後に、手で流すために使う。

Usage:
    # 対象の件数だけを表示する
    python manage.py cleanup_workspaces --dry-run

    # 清掃する
    python manage.py cleanup_workspaces
"""

from django.core.management.base import BaseCommand

from run.cleanup import cleanup_workspaces


class Command(BaseCommand):
    help = "保存期限を過ぎたワークスペースと、消し残したsnapshotを清掃する"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="変更せずに対象の件数を表示する")

    def handle(self, *args, **options):
        result = cleanup_workspaces(dry_run=options["dry_run"])
        if options["dry_run"]:
            self.stdout.write(f"保存期限を過ぎた会話: {result.expired:,}件が対象")
            self.stdout.write(f"消し残したsnapshot: {result.leftover:,}件が対象")
            return
        self.stdout.write(self.style.SUCCESS(f"保存期限を過ぎた会話: {result.expired:,}件のワークスペースを消しました"))
        self.stdout.write(self.style.SUCCESS(f"消し残したsnapshot: {result.leftover:,}件を消し直しました"))
        if result.remaining:
            self.stdout.write(
                self.style.WARNING(f"消せずに残ったsnapshot: {result.remaining:,}件（次の清掃でやり直します）")
            )
