"""Workerのdrainを解除するコマンド（deployment.md §6）。順番待ちのRunは、1分ごとのReconcilerが送り直して動き出す。

Usage:
    python manage.py resume_workers
"""

from django.core.management.base import BaseCommand

from run.models import WorkerDrain


class Command(BaseCommand):
    help = "Workerのdrainを解除する"

    def handle(self, *args, **options):
        if WorkerDrain.stop():
            self.stdout.write(self.style.SUCCESS("drainを解除しました。順番待ちのRunは1〜2分のうちに動き出します。"))
        else:
            self.stdout.write("drain中ではありません。")
