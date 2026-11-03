"""確認用の偽のLLM（OpenAI互換）を起動するコマンド。

台本（JSON）のルールで応答を決める。台本は要求ごとに読み直すので、起動したまま書き換えられる。
台本の書き方と、接続の登録（seed_dev）は docs/development.md §6

Usage:
    # 既定の台本（devtools/scenarios/default.json）で 127.0.0.1:8765 に起動する
    python manage.py fake_llm

    # 自分の台本で起動し、受けた要求をファイルに書き出す
    python manage.py fake_llm --scenario /tmp/scenario.json --dump /tmp/fake-llm-requests
"""

from pathlib import Path

from django.core.management.base import CommandError

from devtools.commands import DevCommand
from devtools.fake_llm import DEFAULT_SCENARIO, FakeLLMServer, Responder, ScenarioError


class Command(DevCommand):
    help = "確認用の偽のLLM（OpenAI互換）を起動する"

    def add_arguments(self, parser):
        parser.add_argument("--host", default="127.0.0.1", help="待ち受けるアドレス（既定 127.0.0.1）")
        parser.add_argument("--port", type=int, default=8765, help="待ち受けるポート（既定 8765）")
        parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO, help="台本のJSONファイル")
        parser.add_argument("--dump", type=Path, help="受けた要求を1件ずつJSONで書き出すディレクトリ")

    def handle(self, *args, **options):
        responder = Responder(options["scenario"].resolve())
        try:
            scenario = responder.scenario()
        except (ScenarioError, OSError) as exc:
            raise CommandError(str(exc)) from exc
        if options["dump"]:
            options["dump"].mkdir(parents=True, exist_ok=True)
        server = FakeLLMServer(
            (options["host"], options["port"]),
            responder,
            log=self._log,
            dump_dir=options["dump"],
        )
        self._log(
            self.style.SUCCESS(
                f"偽のLLMを http://{options['host']}:{options['port']}/v1 で起動しました"
                f"（台本：{responder.path}、ルール {len(scenario.rules):,}件）。Ctrl-Cで止めます"
            )
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()

    def _log(self, line: str) -> None:
        # ファイルへ書き出すときも、要求ごとにすぐ見えるようにする
        self.stdout.write(line)
        self.stdout.flush()
