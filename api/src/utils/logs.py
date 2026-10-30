"""ログに資格情報を残さないためのフィルター。

出口へ渡した成果物の取得用のURL（`/api/files/<token>/`）は、tokenだけで期限の間ファイルを取得できる。DBにはハッシュ
だけを保存するので、アクセスログにも残さない（design.md §4.4。nginxでも同じく伏せる：deploy/nginx）。
"""

import logging
import re

FILE_TOKEN_PATH = re.compile(r"(/files/)[^/?\s\"]+")
MASKED = r"\1***"


def mask_file_tokens(text: str) -> str:
    return FILE_TOKEN_PATH.sub(MASKED, text)


class MaskFileTokens(logging.Filter):
    """ログの文と引数の中の `/files/<token>` を伏せる（uvicornのアクセスログは、パスを引数で渡す）"""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = mask_file_tokens(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(mask_file_tokens(a) if isinstance(a, str) else a for a in record.args)
        return True


def install() -> None:
    """uvicornのアクセスログに付ける。uvicornはDjangoより先に自分のログを設定するので、Djangoの LOGGING ではなく
    アプリを読み込むときに付ける（Djangoのログは LOGGING の console に付けている）"""
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, MaskFileTokens) for f in logger.filters):
        logger.addFilter(MaskFileTokens())
