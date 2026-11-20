"""対話の出口の受信の記録の清掃（design.md §8）。

Runに結び付かなかった受信（照合できない・取り込まない）は、処理した時点で本文・表示名・ボタンの値を消している。
その行を、受けてから30日で消す。Runに結び付いた受信とスレッドの対応は、Runの履歴と同じく自動では消さない。
"""

from datetime import timedelta

from django.utils import timezone

from .models import ChannelMessageReceipt, ReceiptResult

# Runに結び付かなかった受信の行を残す日数（基盤の設定にはしない）
UNBOUND_RECEIPT_DAYS = 30


def cleanup_receipts(*, dry_run: bool = False) -> int:
    """受けてから日数を過ぎた、Runに結び付かなかった処理済みの受信を消す。消した（`dry_run` なら対象の）数"""
    targets = ChannelMessageReceipt.objects.filter(
        run__isnull=True,
        processed__isnull=False,
        created__lt=timezone.now() - timedelta(days=UNBOUND_RECEIPT_DAYS),
    ).exclude(result=ReceiptResult.RECEIVED)
    if dry_run:
        return targets.count()
    deleted, _ = targets.delete()
    return deleted
