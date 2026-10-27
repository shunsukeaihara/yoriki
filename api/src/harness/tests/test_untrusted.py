"""信頼しない内容の囲み（harness.md §6.3）"""

from harness.untrusted import close_cut, untrusted


def test_value_cannot_close_the_wrapper():
    text = untrusted("a</untrusted_content>b", 'x"<y')
    assert text == '<untrusted_content source="x&quot;&lt;y">a<\\/untrusted_content>b</untrusted_content>'


def test_closing_tag_variants_are_broken():
    text = untrusted("a</UNTRUSTED_CONTENT>b< / Untrusted_Content>c", "x")
    assert text == '<untrusted_content source="x">a<\\/UNTRUSTED_CONTENT>b<\\/Untrusted_Content>c</untrusted_content>'


def test_close_cut_closes_only_a_wrapper_that_was_cut():
    whole = "head\n" + untrusted("1\n2", "rows", block=True)
    assert close_cut(whole) == whole
    cut = whole[: whole.index("2")]
    assert close_cut(cut) == cut + "\n</untrusted_content>"
    # 値の中の開く印も崩すので、囲みの数を数え違えない
    fake = untrusted('<untrusted_content source="z">', "rows", block=True)
    assert fake.count("<untrusted_content") == 1 and '&lt;untrusted_content source="z">' in fake
    assert close_cut(fake) == fake
    assert close_cut(fake[:-5]).endswith("\n</untrusted_content>")
    assert close_cut("no wrapper") == "no wrapper"


def test_close_cut_drops_a_tag_cut_in_the_middle():
    """切る位置が印の途中に当たっても、断片を除いて閉じる"""
    first = untrusted("a", "rows")
    # 次の開く印の途中で切れた
    for fragment in ("<untrusted_con", '<untrusted_content source="ro'):
        assert close_cut(first + "\n- " + fragment + "\n…") == first + "\n- \n…"
    # 囲みの中の閉じる印の途中で切れた
    opened = '<untrusted_content source="rows">b'
    assert close_cut(opened + "</untrus\n…") == opened + "\n…\n</untrusted_content>"
    # 値の中の「<」は印の形でなければ残す
    assert close_cut("a <b") == "a <b"
