"""このWorkerのホストに残った、終わったAttemptのSandboxのコンテナと作業領域を清掃するコマンド（design.md §8）。

コンテナと作業領域はホストごとにあり、Q2のScheduleはクラスタのどれか1台でしか動かないので、qclusterを動かす各ホストの
cronから1時間ごとに流す（deployment.md §7）。保存先の実体は `cleanup_objects`（Q2のSchedule）が消す。

Usage:
    # 対象の件数だけを表示する
    python manage.py cleanup_worker --dry-run

    # 清掃する
    python manage.py cleanup_worker
"""

from django.core.management.base import BaseCommand

from harness.cleanup import cleanup_worker


class Command(BaseCommand):
    help = "このホストに残った、終わったAttemptのSandboxのコンテナと作業領域を清掃する"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="変更せずに対象の件数を表示する")

    def handle(self, *args, **options):
        result = cleanup_worker(dry_run=options["dry_run"])
        rows = [("Sandboxのコンテナ", result.sandboxes), ("作業領域", result.workspaces)]
        if options["dry_run"]:
            for label, count in rows:
                self.stdout.write(f"{label}: {count:,}件が対象")
            return
        for label, count in rows:
            self.stdout.write(self.style.SUCCESS(f"{label}: {count:,}件を消しました"))
        if result.failed:
            self.stdout.write(self.style.WARNING(f"消せずに残ったもの: {result.failed:,}件（次の清掃でやり直します）"))
