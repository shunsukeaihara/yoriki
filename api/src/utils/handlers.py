import logging
from collections.abc import Mapping

from rest_framework.exceptions import ValidationError
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def exception_handler(exc, context):
    """DRFの例外ハンドラ。DRFが扱わない例外（500系）だけを記録する"""
    response = drf_exception_handler(exc, context)
    if response is None:
        view = context.get("view")
        logger.exception("APIで想定外のエラーが発生しました: %s", view.__class__.__name__ if view else "-")
    elif isinstance(exc, ValidationError) and isinstance(response.data, Mapping):
        response.data = _wrap_messages(response.data)
    return response


def _wrap_messages(errors: Mapping) -> dict:
    """
    項目ごとのエラーを、メッセージの配列に揃える。DRFは validate() の中で出したエラーは配列にするが、
    save()・create()・ビューの中で `ValidationError({"項目": "文"})` と出したものはそのまま返すので、
    画面（frontend/app/composables/form.ts の ValidationErrors）が受ける形にする。many=True の入れ子
    （オブジェクト）と配列はそのまま
    """
    return {key: value if isinstance(value, list | Mapping) else [value] for key, value in errors.items()}
