from __future__ import annotations

import pytest

from amane.crawlers import registry
from amane.crawlers.sites.pondo import PondoCrawler, _number, _public_gallery
from amane.enums import SiteName


def test_pondo_registered() -> None:
    assert registry.get(SiteName.PONDO) is PondoCrawler


def test_number_accepts_only_pondo_format_without_normalization() -> None:
    assert _number("110526_001") == "110526_001"
    assert _number("071826_001") == "071826_001"
    assert _number("071826_01") is None
    assert _number("071826-001") is None
    assert _number("110526_01") is None
    assert _number("110526_001 ") is None


def test_public_gallery_uses_only_clickable_public_nodes() -> None:
    html = '''<div class="gallery-image-wrap"><img class="gallery-image pointer" data-vue-img-src="/open/a.jpg"></div>
    <div class="gallery-image-wrap is-disabled"><img class="gallery-image" src="/locked/b.jpg"></div>
    <img class="gallery-image pointer" src="/not-original.jpg">'''
    assert _public_gallery(html, "https://www.1pondo.tv/movies/110526_001/") == [
        "https://www.1pondo.tv/open/a.jpg"
    ]


class _FakeClient:
    async def get_json(self, url: str, **kwargs: object) -> dict:
        return {
            "MovieID": "110526_001", "Title": "Title", "Desc": "Plot",
            "Actor": "Actress", "Release": "2026-11-05", "Duration": 3672,
            "Series": "Series", "UCNAME": ["Tag A"], "UCNAMEEn": ["Tag B"],
            "ThumbUltra": "main.jpg", "SampleFiles": ["trailer.mp4"],
            "MemberFiles": ["restricted.mp4"],
        }

    async def get_html(self, url: str, **kwargs: object) -> str:
        return '<img class="gallery-image pointer" data-vue-img-src="/gallery/open.jpg"><div class="gallery-image-wrap is-disabled"><img class="gallery-image"></div>'


@pytest.mark.asyncio
async def test_scrape_maps_public_metadata_only() -> None:
    crawler = PondoCrawler(_FakeClient())  # type: ignore[arg-type]
    result = await crawler._scrape("https://www.1pondo.tv/dyn/phpauto/movie_details/movie_id/110526_001.json")
    assert result is not None
    assert result.runtime == 61
    assert result.poster_urls == ["main.jpg"]
    assert result.thumb_urls == ["main.jpg"]
    assert result.extrafanart == ["https://www.1pondo.tv/gallery/open.jpg"]
    assert result.trailer_urls == ["trailer.mp4"]
    assert result.tags == ["Tag A", "Tag B"]
    assert result.studio == "一本道"
    assert result.publisher == "1pondo"
