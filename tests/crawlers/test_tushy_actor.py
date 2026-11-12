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


def _html(*models: dict) -> str:
    payload = {"props": {"pageProps": {"videos": [{"title": "unrelated"}], "models": list(models)}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'


def _model(name="Jane Doe", slug="jane-doe", image="https://cdn.test/jane_620x1060.webp?sig=x"):
    listing = []
    if image:
        listing = [{"src": image, "webp": {"highdpi": {"double": image}}}]
    return {"name": name, "slug": slug, "images": {"listing": listing}}


def test_parse_next_data_and_only_highdpi_double_webp() -> None:
    models = _models_from_html(_html(_model()))
    assert models[0]["name"] == "Jane Doe"
    assert _listing_webp(models[0]) == "https://cdn.test/jane_620x1060.webp?sig=x"
    assert _listing_webp(_models_from_html(_html(_model(image="https://cdn.test/small.webp")))[0]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("crawler_type", "site", "host"),
    [(TushyActorCrawler, SiteName.TUSHY, "tushy.com"), (TushyRawActorCrawler, SiteName.TUSHY_RAW, "tushyraw.com")],
)
async def test_fetch_searches_once_and_exact_matches_without_detail_request(crawler_type, site, host) -> None:
    client = AsyncMock()
    client.get_html = AsyncMock(return_value=_html(_model()))
    crawler = crawler_type(client=client)
    result = await crawler.fetch("jANE dOE")
    assert crawler.name == site
    assert result is not None
    assert result.image_urls == ["https://cdn.test/jane_620x1060.webp?sig=x"]
    assert result.source_url == f"https://www.{host}/performers/jane-doe"
    assert client.get_html.await_count == 1
    assert "/search?q=jANE" in client.get_html.await_args.args[0]


@pytest.mark.asyncio
async def test_fetch_exactly_matches_among_fuzzy_search_results() -> None:
    client = AsyncMock()
    client.get_html = AsyncMock(
        return_value=_html(
            _model("Selva Lapiedra", "selva-lapiedra", "https://cdn.test/female-fallback-portrait.svg"),
            _model("Natasha Lapiedra", "natasha-lapiedra"),
            _model("Ambar Lapiedra", "ambar-lapiedra"),
        )
    )
    crawler = TushyActorCrawler(client=client)
    result = await crawler.fetch("Selva Lapiedra")
    assert result is not None
    assert result.name == "Selva Lapiedra"
    assert result.image_urls == []
    assert result.source_url is not None
    assert result.source_url.endswith("/performers/selva-lapiedra")
    assert client.get_html.await_count == 1


@pytest.mark.asyncio
async def test_fetch_does_not_fuzzy_match() -> None:
    client = AsyncMock()
    client.get_html = AsyncMock(return_value=_html(_model()))
    crawler = TushyActorCrawler(client=client)
    assert await crawler.fetch("Jane Doe Extra") is None
    assert client.get_html.await_count == 1


@pytest.mark.asyncio
async def test_fetch_keeps_actor_hit_when_only_fallback_image_exists() -> None:
    client = AsyncMock()
    client.get_html = AsyncMock(return_value=_html(_model(image="https://cdn.test/female-fallback-portrait.svg")))
    crawler = TushyRawActorCrawler(client=client)
    result = await crawler.fetch("Jane Doe")
    assert result is not None
    assert result.image_urls == []
