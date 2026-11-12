"""JavLibrary 唯一命中 302 直达详情页时校验番号."""

from unittest.mock import AsyncMock

import pytest

from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.javlibrary import JavLibraryCrawler


@pytest.mark.asyncio
async def test_search_accepts_matching_canonical_detail() -> None:
    html = """<html><head><link rel="canonical" href="/cn/?v=javliabc123"></head>
    <body><div id="video_id"><table><tr><td class="text">ABC-123</td></tr></table></div></body></html>"""
    client = AsyncMock()
    client.get_html.return_value = html
    crawler = JavLibraryCrawler(client=client)
    result = await crawler._search(SearchQuery("ABC-123"))
    assert result == "https://www.javlibrary.com/cn/?v=javliabc123"


@pytest.mark.asyncio
async def test_search_rejects_mismatched_redirect_detail() -> None:
    html = """<html><head><link rel="canonical" href="/cn/?v=javliwrong"></head>
    <body><div id="video_id"><table><tr><td class="text">ABC-124</td></tr></table></div></body></html>"""
    client = AsyncMock()
    client.get_html.return_value = html
    crawler = JavLibraryCrawler(client=client)
    assert await crawler._search(SearchQuery("ABC-123")) is None
