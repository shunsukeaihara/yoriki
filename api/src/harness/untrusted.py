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
        body = f"\n{body}\n"
    return f'<untrusted_content source="{_escape(source)}">{body}</untrusted_content>'
