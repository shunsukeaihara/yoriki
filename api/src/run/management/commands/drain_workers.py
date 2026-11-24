"""Workerをdrainするコマンド（deployment.md §6）。すべてのqclusterが新しいAttemptを始めなくなり、動いているRunは次の
区切りで保存してWorkerを手放し、順番待ちに戻る。解除は `resume_workers`（再起動だけでは解除されない）。

動いているバックグラウンドのジョブとサブエージェントは止めずに、終わるのを待つ（ジョブは長くてもAttemptの上限まで）。
待つのをやめても（Ctrl-C・`--timeout`）、drainは続く。

Usage:
    # drainを始める（待たない。drain中に流すと、動いている数だけを表示する）
    python manage.py drain_workers

    # 動いているものがなくなるまで待つ
    python manage.py drain_workers --wait

    # 30分まで待つ。残っていればエラーで終わる
    python manage.py drain_workers --wait --timeout 1800
"""

import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from run.models import ExecutionAttempt, WorkerDrain

POLL_SECONDS = 5


class Command(BaseCommand):
    help = "すべてのWorkerで新しい実行を止め、動いているRunに保存してWorkerを手放させる"

    def add_arguments(self, parser):
        parser.add_argument("--wait", action="store_true", help="動いているものがなくなるまで待つ")
        parser.add_argument("--timeout", type=int, help="待つ秒数の上限（--wait のとき。指定しなければ上限なし）")

    def handle(self, *args, **options):
        if options["timeout"] is not None and not options["wait"]:
            raise CommandError("--timeout は --wait と一緒に指定してください。")
        if WorkerDrain.start():
            self.stdout.write("drainを始めました。")
        else:
            self.stdout.write("すでにdrain中です。")
        deadline = None if options["timeout"] is None else time.monotonic() + options["timeout"]
        shown: tuple[int, int] | None = None
        while True:
            counts = self._live()
            if counts == (0, 0):
                self.stdout.write(self.style.SUCCESS("動いているものはありません。"))
                return
            if counts != shown:
                self.stdout.write(f"動いているもの: Run {counts[0]:,}件、サブエージェント {counts[1]:,}件")
                shown = counts
            if not options["wait"]:
                return
            if not WorkerDrain.is_active():
                raise CommandError("待っている間にdrainが解除されました。")
            if deadline is not None and time.monotonic() >= deadline:
                raise CommandError("時間内に終わりませんでした。drainは続いています（解除は resume_workers）。")
            time.sleep(POLL_SECONDS)

    @staticmethod
    def _live() -> tuple[int, int]:
        """leaseの切れたものは、Workerが落ちたものとして数えない（Reconcilerが閉じる）"""
        live = ExecutionAttempt.objects.filter(ended__isnull=True, lease_expires__gt=timezone.now())
        return (live.filter(subagent__isnull=True).count(), live.filter(subagent__isnull=False).count())
