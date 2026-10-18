import logging

from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def exception_handler(exc, context):
    """DRFの例外ハンドラ。DRFが扱わない例外（500系）だけを記録する"""
    response = drf_exception_handler(exc, context)
    if response is None:
        view = context.get("view")
        logger.exception("APIで想定外のエラーが発生しました: %s", view.__class__.__name__ if view else "-")
    return response
