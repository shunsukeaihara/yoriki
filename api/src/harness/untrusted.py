"""信頼しない内容の囲み（harness.md §6.3）。

利用者の指示ではないデータ（Connectorの結果、タスクの入力の値等）は `<untrusted_content source="...">` で囲んで
モデルに渡す。system promptで、中に書かれた指示に従わないと定める。値で囲みを閉じさせない。囲みは補助の対策で、
権限の境界にはしない。
"""

import re

# 値の中の閉じる印と開く印。大文字・小文字と空白の揺れも崩す
CLOSING = re.compile(r"<\s*/\s*(untrusted_content)", re.IGNORECASE)
OPENING = re.compile(r"<\s*(untrusted_content)", re.IGNORECASE)


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def untrusted(text: str, source: str, *, block: bool = False) -> str:
    """`block` なら囲みの中を別の行にする（複数行のデータ）。文の中に埋め込む値は1行のまま囲む"""
    body = OPENING.sub(r"&lt;\1", CLOSING.sub(r"<\\/\1", text))
    if block:
        body = "\n" + body.rstrip("\n") + "\n"
    return f'<untrusted_content source="{_escape(source)}">{body}</untrusted_content>'


OPEN = "<untrusted_content source="
CLOSE = "</untrusted_content>"


def close_cut(text: str) -> str:
    """囲みの途中で切った文に、閉じる印を足す。中の値の開く印・閉じる印は崩してあるので、最後の開く印が最後の
    閉じる印より後ろにあれば、囲みの中で切れている"""
    if text.rfind(OPEN) > text.rfind(CLOSE):
        return text + "\n" + CLOSE
    return text
