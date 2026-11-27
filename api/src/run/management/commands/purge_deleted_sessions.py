"""論理削除してから指定した日数を過ぎたagentセッション（会話・修正チャット・タスクの実行）を物理削除するコマンド
（design.md §8）。

消すと戻せないので、Q2のScheduleには登録しない。運用者が日数を決めて明示的に流す。履歴・入力・成果物を消し、
監査ログと利用量の記録は残す。保存先から消せなかったファイルは、次に流したときに消し直す。

Usage:
    # 対象の件数とファイルの数・大きさだけを表示する
    python manage.py purge_deleted_sessions --days 90 --dry-run

    # 物理削除する
    python manage.py purge_deleted_sessions --days 90
"""

from django.core.management.base import BaseCommand, CommandError

from conversation.models import ConversationPurpose
from run.purge import PURGEABLE_PURPOSES, purge_deleted_sessions


class Command(BaseCommand):
    help = "論理削除してから指定した日数を過ぎたagentセッションを物理削除する"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, required=True, help="論理削除してからの日数（1以上）")
        parser.add_argument("--dry-run", action="store_true", help="変更せずに対象の件数を表示する")

    def handle(self, *args, **options):
        days = options["days"]
        if days < 1:
            raise CommandError("--days は1以上にしてください")
        dry_run = options["dry_run"]
        result = purge_deleted_sessions(days=days, dry_run=dry_run)
        verb = "が対象" if dry_run else "を消しました"
        style = self.style.SUCCESS if not dry_run else (lambda text: text)
        for purpose in PURGEABLE_PURPOSES:
            self.stdout.write(style(f"{ConversationPurpose(purpose).label}: {result.sessions[purpose]:,}件{verb}"))
        self.stdout.write(style(f"ファイル: {result.files:,}件（{result.size:,}バイト）{verb}"))
        if result.deferred:
            self.stdout.write(
                self.style.WARNING(
                    f"今回は消さないもの: {result.deferred:,}件（動いている実行・今月の実行・送り終わっていない配信・"
                    "消し残したワークスペースがある。次に流したときにまた調べます）"
                )
            )
        if result.remaining:
            label = "削除を予約したまま残っているファイル" if dry_run else "消せずに残ったファイル"
            self.stdout.write(self.style.WARNING(f"{label}: {result.remaining:,}件（次に流したときに消し直します）"))
