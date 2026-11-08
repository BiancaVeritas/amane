from __future__ import annotations

import pytest

from amane.crawlers import registry
from amane.crawlers.sites.tenmusume import TenmusumeCrawler, _number
from amane.enums import SiteName


def test_tenmusume_registered() -> None:
    assert registry.get(SiteName.TENMUSUME) is TenmusumeCrawler


def test_number_accepts_only_tenmusume_format_without_normalization() -> None:
    assert _number("110426_01") == "110426_01"
    assert _number("071826_01") == "071826_01"
    assert _number("071826_001") is None
    assert _number("071826-01") is None
    assert _number("110426_1") is None
    assert _number("110426_001") is None
    assert _number("110426_01 ") is None



class _FakeClient:
    async def get_json(self, url: str, **kwargs: object) -> dict:
        return {
            "MovieID": "110426_01", "Title": "Title", "Desc": "Plot",
            "Actor": "Actress", "Release": "2026-01-02", "Duration": 2274,
            "Series": "Series", "UCNAME": ["素人", "美脚"], "UCNAMEEn": ["Amateur"], "ThumbUltra": "main.jpg",
            "SampleFiles": [{"URL": "trailer.mp4"}],
            "MemberFiles": [
                {"Protected": False, "URL": "sample.jpg"},
                {"Protected": True, "URL": "locked.jpg"},
            ],
        }


@pytest.mark.asyncio
async def test_scrape_maps_metadata_and_filters_protected_images() -> None:
    crawler = TenmusumeCrawler(_FakeClient())  # type: ignore[arg-type]
    result = await crawler._scrape("https://www.10musume.com/dyn/phpauto/movie_details/movie_id/110426_01.json")
    assert result is not None
    assert result.runtime == 38
    assert result.poster_urls == ["main.jpg"]
    assert result.thumb_urls == ["main.jpg"]
    assert result.extrafanart == []
    assert result.trailer_urls == ["trailer.mp4"]
    assert result.tags == ["素人", "美脚", "Amateur"]
    assert result.studio == "天然むすめ"
    assert result.publisher == "10musume"
    assert result.source_url == "https://www.10musume.com/movies/110426_01/"
