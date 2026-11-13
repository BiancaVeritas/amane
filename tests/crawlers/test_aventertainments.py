from __future__ import annotations

from typing import cast

import pytest

from amane.crawlers import registry
from amane.crawlers.http import HttpClient
from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.aventertainments import (
    _detail_links,
    _image_bytes_valid,
    _is_bd_number,
    _metadata_from_page,
    _number_key,
    _search_terms,
    _version_links,
    lookup_pro,
)
from amane.enums import SiteName

DETAIL_URL = "https://www.aventertainments.com/dvd/detail?pro=87393&lang=2&cat=29"


def _detail_html(number: str = "S2MBD-046") -> str:
    return f"""<!doctype html>
    <html><head><title>Encore Vol.46</title>
      <meta property="og:image" content="https://imgs02.aventertainments.com/new/jacket_images/dvd1s2mbd-046.webp">
      <script type="application/ld+json">{{
        "@context":"https://schema.org", "@type":"VideoObject",
        "name":"Encore Vol.46", "description":"Example plot",
        "uploadDate":"2015-04-24T00:00:00+09:00",
        "contentUrl":"https://ppvclips02.aventertainments.com/00m3u8/{number}/{number}.m3u8"
      }}</script>
    </head><body>
      <div class="single-info"><span class="title">商品番号</span><span class="tag-title">{number}</span></div>
      <div class="single-info">
        <span class="title">主演女優</span>
        <span class="value"><a>Example Actress</a></span>
      </div>
      <div class="single-info"><span class="title">スタジオ</span><span class="value">Example Studio</span></div>
      <div class="single-info"><span class="title">シリーズ</span><span class="value">Example Series</span></div>
      <div class="single-info"><span class="title">発売日</span><span class="value">04/24/2015</span></div>
      <div class="single-info"><span class="title">収録時間</span><span class="value">Apx. 130 Min.</span></div>
      <div class="single-info">
        <span class="title">カテゴリ</span>
        <span class="value-category"><a>完全無修正</a><a>店長推薦</a></span>
      </div>
      <div class="tab-area">
        <a href="/dvd/detail?pro=87392&amp;lang=2&amp;cat=29" data-tippy="View on DVD">DVD</a>
        <a href="/dvd/detail?pro=87393&amp;lang=2&amp;cat=29" data-tippy="View on Blu-ray">Blu-ray</a>
      </div>
      <video id="player1_html5" poster="https://imgs02.aventertainments.com/new/bigcover/dvd1s2mbd-046.webp"></video>
      <a class="lightbox" href="https://imgs02.aventertainments.com/new/bigcover/dvd1s2mbd-046.webp">ジャケット画像</a>
    </body></html>"""


def test_aventertainments_is_registered_as_film_source() -> None:
    assert registry.get(SiteName.AVENTERTAINMENTS) is not None


def test_number_normalization_matches_bd_and_dvd_sibling() -> None:
    assert _number_key("S2MBD-046") == _number_key("S2M-046")
    assert _search_terms("S2M-046") == ["S2M-046", "S2MBD-046"]
    assert _search_terms("S2MBD-046") == ["S2MBD-046", "S2M-046"]


def test_parser_maps_jacket_to_poster_and_bigcover_to_thumb() -> None:
    metadata = _metadata_from_page(DETAIL_URL, _detail_html())
    assert metadata is not None
    assert metadata.number == "S2MBD-046"
    assert metadata.poster_urls == ["https://imgs02.aventertainments.com/new/jacket_images/dvd1s2mbd-046.webp"]
    assert metadata.thumb_urls == ["https://imgs02.aventertainments.com/new/bigcover/dvd1s2mbd-046.webp"]
    assert metadata.original_poster_urls == ["https://imgs02.aventertainments.com/new/jacket_images/dvd1s2mbd-046.webp"]
    assert metadata.double_page_poster_urls == metadata.poster_urls
    assert metadata.double_page_thumb_urls == metadata.thumb_urls
    assert metadata.release == "2015-04-24"
    assert metadata.runtime == 130
    assert metadata.actors[0].name == "Example Actress"


