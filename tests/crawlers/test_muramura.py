"""Muramura 解析: 与 1pondo 同一套详情接口, 字段结构一致."""

from __future__ import annotations

import pytest

from amane.crawlers import registry
from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.muramura import MuramuraCrawler, _muramura_number
from amane.enums import SiteName

_DETAIL = {
    "MovieID": "020616_348",
    "Title": "ムラムラってくる素人 テスト",
    "TitleEn": "Test Title",
    "Desc": "説明文です。",
    "ActressesJa": ["宮代薫"],
    "Duration": 3637,
    "Release": "2026-11-08",
    "Series": "テストシリーズ",
    "UCNAME": ["素人", "美脚"],
    "UCNAMEEn": ["Amateur", "Sexy Legs"],
    "ThumbUltra": "https://www.muramura.tv/moviepages/020616_348/images/str.jpg",
    "SampleFiles": [{"URL": "https://www.muramura.tv/sample/1.mp4"}],
}


class _FakeClient:
    def __init__(self, detail: dict, gallery: dict | None = None) -> None:
        self._detail = detail
        self._gallery = gallery or {"Rows": []}
        self.urls: list[str] = []

    async def get_json(self, url: str, **kwargs: object) -> dict:
        del kwargs
        self.urls.append(url)
        if "movie_galleries" in url:
            return self._gallery
        return self._detail


def test_muramura_registered() -> None:
    assert registry.get("muramura") is MuramuraCrawler


def test_profile_uses_own_site() -> None:
    profile = MuramuraCrawler.profile()
    assert profile.name == SiteName.MURAMURA
    assert "muramura.tv" in profile.base_url


def test_detail_url_from_number() -> None:
    crawler = MuramuraCrawler(client=_FakeClient(_DETAIL))  # type: ignore[arg-type]
    assert (
        crawler._detail_url("020616_348")
        == "https://www.muramura.tv/dyn/phpauto/movie_details/movie_id/020616_348.json"
    )
    # 不是本站的番号格式一律拒绝.
    assert crawler._detail_url("ABC-123") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 序号 3 位与 4 位都有: 不能直接套用 1pondo 的 6+3 校验.
        ("020616_348", "020616_348"),
        ("110826_1274", "110826_1274"),
        ("103126_1273", "103126_1273"),
        ("100426_1267", "100426_1267"),
        # 非法: 位数不符或有别的字符.
        ("020616_34", None),
        ("020616_34812", None),
        ("ABC-123", None),
        ("", None),
        (None, None),
    ],
)
def test_number_accepts_three_and_four_digit_sequence(raw: str | None, expected: str | None) -> None:
    """本站番号的序号是 3~4 位.

    只认 3 位时 ``110826_1274`` 这类会被判成非法, _search 直接返回 None,
    表现为「这个番号搜不到」而站点其实有.
    """
    assert _muramura_number(raw) == expected


@pytest.mark.asyncio
async def test_scrape_maps_detail_fields() -> None:
    client = _FakeClient(_DETAIL)
    crawler = MuramuraCrawler(client)  # type: ignore[arg-type]

    result = await crawler.scrape_url("https://www.muramura.tv/movies/020616_348/")

    assert result is not None
    assert result.number == "020616_348"
    assert result.title == "ムラムラってくる素人 テスト"
    assert [a.name for a in result.actors] == ["宮代薫"]
    assert result.release == "2026-11-08"
    # 3637 秒 -> 61 分钟.
    assert result.runtime == 61
    assert result.series == "テストシリーズ"
    # 只取日文标签, 英文名不并进来.
    assert result.tags == ["素人", "美脚"]
    assert result.poster_urls == ["https://www.muramura.tv/moviepages/020616_348/images/str.jpg"]
    assert result.source_url == "https://www.muramura.tv/movies/020616_348/"


@pytest.mark.asyncio
async def test_scrape_rejects_mismatched_number() -> None:
    """站点对不存在的番号会返回别的记录, 必须核对番号, 否则会把别人的数据写成这条."""
    wrong = {**_DETAIL, "MovieID": "110826_1274"}
    crawler = MuramuraCrawler(_FakeClient(wrong))  # type: ignore[arg-type]

    assert await crawler.scrape_url("https://www.muramura.tv/movies/020616_348/") is None


@pytest.mark.asyncio
async def test_search_returns_detail_url_directly() -> None:
    """搜索不需要站点搜索页: 番号本身就是详情地址的一部分."""
    crawler = MuramuraCrawler(_FakeClient(_DETAIL))  # type: ignore[arg-type]
    url = await crawler._search(SearchQuery("020616_348"))
    assert url is not None
    assert url.endswith("/movie_details/movie_id/020616_348.json")
