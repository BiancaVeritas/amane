from __future__ import annotations

from amane.crawlers.sites.avbase import build_metadata


def test_avbase_marks_only_real_thumbnail_as_original_poster() -> None:
    paired = build_metadata(
        {
            "work_id": "ABC-001",
            "products": [{"image_url": "https://example.com/cover.jpg", "thumbnail_url": "https://example.com/poster.jpg"}],
        },
        url="https://www.avbase.net/works/ABC-001",
    )
    assert paired is not None
    assert paired.original_poster_urls == ["https://example.com/poster.jpg"]
    assert paired.double_page_poster_urls == ["https://example.com/poster.jpg"]
    assert paired.double_page_thumb_urls == ["https://example.com/cover.jpg"]

    single = build_metadata(
        {"work_id": "ABC-002", "products": [{"image_url": "https://pics.dmm.co.jp/mono/movie/adult/abc002/abc002pl.jpg"}]},
        url="https://www.avbase.net/works/ABC-002",
    )
    assert single is not None
    assert single.poster_urls
    assert single.original_poster_urls == []
    assert single.double_page_poster_urls == []