def test_parser_extracts_sample_screenshots_as_extrafanart() -> None:
    """剧照在「サンプル画像」弹窗里, 是一串 screenshot 图.

    详情页本身只展示封面, 不解析这个弹窗就一张剧照也拿不到.
    """
    shots = [
        f"https://imgs02.aventertainments.com/vodimages/screenshot/large/S2MBD-046/{n:03d}.webp" for n in (1, 2, 3)
    ]
    modal = (
        '<div class="modal-body" id="sscontainerppv1">'
        + "".join(f"<img class='img-fluid' src='{src}' />" for src in shots)
        + "</div>"
    )
    metadata = _metadata_from_page(DETAIL_URL, _detail_html() + modal)

    assert metadata is not None
    assert metadata.extrafanart == shots


def test_parser_extracts_bigcover_from_product_image_area_without_video_poster() -> None:
    bigcover = "https://imgs02.aventertainments.com/new/bigcover/dvd1smbd-06.webp"
    html = _detail_html("SMBD-06").replace(
        '<video id="player1_html5" poster="https://imgs02.aventertainments.com/new/bigcover/dvd1s2mbd-046.webp"></video>',
        '<div class="col-12" style="text-align: center;"><img src="' + bigcover + '" class="img-fluid" /></div>'
        '<a class="lightbox" href="' + bigcover + '">ジャケット画像</a>',
    )
    metadata = _metadata_from_page(
        "https://www.aventertainments.com/dvd/detail?pro=34593&lang=2&cat=29",
        html,
    )
    assert metadata is not None
    assert metadata.number == "SMBD-06"
    assert metadata.poster_urls == ["https://imgs02.aventertainments.com/new/jacket_images/dvd1s2mbd-046.webp"]
    assert metadata.thumb_urls == [bigcover]


def test_bd_number_with_prefix_and_hyphen_is_recognized() -> None:
    assert _is_bd_number("SMBD-06")
    assert _is_bd_number("S2MBD-046")
    assert not _is_bd_number("S2M-046")


def test_parser_does_not_use_video_poster_when_bigcover_link_is_missing() -> None:
    html = _detail_html().replace(
        '<a class="lightbox" href="https://imgs02.aventertainments.com/new/bigcover/dvd1s2mbd-046.webp">ジャケット画像</a>',
        "",
    )
    metadata = _metadata_from_page(DETAIL_URL, html)
    assert metadata is not None
    assert metadata.original_poster_urls == ["https://imgs02.aventertainments.com/new/jacket_images/dvd1s2mbd-046.webp"]
    assert metadata.thumb_urls == []
    assert metadata.double_page_poster_urls == []


def test_parser_prefers_real_product_description_over_jsonld_boilerplate() -> None:
    html = (
        _detail_html()
        .replace(
            '"description":"Example plot"',
            '"description":"Encore Vol.46 | AVエンターテインメント : site slogan"',
        )
        .replace(
            "</body></html>",
            '<div class="product-description mt-20">First synopsis paragraph<br/><br/>Second synopsis paragraph</div></body></html>',
        )
    )
    metadata = _metadata_from_page(DETAIL_URL, html)
    assert metadata is not None
    assert metadata.plot == "First synopsis paragraph Second synopsis paragraph"


def test_parser_does_not_use_jsonld_title_or_site_tagline_as_plot() -> None:
    html = _detail_html().replace(
        '"description":"Example plot"',
        '"description":"Encore Vol.46 | AVエンターテインメント : site slogan"',
    )
    metadata = _metadata_from_page(DETAIL_URL, html)
    assert metadata is not None
    assert metadata.plot is None


def test_version_links_identify_bd_and_dvd_tabs() -> None:
    bd, dvd = _version_links(DETAIL_URL, _detail_html())
    assert bd == "https://www.aventertainments.com/dvd/detail?pro=87393&lang=2&cat=29"
    assert dvd == "https://www.aventertainments.com/dvd/detail?pro=87392&lang=2&cat=29"


def test_search_result_links_are_limited_to_product_detail_pages() -> None:
    page = """<a href="/dvd/detail?pro=87393&amp;lang=2&amp;cat=29">Blu-ray</a>
              <a href="/dvd/detail?pro=87392&amp;lang=2&amp;cat=29">DVD</a>
              <a href="/ppv/detail?pro=10090">PPV</a>"""
    links = _detail_links(page, "https://www.aventertainments.com/dvd/search?keyword=S2M-046")
    assert len(links) == 2
    assert all("/dvd/detail?" in link for link in links)


def test_image_validator_rejects_html_and_accepts_webp() -> None:
    assert _image_bytes_valid(b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"data")
    assert not _image_bytes_valid(b"<!doctype html><title>not found</title>")


