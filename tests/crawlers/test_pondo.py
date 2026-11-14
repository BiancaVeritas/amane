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


def test_public_gallery_builds_sample_urls_for_unprotected_rows() -> None:
    """剧照地址按站点的 sample 目录拼: 站点自己的页面不渲染这段 HTML.

    1pondo 的公开剧照在 ``assets/sample/{番号}/popu/{序号}.jpg``; 只有 ``Protected`` 为假的
    那几张能直接取, 其余需要登录.
    """
    data = {
        "Rows": [
            {"MovieID": "110526_001", "Filename": "1.jpg", "Protected": False},
            {"MovieID": "110526_001", "Filename": "2.jpg", "Protected": False},
            {"MovieID": "110526_001", "Filename": "3.jpg", "Protected": True},
            {"MovieID": "999999_99", "Filename": "other.jpg", "Protected": False},
        ]
    }
    assert _public_gallery(data, "110526_001", "https://www.1pondo.tv", "popu") == [
        "https://www.1pondo.tv/assets/sample/110526_001/popu/1.jpg",
        "https://www.1pondo.tv/assets/sample/110526_001/popu/2.jpg",
    ]


def test_public_gallery_empty_when_nothing_is_public() -> None:
    data = {"Rows": [{"MovieID": "110526_001", "Filename": "1.jpg", "Protected": True}]}
    assert _public_gallery(data, "110526_001", "https://www.1pondo.tv", "popu") == []


class _FakeClient:
    async def get_json(self, url: str, **kwargs: object) -> dict:
        # 剧照接口与详情接口是两条路径, 按 URL 分流.
        if "movie_galleries" in url:
            return {
                "Rows": [
                    {"MovieID": "110526_001", "Filename": "1.jpg", "Protected": False},
                    {"MovieID": "110526_001", "Filename": "2.jpg", "Protected": True},
                ]
            }
        return {
            "MovieID": "110526_001",
            "Title": "Title",
            "Desc": "Plot",
            "Actor": "Actress",
            "Release": "2026-11-05",
            "Duration": 3672,
            "Series": "Series",
            "UCNAME": ["Tag A"],
            "UCNAMEEn": ["Tag B"],
            "ThumbUltra": "main.jpg",
            "SampleFiles": ["trailer.mp4"],
            "MemberFiles": ["restricted.mp4"],
        }


@pytest.mark.asyncio
async def test_scrape_maps_public_metadata_only() -> None:
    crawler = PondoCrawler(_FakeClient())  # type: ignore[arg-type]
    result = await crawler._scrape("https://www.1pondo.tv/dyn/phpauto/movie_details/movie_id/110526_001.json")
    assert result is not None
    assert result.runtime == 61
    assert result.poster_urls == ["main.jpg"]
    assert result.thumb_urls == ["main.jpg"]
    assert result.extrafanart == ["https://www.1pondo.tv/assets/sample/110526_001/popu/1.jpg"]
    assert result.trailer_urls == ["trailer.mp4"]
    # 日文标签优先, 英文名只在日文为空时用: 两套并进来会让界面上一串日文夹一串英文.
    assert result.tags == ["Tag A"]
    assert result.studio == "一本道"
    assert result.publisher == "一本道"


@pytest.mark.asyncio
async def test_gallery_failure_does_not_lose_metadata() -> None:
    """剧照接口挂了不影响其余字段: 它是补充信息, 不是必得项."""

    class _GalleryDownClient(_FakeClient):
        async def get_json(self, url: str, **kwargs: object) -> dict:
            if "movie_galleries" in url:
                raise RuntimeError("boom")
            return await super().get_json(url, **kwargs)  # type: ignore[misc]

    crawler = PondoCrawler(_GalleryDownClient())  # type: ignore[arg-type]
    result = await crawler._scrape("https://www.1pondo.tv/dyn/phpauto/movie_details/movie_id/110526_001.json")

    assert result is not None
    assert result.title == "Title"
    assert result.extrafanart == []

@pytest.mark.asyncio
async def test_tags_never_include_english_names() -> None:
    """英文标签一律不取.

    UCNAMEEn 只是同一批标签的英文写法, 并进来会让界面上日文与英文各占一半.
    即使日文为空也不拿英文补 —— 宁可没有标签.
    """

    class _EnglishOnlyClient(_FakeClient):
        async def get_json(self, url: str, **kwargs: object) -> dict:
            data = await super().get_json(url, **kwargs)  # type: ignore[misc]
            if "movie_details" in url:
                return {**data, "UCNAME": [], "UCNAMEEn": ["Amateur", "Big Tits"]}
            return data

    crawler = PondoCrawler(_EnglishOnlyClient())  # type: ignore[arg-type]
    result = await crawler._scrape("https://www.1pondo.tv/dyn/phpauto/movie_details/movie_id/110526_001.json")

    assert result is not None
    assert result.tags == []
