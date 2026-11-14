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


def test_public_gallery_builds_sample_urls_for_unprotected_rows() -> None:
    """剧照地址按站点的 sample 目录拼: 站点页面不渲染这段 HTML.

    paco 的公开剧照在 ``assets/sample/{番号}/l/{序号}.jpg``; 只有 ``Protected`` 为假的那几张能直接取.
    """
    data = {
        "Rows": [
            {"MovieID": "110526_100", "Filename": "1.jpg", "Protected": False},
            {"MovieID": "110526_100", "Filename": "2.jpg", "Protected": False},
            {"MovieID": "110526_100", "Filename": "3.jpg", "Protected": True},
            {"MovieID": "999999_99", "Filename": "other.jpg", "Protected": False},
        ]
    }
    assert _public_gallery(data, "110526_100", "https://www.pacopacomama.com", "l") == [
        "https://www.pacopacomama.com/assets/sample/110526_100/l/1.jpg",
        "https://www.pacopacomama.com/assets/sample/110526_100/l/2.jpg",
    ]


def test_public_gallery_empty_when_nothing_is_public() -> None:
    data = {"Rows": [{"MovieID": "110526_100", "Filename": "1.jpg", "Protected": True}]}
    assert _public_gallery(data, "110526_100", "https://www.pacopacomama.com", "l") == []


class _FakeClient:
    def __init__(self, *, movie_id: str = "110526_100") -> None:
        self.movie_id = movie_id
        self.json_url: str | None = None
        self.gallery_url: str | None = None

    async def get_json(self, url: str, **kwargs: object) -> dict[str, object]:
        # 剧照接口与详情接口是两条路径, 按 URL 分流.
        if "movie_galleries" in url:
            self.gallery_url = url
            return {
                "Rows": [
                    {"MovieID": self.movie_id, "Filename": "1.jpg", "Protected": False},
                    {"MovieID": self.movie_id, "Filename": "2.jpg", "Protected": True},
                ]
            }
        self.json_url = url
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


@pytest.mark.asyncio
async def test_scrape_url_turns_page_url_into_detail_json() -> None:
    """手动搜索粘页面地址时, 换成本站详情 JSON 再解析.

    ``_scrape`` 只认 ``/movie_id/xxx.json``: 拿页面地址直接进去, 正则匹配不到番号会返回 None.
    """
    client = _FakeClient()
    crawler = PacopacomamaCrawler(client)  # type: ignore[arg-type]

    result = await crawler.scrape_url("https://www.pacopacomama.com/movies/110526_100/")

    assert result is not None
    assert result.number == "110526_100"
    assert client.json_url == (
        "https://www.pacopacomama.com/dyn/phpauto/movie_details/movie_id/110526_100.json"
    )


@pytest.mark.asyncio
async def test_scrape_url_rejects_address_without_number() -> None:
    """地址里没有番号时不乱猜: 交给下一家来源."""
    client = _FakeClient()
    crawler = PacopacomamaCrawler(client)  # type: ignore[arg-type]

    assert await crawler.scrape_url("https://www.pacopacomama.com/movies/") is None
    assert client.json_url is None
    assert client.gallery_url is None


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
    result = await crawler._scrape("https://www.pacopacomama.com/dyn/phpauto/movie_details/movie_id/110526_100.json")
    assert result is not None
    assert client.json_url is not None
    assert client.gallery_url == ("https://www.pacopacomama.com/dyn/phpauto/movie_galleries/movie_id/110526_100.json")
    assert result.number == "110526_100"
    assert result.external_id == "110526_100"
    assert result.source_url == "https://www.pacopacomama.com/movies/110526_100/"
    assert result.title == "人妻中出しベスト パート2"
    assert result.plot == "日本語の原剧情"
    assert [actor.name for actor in result.actors] == ["工藤れいか", "戸田さやか"]
    assert result.studio == "パコパコママ"
    assert result.publisher == "パコパコママ"
    assert result.release == "2026-11-05"
    assert result.runtime == 180
    assert result.series == "ベスト"
    assert result.tags == ["熟女/人妻", "美乳"]
    poster = "https://www.pacopacomama.com/moviepages/110526_100/images/l_hd.jpg"
    assert result.poster_urls == [poster]
    assert result.thumb_urls == [poster]
    assert result.extrafanart == ["https://www.pacopacomama.com/assets/sample/110526_100/l/1.jpg"]
    assert result.trailer_urls == []


@pytest.mark.asyncio
async def test_scrape_rejects_mismatched_movie_id_without_loading_gallery() -> None:
    client = _FakeClient(movie_id="110526_101")
    crawler = PacopacomamaCrawler(client)  # type: ignore[arg-type]
    result = await crawler._scrape("https://www.pacopacomama.com/dyn/phpauto/movie_details/movie_id/110526_100.json")
    assert result is None
    assert client.gallery_url is None
