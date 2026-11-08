from __future__ import annotations

import pytest

from amane.crawlers import registry
from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.tokyo_hot import TokyoHotCrawler, _normalize_number, _parse_runtime
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
    assert result.thumb_urls == []
    assert result.extrafanart == [
        "https://my.cdn.tokyo-hot.com/media/5770/scap/0sample_001/640x480_wlimited.jpg",
        "https://my.cdn.tokyo-hot.com/media/5770/vcap/151/640x480_wlimited.jpg",
    ]
    assert result.trailer_urls == ["https://my.cdn.tokyo-hot.com/media/samples/5770.mp4"]
