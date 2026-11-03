"""確認用のデータを入れるコマンド。何度流しても同じ状態になる（名前で探して作るか揃える）。

入れるもの（docs/development.md §6.1）：確認用の部門とユーザー3人、偽のLLMの接続とモデル設定、
つながらない接続とモデル設定、確認用のスキーマ・読むだけのロールとConnector、Runtime Profile（指定したとき）

Usage:
    # 何を作るか・揃えるかだけを表示する（最後に取り消す）
    python manage.py seed_dev --dry-run

    # 入れる
    python manage.py seed_dev

    # 手元のレジストリにpushしたSandboxのイメージで、Runtime Profileも入れる
    python manage.py seed_dev --sandbox-image localhost:5001/yoriki-sandbox-docs@sha256:<digest>
"""

from django.core.management.base import CommandError
from django.db import DatabaseError, transaction

from devtools.commands import DevCommand
from devtools.seed import DEFAULT_LLM_URL, DEFAULT_PASSWORD, Seeder
from runtime.errors import RegistryError


class DryRun(Exception):
    pass


class Command(DevCommand):
    help = "確認用のデータ（ユーザー、偽のLLMのモデル設定、確認用のConnector等）を入れる"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="変更せずに、何をするかを表示する")
        parser.add_argument(
            "--password", default=DEFAULT_PASSWORD, help=f"確認用のユーザーのパスワード（既定 {DEFAULT_PASSWORD}）"
        )
        parser.add_argument("--llm-url", default=DEFAULT_LLM_URL, help=f"偽のLLMのbase URL（既定 {DEFAULT_LLM_URL}）")
        parser.add_argument(
            "--sandbox-image", default="", help="Runtime Profileに登録するイメージ（名前@sha256:digest）"
        )
        parser.add_argument("--skip-database", action="store_true", help="確認用のスキーマとConnectorを作らない")

    def handle(self, *args, **options):
        seeder = Seeder(
            password=options["password"],
            llm_url=options["llm_url"],
            sandbox_image=options["sandbox_image"],
            database=not options["skip_database"],
        )
        try:
            with transaction.atomic():
                seeder.run()
                if options["dry_run"]:
                    raise DryRun
        except DryRun:
            pass
        except RegistryError as exc:
            raise CommandError(f"Sandboxのイメージを確かめられません：{exc}") from exc
        except DatabaseError as exc:
            raise CommandError(f"確認用のスキーマ・ロールを作れません（ロールを作る権限が要ります）：{exc}") from exc
        for line in seeder.lines:
            self.stdout.write(line)
        label = "（--dry-run のため取り消しました）" if options["dry_run"] else ""
        self.stdout.write(
            self.style.SUCCESS(f"確認用のデータを入れました{label}。パスワードは {options['password']} です")
        )
