from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from amane.crawlers.actor.sites.tushy import (
    TushyActorCrawler,
    TushyRawActorCrawler,
    _listing_webp,
    _models_from_html,
)
from amane.enums import SiteName


def _html(name: str = "Jane Doe", slug: str = "jane-doe", image: str | None = "https://cdn.test/jane_620x1060.webp?sig=x", *, page_num: int = 1, total_count: int = 1) -> str:
    model = {"name": name, "slug": slug, "images": {"listing": [{"webp": {"highdpi": {"double": image}}}]}}
    payload = {"props": {"pageProps": {"models": [model], "pageNum": page_num, "totalCount": total_count}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'


def test_parse_next_data_and_only_highdpi_double_webp() -> None:
    models = _models_from_html(_html())
    assert models[0]["name"] == "Jane Doe"
    assert _listing_webp(models[0]) == "https://cdn.test/jane_620x1060.webp?sig=x"
    assert _listing_webp(_models_from_html(_html(image="https://cdn.test/small.webp"))[0]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("crawler_type", "site", "host"),
    [(TushyActorCrawler, SiteName.TUSHY, "tushy.com"), (TushyRawActorCrawler, SiteName.TUSHY_RAW, "tushyraw.com")],
)
async def test_fetch_exact_name_without_detail_request(crawler_type, site, host) -> None:
    web = AsyncMock()
    client = AsyncMock()
    client.get_html = AsyncMock(return_value=_html())
    crawler = crawler_type(client=client)
    result = await crawler.fetch("jANE dOE")
    assert crawler.name == site
    assert result is not None
    assert result.image_urls == ["https://cdn.test/jane_620x1060.webp?sig=x"]
    assert result.source_url == f"https://www.{host}/performers/jane-doe"
    assert client.get_html.await_count == 1


@pytest.mark.asyncio
async def test_fetch_does_not_fuzzy_match() -> None:
    client = AsyncMock()
    client.get_html = AsyncMock(return_value=_html())
    crawler = TushyActorCrawler(client=client)
    assert await crawler.fetch("Jane Doe Extra") is None


@pytest.mark.asyncio
async def test_fetch_paginates_only_until_reported_last_page() -> None:
    client = AsyncMock()
    client.get_html = AsyncMock(side_effect=[_html("First", "first", total_count=2), _html("Jane Doe", "jane-doe", page_num=2, total_count=2)])
    crawler = TushyRawActorCrawler(client=client)
    result = await crawler.fetch("Jane Doe")
    assert result is not None
    assert result.source_url == "https://www.tushyraw.com/performers/jane-doe"
    assert client.get_html.await_count == 2


@pytest.mark.asyncio
async def test_fetch_stops_when_pagination_metadata_is_missing() -> None:
    client = AsyncMock()
    model = {"name": "Other", "slug": "other", "images": {"listing": []}}
    payload = {"props": {"pageProps": {"models": [model]}}}
    client.get_html = AsyncMock(return_value=f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>')
    crawler = TushyActorCrawler(client=client)
    assert await crawler.fetch("Jane Doe") is None
    assert client.get_html.await_count == 1