class _FakeClient:
    def __init__(self, pages: dict[str, str], images: dict[str, bytes]) -> None:
        self.pages = pages
        self.images = images

    async def get_html(self, url: str, **kwargs: object) -> str:
        return self.pages.get(url, "")

    async def get_bytes(self, url: str, **kwargs: object) -> bytes:
        return self.images.get(url, b"")


def _version_html(number: str, image_stem: str) -> str:
    html = _detail_html(number)
    html = html.replace("dvd1s2mbd-046.webp", f"{image_stem}.webp")
    return html


@pytest.mark.asyncio
async def test_scrape_prefers_bd_even_when_search_result_starts_at_dvd() -> None:
    from amane.crawlers.sites.aventertainments import AVEntertainmentsCrawler

    bd_url = "https://www.aventertainments.com/dvd/detail?pro=87393&lang=2&cat=29"
    dvd_url = "https://www.aventertainments.com/dvd/detail?pro=87392&lang=2&cat=29"
    bd_html = _version_html("S2MBD-046", "dvd1s2mbd-046")
    dvd_html = _version_html("S2M-046", "dvd1s2m-046")
    bd_jacket = "https://imgs02.aventertainments.com/new/jacket_images/dvd1s2mbd-046.webp"
    bd_cover = "https://imgs02.aventertainments.com/new/bigcover/dvd1s2mbd-046.webp"
    dvd_jacket = "https://imgs02.aventertainments.com/new/jacket_images/dvd1s2m-046.webp"
    dvd_cover = "https://imgs02.aventertainments.com/new/bigcover/dvd1s2m-046.webp"
    client = _FakeClient(
        {bd_url: bd_html, dvd_url: dvd_html},
        {
            bd_jacket: b"RIFF\x00\x00\x00\x00WEBPdata",
            bd_cover: b"RIFF\x00\x00\x00\x00WEBPdata",
            dvd_jacket: b"RIFF\x00\x00\x00\x00WEBPdata",
            dvd_cover: b"RIFF\x00\x00\x00\x00WEBPdata",
        },
    )
    crawler = AVEntertainmentsCrawler(cast(HttpClient, client))
    result = await crawler._scrape(dvd_url)
    assert result is not None
    assert result.number == "S2MBD-046"
    assert result.poster_urls == [bd_jacket]
    assert result.thumb_urls == [bd_cover]


@pytest.mark.asyncio
async def test_scrape_falls_back_to_complete_dvd_when_bd_images_are_invalid() -> None:
    from amane.crawlers.sites.aventertainments import AVEntertainmentsCrawler

    bd_url = "https://www.aventertainments.com/dvd/detail?pro=87393&lang=2&cat=29"
    dvd_url = "https://www.aventertainments.com/dvd/detail?pro=87392&lang=2&cat=29"
    bd_html = _version_html("S2MBD-046", "dvd1s2mbd-046")
    dvd_html = _version_html("S2M-046", "dvd1s2m-046")
    bd_jacket = "https://imgs02.aventertainments.com/new/jacket_images/dvd1s2mbd-046.webp"
    bd_cover = "https://imgs02.aventertainments.com/new/bigcover/dvd1s2mbd-046.webp"
    dvd_jacket = "https://imgs02.aventertainments.com/new/jacket_images/dvd1s2m-046.webp"
    dvd_cover = "https://imgs02.aventertainments.com/new/bigcover/dvd1s2m-046.webp"
    client = _FakeClient(
        {bd_url: bd_html, dvd_url: dvd_html},
        {
            bd_jacket: b"<html>not an image</html>",
            bd_cover: b"<html>not an image</html>",
            dvd_jacket: b"RIFF\x00\x00\x00\x00WEBPdata",
            dvd_cover: b"RIFF\x00\x00\x00\x00WEBPdata",
        },
    )
    crawler = AVEntertainmentsCrawler(cast(HttpClient, client))
    result = await crawler._scrape(dvd_url)
    assert result is not None
    assert result.number == "S2M-046"
    assert result.poster_urls == [dvd_jacket]
    assert result.thumb_urls == [dvd_cover]


