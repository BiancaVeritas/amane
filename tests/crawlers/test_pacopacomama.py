from __future__ import annotations

import pytest

from amane.crawlers import registry
from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.pacopacomama import PacopacomamaCrawler, _number, _public_gallery
from amane.enums import SiteName


def test_pacopacomama_registered() -> None:
    assert registry.get(SiteName.PACOPACOMAMA) is PacopacomamaCrawler


def test_number_requires_exact_six_three_underscore_format() -> None:
    assert _number("110526_100") == "110526_100"
    assert _number("000001_001") == "000001_001"
    assert _number("110526_10") is None
    assert _number("110526_1000") is None
    assert _number("110526-100") is None
    assert _number("110526_100 ") is None


def test_public_gallery_uses_only_full_size_public_nodes() -> None:
    html = '''
    <img class="gallery-image pointer" data-vue-img-src="/dyn/dla/images/movie_gallery/sample/110526_100/a.jpg">
    <img class="gallery-image" src="/thumb/b.jpg">
    <img class="gallery-image pointer" src="/thumb/c.jpg">
    <img class="gallery-image pointer" data-vue-img-src="/dyn/dla/images/movie_gallery/sample/110526_100/a.jpg">
    <img class="gallery-image pointer" data-vue-img-src="/dyn/dla/images/movie_gallery/sample/110526_100/d.jpg">
    '''
    assert _public_gallery(html, "https://www.pacopacomama.com/movies/110526_100/") == [
        "https://www.pacopacomama.com/dyn/dla/images/movie_gallery/sample/110526_100/a.jpg",
        "https://www.pacopacomama.com/dyn/dla/images/movie_gallery/sample/110526_100/d.jpg",
    ]


class _FakeClient:
    def __init__(self, *, movie_id: str = "110526_100") -> None:
        self.movie_id = movie_id
        self.json_url: str | None = None
        self.html_url: str | None = None

    async def get_json(self, url: str, **kwargs: object) -> dict[str, object]:
        self.json_url = url
        assert kwargs.get("headers") == {"Accept": "application/json"}
        return {
            "MovieID": self.movie_id,
            "Title": "人妻中出しベスト パート2",
            "TitleEn": "Must not be used",
            "Desc": "日本語の原剧情",
            "DescEn": "Must not be used",
            "ActressesJa": ["工藤れいか", "戸田さやか"],
            "ActressesEn": ["Must not be used"],
            "ActorID": [5244, 6631],
            "Release": "2026-11-05",
            "Duration": 10800,
            "Series": "ベスト",
            "SeriesEn": "Must not be used",
            "UCNAME": ["熟女/人妻", "美乳", "熟女/人妻"],
            "UCNAMEEn": ["Must not be used"],
            "ThumbHigh": "https://www.pacopacomama.com/moviepages/110526_100/images/l_hd.jpg",
            "SampleFiles": [{"URL": "https://example.com/sample.mp4"}],
        }

    async def get_html(self, url: str, **kwargs: object) -> str:
        self.html_url = url
        return (
            '''<img class="gallery-image pointer" data-vue-img-src="/dyn/dla/images/movie_gallery/sample/110526_100/a.jpg">
            <img class="gallery-image" src="/thumb/locked.jpg">'''
        )


@pytest.mark.asyncio
async def test_search_uses_exact_json_detail_endpoint() -> None:
    crawler = PacopacomamaCrawler(_FakeClient())  # type: ignore[arg-type]
    assert await crawler._search(SearchQuery(number="110526_100")) == (
        "https://www.pacopacomama.com/dyn/phpauto/movie_details/movie_id/110526_100.json"
    )
    assert await crawler._search(SearchQuery(number="110526-100")) is None


@pytest.mark.asyncio
async def test_scrape_maps_only_requested_japanese_metadata() -> None:
    client = _FakeClient()
    crawler = PacopacomamaCrawler(client)  # type: ignore[arg-type]
    result = await crawler._scrape(
        "https://www.pacopacomama.com/dyn/phpauto/movie_details/movie_id/110526_100.json"
    )
    assert result is not None
    assert client.json_url is not None
    assert client.html_url == "https://www.pacopacomama.com/movies/110526_100/"
    assert result.number == "110526_100"
    assert result.external_id == "110526_100"
    assert result.source_url == "https://www.pacopacomama.com/movies/110526_100/"
    assert result.title == "人妻中出しベスト パート2"
    assert result.plot == "日本語の原剧情"
    assert [actor.name for actor in result.actors] == ["工藤れいか", "戸田さやか"]
    assert result.studio == "パコパコママ"
    assert result.publisher == "Pacopacomama"
    assert result.release == "2026-11-05"
    assert result.runtime == 180
    assert result.series == "ベスト"
    assert result.tags == ["熟女/人妻", "美乳"]
    poster = "https://www.pacopacomama.com/moviepages/110526_100/images/l_hd.jpg"
    assert result.poster_urls == [poster]
    assert result.thumb_urls == [poster]
    assert result.extrafanart == [
        "https://www.pacopacomama.com/dyn/dla/images/movie_gallery/sample/110526_100/a.jpg"
    ]
    assert result.trailer_urls == []


@pytest.mark.asyncio
async def test_scrape_rejects_mismatched_movie_id_without_loading_detail_page() -> None:
    client = _FakeClient(movie_id="110526_101")
    crawler = PacopacomamaCrawler(client)  # type: ignore[arg-type]
    result = await crawler._scrape(
        "https://www.pacopacomama.com/dyn/phpauto/movie_details/movie_id/110526_100.json"
    )
    assert result is None
    assert client.html_url is None
