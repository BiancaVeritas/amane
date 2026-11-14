"""通用页面解析: 站点没有专属爬虫时靠页面特征认出番号与标题."""

from __future__ import annotations

import pytest
from parsel import Selector

from amane.crawlers.sites.generic_page import GenericPageCrawler, _pick_number


class _FakeClient:
    def __init__(self, html: str) -> None:
        self._html = html
        self.requests: list[str] = []

    async def get_html(self, url: str, **kwargs: object) -> str:
        del kwargs
        self.requests.append(url)
        return self._html


def test_number_comes_from_url_tail_and_keeps_format() -> None:
    """地址末段是番号时直接用它, 且保留原始写法.

    按捕获组重拼会把 ``020616_348`` 变成 ``020616348``, 那已不是番号:
    类型判定与别的来源都认不出来.
    """
    assert _pick_number(Selector(text="<html></html>"), "https://x.tv/movies/020616_348/") == "020616_348"


def test_number_falls_back_to_page_when_url_has_none() -> None:
    """地址里没有番号时从页面正文里挑."""
    html = "<html><body><p>品番: ABC-123</p><p>ABC-123</p></body></html>"
    assert _pick_number(Selector(text=html), "https://x.tv/v/watch") == "ABC-123"


@pytest.mark.asyncio
async def test_scrape_reads_title_and_poster() -> None:
    html = """
    <html><head>
      <meta property="og:title" content="【素人】テスト作品" />
      <meta property="og:image" content="https://x.tv/p.jpg" />
    </head><body></body></html>
    """
    client = _FakeClient(html)
    crawler = GenericPageCrawler(client)  # type: ignore[arg-type]

    meta = await crawler._scrape("https://www.muramura.tv/movies/020616_348/")

    assert meta is not None
    assert meta.number == "020616_348"
    assert meta.title == "【素人】テスト作品"
    assert meta.poster_urls == ["https://x.tv/p.jpg"]
    assert meta.source_url == "https://www.muramura.tv/movies/020616_348/"


@pytest.mark.asyncio
async def test_scrape_returns_none_without_any_signal() -> None:
    """既没有番号也没有标题时不当成命中, 免得凭空造一条空记录."""
    crawler = GenericPageCrawler(_FakeClient("<html></html>"))  # type: ignore[arg-type]
    assert await crawler._scrape("https://x.tv/v/watch") is None


@pytest.mark.asyncio
async def test_generic_crawler_never_searches() -> None:
    """通用爬虫不做站内搜索: 它只在用户给定地址时被调用."""
    crawler = GenericPageCrawler(_FakeClient(""))  # type: ignore[arg-type]
    assert await crawler._search(object()) is None  # type: ignore[arg-type]