@pytest.mark.asyncio
async def test_search_uses_local_pro_index_without_touching_search_page() -> None:
    """命中本地 pro 映射时直接访问详情页: 搜索页被人机验证拦死, 详情页不设防.

    ``S2M-046`` 与 ``S2MBD-046`` 是两个番号, 索引按番号精确取, 不会互相顶替.
    """
    from amane.crawlers.sites.aventertainments import AVEntertainmentsCrawler

    search_url = "https://www.aventertainments.com/dvd/search?lang=2&cat=29&keyword=S2M-046&searchby=keyword&page=1"
    bd_url = "https://www.aventertainments.com/dvd/detail?pro=87393&lang=2&cat=29"
    dvd_url = "https://www.aventertainments.com/dvd/detail?pro=87392&lang=2&cat=29"
    # 搜索页有内容也不该被访问: 命中索引就应该直奔详情页.
    search_html = (
        '<a href="/dvd/detail?pro=87392&amp;lang=2&amp;cat=29">DVD</a>'
        '<a href="/dvd/detail?pro=87393&amp;lang=2&amp;cat=29">Blu-ray</a>'
    )
    crawler = AVEntertainmentsCrawler(
        cast(
            HttpClient,
            _FakeClient(
                {search_url: search_html, bd_url: _detail_html("S2MBD-046"), dvd_url: _detail_html("S2M-046")},
                {},
            ),
        )
    )
    # 索引里 S2M-046 指向 DVD 那个 pro, 且番号校验通过.
    assert await crawler._search(SearchQuery("S2M-046")) == dvd_url
    assert crawler._search_dvd_fallback is None


@pytest.mark.asyncio
async def test_scrape_falls_back_to_dvd_when_bd_metadata_is_incomplete() -> None:
    from amane.crawlers.sites.aventertainments import AVEntertainmentsCrawler

    bd_url = "https://www.aventertainments.com/dvd/detail?pro=87393&lang=2&cat=29"
    dvd_url = "https://www.aventertainments.com/dvd/detail?pro=87392&lang=2&cat=29"
    bd_html = _version_html("S2MBD-046", "dvd1s2mbd-046").replace('"name":"Encore Vol.46"', '"name":""')
    dvd_html = _version_html("S2M-046", "dvd1s2m-046")
    bd_jacket = "https://imgs02.aventertainments.com/new/jacket_images/dvd1s2mbd-046.webp"
    bd_cover = "https://imgs02.aventertainments.com/new/bigcover/dvd1s2mbd-046.webp"
    dvd_jacket = "https://imgs02.aventertainments.com/new/jacket_images/dvd1s2m-046.webp"
    dvd_cover = "https://imgs02.aventertainments.com/new/bigcover/dvd1s2m-046.webp"
    image = b"RIFF\x00\x00\x00\x00WEBPdata"
    client = _FakeClient(
        {bd_url: bd_html, dvd_url: dvd_html},
        {bd_jacket: image, bd_cover: image, dvd_jacket: image, dvd_cover: image},
    )
    crawler = AVEntertainmentsCrawler(cast(HttpClient, client))
    result = await crawler._scrape(dvd_url)
    assert result is not None
    assert result.number == "S2M-046"
    assert result.poster_urls == [dvd_jacket]
    assert result.thumb_urls == [dvd_cover]


def test_parser_extracts_title_from_h3_when_jsonld_and_h1_are_absent() -> None:
    html = _detail_html("SMBD-06")
    html = html.replace('"name":"Encore Vol.46",', "")
    html = html.replace("<head><title>Encore Vol.46</title>", "<head><title></title>")
    html = html.replace("<body>", "<body><h3>S Model 06 : 沢木樹里 (ブルーレイ版)</h3>")
    metadata = _metadata_from_page(DETAIL_URL, html)
    assert metadata is not None
    assert metadata.title == "S Model 06 : 沢木樹里 (ブルーレイ版)"


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        # 分隔符是番号的一部分: 站点把带与不带 ``-`` 的两种写法当作不同商品,
        # 索引里各自存在且 pro 不同, 不能归一.
        ("AND-001", 114609),
        ("AND001", 9730),
        ("F-B001", 7383),
        ("FB001", 5671),
        # 只存在于一种写法时, 另一种必须查不到, 交给搜索页兜底.
        ("DR009", 80),
        ("DR-009", None),
        # 大小写不敏感: 索引键统一大写.
        ("gsdv-005", 162896),
        ("不存在的番号", None),
    ],
)
def test_lookup_pro_matches_exactly_without_normalizing(number: str, expected: int | None) -> None:
    assert lookup_pro(number) == expected
