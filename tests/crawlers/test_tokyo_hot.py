from __future__ import annotations

import pytest

from amane.crawlers import registry
from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.tokyo_hot import (
    TokyoHotCrawler,
    _normalize_number,
    _parse_runtime,
    _search_result,
)
from amane.enums import SiteName


SEARCH_HTML = '''
<ul class="list slider cf">
  <li class="detail">
    <a href="/product/5770/" class="rm">
      <img src="/thumb/220x124_default.jpg" alt="n0956" title="n0956">
      <div class="description2"><div class="actor">(作品番号: n0956)</div></div>
    </a>
  </li>
  <li class="detail"><a href="/product/9999/" class="rm"><div class="actor">(作品番号: N9999)</div></a></li>
</ul>
'''

DETAIL_HTML = '''
<html><head><meta property="og:title" content="問答無用カン 吉永小夜 | Tokyo-Hot 東京熱"></head>
<body>
  <h2>問答無用カン 吉永小夜</h2>
  <div class="sentence">日本語の作品紹介<br>続きの説明</div>
  <div class="infowrapper"><dl class="info">
    <dt>出演者</dt><dd><a>吉永小夜</a></dd>
    <dt>プレイ内容</dt><dd><a>中出し</a><a>フェラ</a></dd>
    <dt>タグ</dt><dd><a>中出し</a><a>緊縛</a></dd>
    <dt>シリーズ</dt><dd><a>問答無用姦</a><a>AIリマスター</a></dd>
    <dt>レーベル</dt><dd><a>東熱</a></dd>
    <dt>配信開始日</dt><dd>2014/06/06</dd>
    <dt>収録時間</dt><dd>01:49:13</dd>
    <dt>作品番号</dt><dd>n0956</dd>
  </dl></div>
  <ul class="control"><li class="package">
    <a href="https://my.cdn.tokyo-hot.com/media/5770/jacket/n0956.jpg">Jacket</a>
  </li></ul>
  <div class="scap">
    <a href="https://my.cdn.tokyo-hot.com/media/5770/scap/0sample_001/640x480_wlimited.jpg">
      <img src="/thumb/150x150_default.jpg">
    </a>
    <a href="https://my.cdn.tokyo-hot.com/media/5770/vcap/151/640x480_wlimited.jpg">
      <img src="/thumb/120x120_default.jpg">
    </a>
  </div>
  <div class="sample-player-shell" data-mp4-src="https://my.cdn.tokyo-hot.com/media/samples/5770.mp4"></div>
  <div class="recommend"><dt>シリーズ</dt><dd><a>DO NOT INCLUDE</a></dd></div>
</body></html>
'''


class _FakeClient:
    def __init__(self) -> None:
        self.urls: list[str] = []

    async def get_html(self, url: str, **kwargs: object) -> str:
        self.urls.append(url)
        if "/product/?q=" in url:
            return SEARCH_HTML
        return DETAIL_HTML


def test_tokyo_hot_registered() -> None:
    assert registry.get(SiteName.TOKYO_HOT) is TokyoHotCrawler


def test_number_normalization_and_runtime() -> None:
    assert _normalize_number("n0956") == "N0956"
    assert _normalize_number("N1020") == "N1020"
    assert _normalize_number("N-1020") is None
    assert _normalize_number("5770") is None
    assert _parse_runtime("01:49:13") == 109
    assert _parse_runtime("unknown") is None


@pytest.mark.asyncio
async def test_search_uses_number_result_not_internal_product_id() -> None:
    client = _FakeClient()
    crawler = TokyoHotCrawler(client)  # type: ignore[arg-type]
    url = await crawler._search(SearchQuery(number="n0956"))
    assert url == "https://my.tokyo-hot.com/product/5770/?lang=ja"
    assert client.urls == ["https://my.tokyo-hot.com/product/?q=n0956&lang=ja"]
    assert await crawler._search(SearchQuery(number="5770")) is None


@pytest.mark.asyncio
async def test_scrape_maps_multi_series_and_full_size_assets() -> None:
    client = _FakeClient()
    crawler = TokyoHotCrawler(client)  # type: ignore[arg-type]
    result = await crawler._scrape("https://my.tokyo-hot.com/product/5770/?lang=ja")
    assert result is not None
    assert result.number == "N0956"
    assert result.external_id is None
    assert result.title == "問答無用カン 吉永小夜"
    assert result.plot == "日本語の作品紹介 続きの説明"
    assert [actor.name for actor in result.actors] == ["吉永小夜"]
    assert result.studio == "東京熱"
    assert result.publisher == "Tokyo-Hot"
    assert result.release == "2014-06-06"
    assert result.runtime == 109
    assert result.tags == ["中出し", "フェラ", "緊縛"]
    assert result.series_names == ["問答無用姦", "AIリマスター", "東熱"]
    assert result.series == "問答無用姦"
    assert result.poster_urls == ["https://my.cdn.tokyo-hot.com/media/5770/jacket/n0956.jpg"]
    assert result.original_poster_urls == result.poster_urls
    # jacket 是横版封套图, 既当封面也当海报源 —— 海报交给物化阶段按人脸裁.
    assert result.thumb_urls == ["https://my.cdn.tokyo-hot.com/media/5770/jacket/n0956.jpg"]
    assert result.extrafanart == [
        "https://my.cdn.tokyo-hot.com/media/5770/scap/0sample_001/640x480_wlimited.jpg",
        "https://my.cdn.tokyo-hot.com/media/5770/vcap/151/640x480_wlimited.jpg",
    ]
    assert result.trailer_urls == ["https://my.cdn.tokyo-hot.com/media/samples/5770.mp4"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 站上只有 k / n 两个系列, 两种都要能进搜索.
        ("k0794", "K0794"),
        ("K0794", "K0794"),
        ("n1138", "N1138"),
        ("n0687", "N0687"),
        # 其它字母、字母不在第一位、缺数字: 都不是本站番号.
        ("a1234", None),
        ("e0001", None),
        ("xk0794", None),
        ("kn0794", None),
        ("0794", None),
        ("k", None),
        ("k-0794", None),
        ("", None),
    ],
)
def test_normalize_number_accepts_only_k_and_n(raw: str, expected: str | None) -> None:
    """番号前缀只有 k / n 且必须在第一位; 放宽成任意字母会让别家的号混进搜索."""
    assert _normalize_number(raw) == expected


def test_search_result_returns_listing_thumb() -> None:
    """搜索页的列表缩略图要能取出: 详情页两处封面都缺时靠它兜底."""
    assert _search_result(SEARCH_HTML, "N0956", "https://my.tokyo-hot.com/product/") == (
        "https://my.tokyo-hot.com/product/5770/?lang=ja",
        "https://my.tokyo-hot.com/thumb/220x124_default.jpg",
    )


def test_search_result_has_no_thumb_for_other_number() -> None:
    assert _search_result(SEARCH_HTML, "K9999", "https://my.tokyo-hot.com/product/") == (None, None)
