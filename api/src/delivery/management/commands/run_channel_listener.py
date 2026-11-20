"""channel-listener を動かすコマンド（design.md §10、deployment.md §3.1）。

常時接続で受ける対話の出口（DiscordのGateway）の接続ごとに担当を取り、スレッドへの返信を受けて記録する。何台で
動かしてもよい（接続ごとにDBのリースで担当を1つに決める）。SIGTERM・SIGINTで、担当を手放して止まる。

Usage:
    python manage.py run_channel_listener
"""

import asyncio
import logging
import signal

from django.core.management.base import BaseCommand

from delivery.channels.listener import ChannelListener


class Command(BaseCommand):
    help = "常時接続で受ける対話の出口（Discord）の返信を受ける"

    def handle(self, *args, **options):
        logging.getLogger("delivery.channels").setLevel(logging.INFO)
        asyncio.run(self._run())

    async def _run(self) -> None:
        listener = ChannelListener()
        task = asyncio.create_task(listener.run())
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, task.cancel)
        self.stdout.write(f"channel listener {listener.owner}")
        try:
            await task
        except asyncio.CancelledError:
            self.stdout.write("stopped")
