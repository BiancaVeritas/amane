"""Jav321 字段提取边界."""

from parsel import Selector

from amane.crawlers.sites.jav321 import _clean_url, _field_links, _field_text


def test_clean_url_collapses_duplicate_path_slashes_only() -> None:
    assert _clean_url("https://pics.dmm.co.jp//digital/video/a.jpg") == "https://pics.dmm.co.jp/digital/video/a.jpg"


def test_field_text_and_links_stop_at_next_label() -> None:
    html = Selector(
        text="""<div><b>品番</b>: ABC-123<br><b>メーカー</b><a>Studio A</a><br>
        <b>ジャンル</b><a>Drama</a><a>Romance</a><br><b>シリーズ</b><a>Series X</a></div>"""
    )
    assert _field_text(html, "品番") == "ABC-123"
    assert _field_links(html, "ジャンル") == ["Drama", "Romance"]
