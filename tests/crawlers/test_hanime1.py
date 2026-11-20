from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest

from amane.crawlers.http import HttpClient
from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.hanime1 import Hanime1Crawler, clean_search_title


@pytest.fixture
def web_client():
    return AsyncMock()


@pytest.fixture
def crawler(web_client):
    return Hanime1Crawler(client=HttpClient(web=web_client))


# ---------------------------------------------------------
# 标题清理
# ---------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("[字幕组] 作品名称 (1080P)", "作品名称"),
        ("作品名称-1080PDVDRIP", "作品名称"),
        ("作品名称-DVDRIP1080P", "作品名称"),
        ("作品名称-1080PX", "作品名称X"),
        ("作品名称-1080P特别版", "作品名称特别版"),
        ("作品名称-720P-特别版", "作品名称-特别版"),
        ("作品名称-4K修正版", "作品名称修正版"),
        ("作品名称-BDRIP", "作品名称"),
        ("作品名称-dvdrip", "作品名称"),
        ("作品名称-有码", "作品名称"),
        ("作品名称-无码", "作品名称"),
        ("作品名称有码", "作品名称有码"),
        ("作品名称无码", "作品名称无码"),
        ("作品名称-C", "作品名称"),
        ("作品名称-c", "作品名称"),
        ("作品名称C", "作品名称C"),
        ("作品名称ABC", "作品名称ABC"),
        ("ABC-DEF", "ABC-DEF"),
    ],
)
def test_clean_search_title(source, expected):
    assert clean_search_title(source) == expected


# ---------------------------------------------------------
# 搜索
# ---------------------------------------------------------


@pytest.mark.asyncio
async def test_search_uses_local_filename(crawler, web_client):
    web_client.get_text.return_value = """
    <div class="video-item-container">
        <a class="video-link" href="/advertisement"></a>
    </div>
    <div class="video-item-container">
        <a class="video-link" href="/watch?v=407947"></a>
    </div>
    """

    query = SearchQuery(
        number="ORIGINAL-001",
        hanime1_file_path="/media/[字幕组] 测试作品 (1080P).mp4",
    )

    url = await crawler._search(query)

    assert url == "https://hanime1.me/watch?v=407947"
    web_client.get_text.assert_awaited_once()

    requested_url = web_client.get_text.await_args.args[0]
    keyword = parse_qs(urlparse(requested_url).query)["query"][0]

    assert keyword == "测试作品"
    assert "ORIGINAL-001" not in keyword


@pytest.mark.asyncio
async def test_search_skips_without_local_file(crawler, web_client):
    query = SearchQuery(number="ORIGINAL-001")

    result = await crawler._search(query)

    assert result is None
    web_client.get_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_ignores_non_watch_links(crawler, web_client):
    web_client.get_text.return_value = """
    <div class="video-item-container">
        <a class="video-link" href="/advertisement"></a>
        <a class="video-link" href="/search?query=test"></a>
        <a class="video-link" href="/watch"></a>
    </div>
    """

    query = SearchQuery(
        number="ORIGINAL-001",
        hanime1_file_path="/media/测试作品.mp4",
    )

    result = await crawler._search(query)

    assert result is None


# ---------------------------------------------------------
# 详情页 TAG 抓取
# ---------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_only_extracts_tags(crawler, web_client):
    web_client.get_text.side_effect = [
        """
        <div class="video-item-container">
            <a class="video-link" href="/watch?v=407947"></a>
        </div>
        """,
        """
        <div class="video-tags-wrapper">
            <div class="single-video-tag">
                <a>标签一<span>123</span></a>
            </div>
            <div class="single-video-tag">
                <a>标签二<span>45</span></a>
            </div>
            <div class="single-video-tag">
                <a>add</a>
            </div>
            <div class="single-video-tag">
                <a>remove</a>
            </div>
        </div>
        """,
    ]

    query = SearchQuery(
        number="ORIGINAL-001",
        hanime1_file_path="/media/测试作品.mp4",
    )

    result = await crawler.fetch(query)

    assert result is not None
    assert result.number == "ORIGINAL-001"
    assert result.tags == ["标签一", "标签二"]
    assert result.source_url == "https://hanime1.me/watch?v=407947"
    assert result.title is None
    assert result.plot is None
    assert result.actors == []
    assert result.poster_urls == []
    assert web_client.get_text.await_count == 2


@pytest.mark.asyncio
async def test_fetch_skips_without_local_file(crawler, web_client):
    result = await crawler.fetch(SearchQuery(number="ORIGINAL-001"))

    assert result is None
    web_client.get_text.assert_not_awaited()
