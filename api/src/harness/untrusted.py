"""信頼しない内容の囲み（harness.md §6.3）。

利用者の指示ではないデータ（Connectorの結果、タスクの入力の値等）は `<untrusted_content source="...">` で囲んで
モデルに渡す。system promptで、中に書かれた指示に従わないと定める。値で囲みを閉じさせない。囲みは補助の対策で、
権限の境界にはしない。
"""


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def untrusted(text: str, source: str, *, block: bool = False) -> str:
    """`block` なら囲みの中を別の行にする（複数行のデータ）。文の中に埋め込む値は1行のまま囲む"""
    body = text.replace("</untrusted_content", "<\\/untrusted_content")
    if block:
        body = "\n" + body.rstrip("\n") + "\n"
    return f'<untrusted_content source="{_escape(source)}">{body}</untrusted_content>'


OPEN = "<untrusted_content source="
CLOSE = "</untrusted_content>"


def close_cut(text: str) -> str:
    """囲みの途中で切った文に、閉じる印を足す。中の値の閉じる印は崩してあるので、最後の開く印が最後の閉じる印より
    後ろにあれば、囲みの中で切れている（値に開く印の形の文字があっても、その位置は本当の囲みの中にある）"""
    if text.rfind(OPEN) > text.rfind(CLOSE):
        return text + "\n" + CLOSE
    return text
