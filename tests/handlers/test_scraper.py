"""测试 amane.pipeline - ScrapeHandler 和 RefreshHandler"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar

import pytest
from pydantic import ValidationError

from amane.aggregate import AggregatedMetadata
from amane.config import HotSettings, ScrapingConfig
from amane.crawlers.base import Crawler, CrawlerProfile
from amane.crawlers.models import MediaMetadata
from amane.db.models import MediaFileStatus, Task, TaskStatus, TaskType
from amane.enums import MetadataField, SiteName
from amane.handlers import (
    CacheKind,
    RefreshHandler,
    RefreshPayload,
    ScanMode,
    ScrapeHandler,
    ScrapePayload,
)
from amane.handlers.scrape import _prepare_image_content_type, _searchable_title
from amane.library import CleanupInventory, InventorySource, InventoryStore, LibraryFileKind, LibraryHit, scan_inventory
from amane.observability import Recorder
from amane.parsing import ContentType
from amane.plugins.models import SourceDescriptor

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from amane.db.repository import Repository

# --- 辅助类 ---


class MockCrawler:
    name = SiteName.JAVDB

    def __init__(self, client=None):
        self.client = client

    async def fetch(self, query, options=None) -> MediaMetadata | None:
        return MediaMetadata.model_validate(
            {
                "number": "MIDV-123",
                "title": "Mock Title",
                "actors": ["Actor A"],
                "tags": ["Drama"],
                "studio": "MockStudio",
            }
        )


class WesternMockCrawler(MockCrawler):
    name = SiteName.THEPORNDB

    async def fetch(self, query, options=None) -> MediaMetadata | None:
        return MediaMetadata.model_validate(
            {
                "number": query.number,
                "title": "Mock Western Title",
                "actors": ["Actor A"],
                "tags": ["Drama"],
                "studio": "MockStudio",
                "external_id": "scene-test-1",
            }
        )


class EmptySearchCrawler:
    """返回空搜索结果的爬虫"""

    name = SiteName.FC2

    def __init__(self, client=None):
        self.client = client

    async def fetch(self, query, options=None) -> MediaMetadata | None:
        return None


class FakeFactory:
    """伪造的 CrawlerFactory, 返回预配置的爬虫实例"""

    def __init__(self, crawlers: dict, generic: object | None = None):
        self._crawlers = crawlers
        self._generic = generic

    async def get(self, name: str):
        return self._crawlers.get(name)

    async def get_generic(self):
        """通用解析器: 站点没有专属爬虫时用它兜底."""
        if self._generic is None:
            raise AssertionError("这个用例不该走到通用解析")
        return self._generic

    async def get_crawlers(self, names: Iterable[str]) -> dict:
        result = {}
        for name in names:
            crawler = await self.get(name)
            if crawler is not None:
                result[name] = crawler
        return result


@pytest.fixture
def factory():
    return FakeFactory({"javdb": MockCrawler()})


@pytest.fixture
def handler(repo, factory, resource_store):
    return ScrapeHandler(repo=repo, factory=factory, resource_store=resource_store, pipeline_config=HotSettings())


# --- ScrapeHandler 测试 ---


class TestSearchableTitle:
    """标题拿去搜别的站之前去掉标点: 站点装饰符会让搜索词一个都匹配不上."""

    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("ABC-123: Test Title (Uncensored)", "ABC123 Test Title Uncensored"),
            ("【完全素人65】マリナVSヒカリ 史上最大の決戦！！", "完全素人65マリナVSヒカリ 史上最大の決戦"),
            ("タイトル・テスト／続編", "タイトルテスト続編"),
            ("  多  空格  ", "多 空格"),
            ("！！！", None),
            ("", None),
            (None, None),
        ],
    )
    def test_strips_punctuation(self, title, expected):
        assert _searchable_title(title) == expected


class TestPreviewByUrl:
    """手动搜索粘整条地址: 打开页面读出番号, 再按番号问各来源.

    地址里往往只有站点内部 ID (javdb 的 ``/v/dgVq9``), 当番号去搜哪个来源都搜不到.
    """

    class _UrlCrawler(Crawler):
        """能按地址解析的来源; 记录被打开过的地址与被问过的番号."""

        scraped_urls: ClassVar[list[str]] = []
        fetched_numbers: ClassVar[list[str]] = []

        @classmethod
        def profile(cls) -> CrawlerProfile:
            return CrawlerProfile(name="javdb", base_url="https://javdb580.com")

        async def _search(self, query, options=None) -> str | None:
            self.fetched_numbers.append(query.number)
            return "https://javdb580.com/v/by-search"

        async def _scrape(self, url, options=None) -> MediaMetadata | None:
            self.scraped_urls.append(url)
            return MediaMetadata.model_validate({"number": "MIDV-123", "title": "From page"})

    @pytest.fixture(autouse=True)
    def _reset(self):
        self._UrlCrawler.scraped_urls = []
        self._UrlCrawler.fetched_numbers = []
        yield

    @pytest.mark.asyncio(loop_scope="function")
    async def test_url_is_opened_and_number_reused(self, repo: Repository, resource_store):
        h = ScrapeHandler(
            repo=repo,
            factory=FakeFactory({"javdb": self._UrlCrawler(client=None)}),
            resource_store=resource_store,
            pipeline_config=HotSettings(),
        )

        items = await h.preview("https://javdb580.com/v/dgVq9", ContentType.CENSORED)

        # 地址被真正打开过, 而且是原样打开 —— 不是截末段当番号.
        assert self._UrlCrawler.scraped_urls[0] == "https://javdb580.com/v/dgVq9"
        # 读出番号后按番号走正常流程.
        assert "MIDV-123" in self._UrlCrawler.fetched_numbers
        assert items[0]["site"] == "javdb"
        assert items[0]["number"] == "MIDV-123"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_url_host_mismatch_is_not_scraped(self, repo: Repository, resource_store):
        """地址不属于任何已配置来源时不乱抓: 免得拿别站页面套自家解析."""
        h = ScrapeHandler(
            repo=repo,
            factory=FakeFactory({"javdb": self._UrlCrawler(client=None)}),
            resource_store=resource_store,
            pipeline_config=HotSettings(),
        )

        items = await h.preview("https://example.com/v/xyz", ContentType.CENSORED)

        assert items == []
        assert self._UrlCrawler.scraped_urls == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_plain_number_still_searches(self, repo: Repository, resource_store):
        h = ScrapeHandler(
            repo=repo,
            factory=FakeFactory({"javdb": self._UrlCrawler(client=None)}),
            resource_store=resource_store,
            pipeline_config=HotSettings(),
        )

        await h.preview("MIDV-123", ContentType.CENSORED)

        # 纯番号走搜索: fetch 内部会先 _search 再 _scrape 那个搜索结果页.
        assert self._UrlCrawler.fetched_numbers == ["MIDV-123"]
        assert self._UrlCrawler.scraped_urls != ["https://javdb580.com/v/dgVq9"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_url_falls_back_to_cleaned_title_search(self, repo: Repository, resource_store):
        """地址解析出的条目没有番号时, 用去标点的标题继续搜当前类型的其余来源."""

        class TitleOnlyCrawler(Crawler):
            @classmethod
            def profile(cls) -> CrawlerProfile:
                return CrawlerProfile(name="javdb", base_url="https://javdb580.com")

            async def _search(self, query, options=None) -> str | None:
                return "https://javdb580.com/v/x"

            async def _scrape(self, url, options=None) -> MediaMetadata | None:
                # 有标题但没番号: 站点页面只给了标题.
                return MediaMetadata.model_validate(
                    {"number": "", "title": "【完全素人65】マリナVSヒカリ 史上最大の決戦！！"}
                )

        asked: list[str] = []

        class Searcher(Crawler):
            @classmethod
            def profile(cls) -> CrawlerProfile:
                return CrawlerProfile(name="javbus", base_url="https://www.javbus.com")

            async def _search(self, query, options=None) -> str | None:
                asked.append(query.number)
                return None

            async def _scrape(self, url, options=None) -> MediaMetadata | None:
                return None

        h = ScrapeHandler(
            repo=repo,
            factory=FakeFactory({"javdb": TitleOnlyCrawler(client=None), "javbus": Searcher(client=None)}),
            resource_store=resource_store,
            pipeline_config=_config_with({ContentType.CENSORED: [SiteName.JAVDB, SiteName.JAVBUS]}),
        )

        await h.preview("https://javdb580.com/v/abc", ContentType.CENSORED)

        # 用去标点后的标题去问过别家.
        assert "完全素人65マリナVSヒカリ 史上最大の決戦" in asked


class TestAppendSingleSource:
    """「添加来源链接」只刮用户贴的那一条, 不把全部来源重新问一遍."""

    class _OneSourceCrawler(Crawler):
        """只在这一家里能解析地址; 记下被问过的站点, 用来证明没有全量重刮."""

        asked: ClassVar[list[str]] = []

        @classmethod
        def profile(cls) -> CrawlerProfile:
            return CrawlerProfile(name="javdb", base_url="https://javdb580.com")

        async def _search(self, query, options=None) -> str | None:
            self.asked.append("search")
            return "https://javdb580.com/v/other"

        async def _scrape(self, url, options=None) -> MediaMetadata | None:
            self.asked.append(f"scrape:{url}")
            return MediaMetadata.model_validate({"number": "MIDV-123", "title": "From pasted url", "source_url": url})

    class _OtherCrawler(Crawler):
        """路由里的另一家: 单源模式下不该被问到."""

        touched: ClassVar[list[str]] = []

        @classmethod
        def profile(cls) -> CrawlerProfile:
            return CrawlerProfile(name="javbus", base_url="https://www.javbus.com")

        async def _search(self, query, options=None) -> str | None:
            self.touched.append("search")
            return None

        async def _scrape(self, url, options=None) -> MediaMetadata | None:
            self.touched.append("scrape")
            return None

    @pytest.fixture(autouse=True)
    def _reset(self):
        self._OneSourceCrawler.asked = []
        self._OtherCrawler.touched = []
        yield

    @pytest.mark.asyncio(loop_scope="function")
    async def test_only_pasted_url_is_scraped(self, repo: Repository, resource_store):
        seeded = await repo.upsert_metadata(
            number="MIDV-123",
            title="Keep me",
            source_urls={"javbus": "https://www.javbus.com/MIDV-123"},
        )
        assert seeded.id is not None

        h = ScrapeHandler(
            repo=repo,
            factory=FakeFactory(
                {"javdb": self._OneSourceCrawler(client=None), "javbus": self._OtherCrawler(client=None)}
            ),
            resource_store=resource_store,
            pipeline_config=_config_with({ContentType.CENSORED: [SiteName.JAVDB, SiteName.JAVBUS]}),
        )

        result = await h.handle(
            ScrapePayload(
                number="MIDV-123",
                content_type=ContentType.CENSORED,
                metadata_id=seeded.id,
                manual=True,
                append_sources=True,
                source_url="https://javdb580.com/v/dgVq9",
                use_cache=[],
            )
        )

        assert result.success is True
        # 只打开了那一个地址, 没有按番号去搜, 另一家完全没动.
        assert self._OneSourceCrawler.asked == ["scrape:https://javdb580.com/v/dgVq9"]
        assert self._OtherCrawler.touched == []

        updated = await repo.get_metadata_by_number("MIDV-123")
        assert updated is not None
        # 字段保持原样, 只多了一条来源.
        assert updated.title == "Keep me"
        assert next(iter(updated.source_urls)) == "javbus"
        assert updated.source_urls["javdb"] == "https://javdb580.com/v/dgVq9"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_unreachable_url_leaves_record_untouched(self, repo: Repository, resource_store):
        seeded = await repo.upsert_metadata(
            number="MIDV-123",
            title="Keep me",
            source_urls={"javbus": "https://www.javbus.com/MIDV-123"},
        )
        assert seeded.id is not None

        h = ScrapeHandler(
            repo=repo,
            factory=FakeFactory({"javdb": self._OneSourceCrawler(client=None)}),
            resource_store=resource_store,
            pipeline_config=_config_with({ContentType.CENSORED: [SiteName.JAVDB]}),
        )

        result = await h.handle(
            ScrapePayload(
                number="MIDV-123",
                content_type=ContentType.CENSORED,
                metadata_id=seeded.id,
                manual=True,
                append_sources=True,
                source_url="https://example.com/not-my-site",
                use_cache=[],
            )
        )

        assert result.success is False
        updated = await repo.get_metadata_by_number("MIDV-123")
        assert updated is not None
        assert updated.title == "Keep me"
        assert next(iter(updated.source_urls)) == "javbus"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_append_keeps_other_sources_snapshots(self, repo: Repository, resource_store):
        """补链接不能清掉别家的 raw 快照.

        「对比来源」页读的就是 raw: 整片覆盖会让它只剩这一次抓的那家, 其余来源与字段归属一起消失.
        """
        seeded = await repo.upsert_metadata(
            number="MIDV-123",
            title="Keep me",
            source_urls={"javbus": "https://www.javbus.com/MIDV-123"},
            raw={
                "javbus": {"number": "MIDV-123", "title": "Keep me"},
                "dmm": {"number": "MIDV-123", "title": "From DMM"},
            },
            field_sources={"title": "javbus"},
        )
        assert seeded.id is not None

        h = ScrapeHandler(
            repo=repo,
            factory=FakeFactory({"javdb": self._OneSourceCrawler(client=None)}),
            resource_store=resource_store,
            pipeline_config=_config_with({ContentType.CENSORED: [SiteName.JAVDB]}),
        )

        await h.handle(
            ScrapePayload(
                number="MIDV-123",
                content_type=ContentType.CENSORED,
                metadata_id=seeded.id,
                manual=True,
                append_sources=True,
                source_url="https://javdb580.com/v/dgVq9",
                use_cache=[],
            )
        )

        updated = await repo.get_metadata_by_number("MIDV-123")
        assert updated is not None
        # 旧快照还在, 新的那家并进去.
        assert set(updated.raw) == {"javbus", "dmm", "javdb"}
        assert updated.raw["dmm"]["title"] == "From DMM"
        # 字段归属不被这次刮削改写.
        assert updated.field_sources["title"] == "javbus"
        assert updated.title == "Keep me"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_append_works_for_source_outside_route(self, repo: Repository, resource_store):
        """路由里没有那一家时也要能解析: 用户贴的链接就是权威.

        FC2 的类型路由里没有 javdb, 但用户贴的正是 javdb 地址 —— 被路由挡住只会报
        「无法从该地址取到元数据」, 而 javdb 明明能解析这条链接.
        """
        seeded = await repo.upsert_metadata(
            number="FC2-1229755",
            title="Keep me",
            source_urls={"freejavbt": "https://freejavbt.com/x"},
        )
        assert seeded.id is not None

        h = ScrapeHandler(
            repo=repo,
            factory=FakeFactory(
                {
                    # 路由里只有 freejavbt; javdb 在路由外.
                    "freejavbt": self._OtherCrawler(client=None),
                    "javdb": self._OneSourceCrawler(client=None),
                }
            ),
            resource_store=resource_store,
            # FC2 路由不含 javdb.
            pipeline_config=HotSettings(
                scraping=ScrapingConfig(
                    content_routes={ContentType.FC2: [SiteName.FREEJAVBT]},
                    field_priority={},
                    field_blacklist={},
                )
            ),
        )

        result = await h.handle(
            ScrapePayload(
                number="FC2-1229755",
                content_type=ContentType.FC2,
                metadata_id=seeded.id,
                manual=True,
                append_sources=True,
                source_url="https://javdb580.com/v/dgVq9",
                use_cache=[],
            )
        )

        assert result.success is True
        # 确实由路由外的那一家打开了地址.
        assert self._OneSourceCrawler.asked == ["scrape:https://javdb580.com/v/dgVq9"]
        updated = await repo.get_metadata_by_number("FC2-1229755")
        assert updated is not None
        assert updated.source_urls["javdb"] == "https://javdb580.com/v/dgVq9"
        assert updated.title == "Keep me"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_route_union_finds_site_configured_elsewhere(self, repo: Repository, resource_store):
        """第二级: 别的类型路由里配置过的站也能解析.

        javdb 配在有码路由里, FC2 路由没有它; 添加来源链接贴的是 javdb 地址时,
        应当按「全部路由并集」找到它, 而不是靠最后的兜底.
        """
        seeded = await repo.upsert_metadata(
            number="FC2-1229755",
            title="Keep me",
            source_urls={"freejavbt": "https://freejavbt.com/x"},
        )
        assert seeded.id is not None

        h = ScrapeHandler(
            repo=repo,
            factory=FakeFactory(
                {
                    "freejavbt": self._OtherCrawler(client=None),
                    "javdb": self._OneSourceCrawler(client=None),
                }
            ),
            resource_store=resource_store,
            # javdb 只在有码路由里; FC2 路由没有它.
            pipeline_config=HotSettings(
                scraping=ScrapingConfig(
                    content_routes={
                        ContentType.FC2: [SiteName.FREEJAVBT],
                        ContentType.CENSORED: [SiteName.JAVDB],
                    },
                    field_priority={},
                    field_blacklist={},
                )
            ),
        )

        result = await h.handle(
            ScrapePayload(
                number="FC2-1229755",
                content_type=ContentType.FC2,
                metadata_id=seeded.id,
                manual=True,
                append_sources=True,
                source_url="https://javdb580.com/v/dgVq9",
                use_cache=[],
            )
        )

        assert result.success is True
        assert self._OneSourceCrawler.asked == ["scrape:https://javdb580.com/v/dgVq9"]
        updated = await repo.get_metadata_by_number("FC2-1229755")
        assert updated is not None
        assert updated.source_urls["javdb"] == "https://javdb580.com/v/dgVq9"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_lookup_stops_before_fallback_pool(self, repo: Repository, resource_store):
        """命中后不再拉「全部影片来源」那批.

        最后一层会把所有内置影片站都建起来; 只有前面几级都找不到域名时才会走到那里.
        """
        requested: list[list[str]] = []

        class RecordingFactory(FakeFactory):
            async def get_crawlers(self, names):
                requested.append(sorted(str(n) for n in names))
                return await super().get_crawlers([str(n) for n in names])

        seeded = await repo.upsert_metadata(number="FC2-1229755", title="Keep me")
        assert seeded.id is not None

        h = ScrapeHandler(
            repo=repo,
            factory=RecordingFactory(
                {
                    "freejavbt": self._OtherCrawler(client=None),
                    "javdb": self._OneSourceCrawler(client=None),
                }
            ),
            resource_store=resource_store,
            pipeline_config=HotSettings(
                scraping=ScrapingConfig(
                    content_routes={
                        ContentType.FC2: [SiteName.FREEJAVBT],
                        ContentType.CENSORED: [SiteName.JAVDB],
                    },
                    field_priority={},
                    field_blacklist={},
                )
            ),
        )

        await h.handle(
            ScrapePayload(
                number="FC2-1229755",
                content_type=ContentType.FC2,
                metadata_id=seeded.id,
                manual=True,
                append_sources=True,
                source_url="https://javdb580.com/v/dgVq9",
                use_cache=[],
            )
        )

        # 第一级是当前类型路由, 之后在路由并集里命中; 没有再拉全部内置影片来源那一批.
        assert requested[0] == [str(SiteName.FREEJAVBT)]
        assert str(SiteName.JAVDB) in requested[-1]
        assert len(requested) == 2


def test_uncensored_double_page_keeps_japanese_crop_flow() -> None:
    metadata = AggregatedMetadata(
        number="ABC-001",
        poster_urls=["https://example.com/poster.jpg"],
        original_poster_urls=["https://example.com/poster.jpg"],
        thumb_urls=["https://example.com/double-cover.jpg"],
        double_page_poster_urls=["https://example.com/poster.jpg"],
        double_page_original_poster_urls=["https://example.com/poster.jpg"],
        double_page_thumb_urls=["https://example.com/double-cover.jpg"],
    )
    assert _prepare_image_content_type(ContentType.UNCENSORED, metadata) is ContentType.UNCENSORED
    assert metadata.poster_urls == ["https://example.com/poster.jpg"]
    assert metadata.thumb_urls == ["https://example.com/double-cover.jpg"]


def test_uncensored_single_jacket_without_bigcover_uses_western_flow() -> None:
    metadata = AggregatedMetadata(
        number="ABC-001A",
        poster_urls=["https://example.com/jacket.jpg"],
        original_poster_urls=["https://example.com/jacket.jpg"],
        thumb_urls=[],
    )
    assert _prepare_image_content_type(ContentType.UNCENSORED, metadata) is ContentType.WESTERN
    assert metadata.poster_urls == ["https://example.com/jacket.jpg"]
    assert metadata.thumb_urls == ["https://example.com/jacket.jpg"]


def test_uncensored_original_poster_and_thumb_alone_do_not_prove_double_page() -> None:
    metadata = AggregatedMetadata(
        number="ABC-001B",
        poster_urls=["https://example.com/poster-small.jpg"],
        original_poster_urls=["https://example.com/poster-small.jpg"],
        thumb_urls=["https://example.com/poster-large.jpg"],
    )
    assert _prepare_image_content_type(ContentType.UNCENSORED, metadata) is ContentType.WESTERN
    assert metadata.poster_urls == ["https://example.com/poster-large.jpg"]
    assert metadata.thumb_urls == ["https://example.com/poster-large.jpg"]


def test_uncensored_single_image_uses_western_cover_flow_even_if_poster_candidates_exist() -> None:
    metadata = AggregatedMetadata(
        number="ABC-002",
        poster_urls=["https://example.com/derived-poster.jpg"],
        original_poster_urls=[],
        thumb_urls=["https://example.com/single-cover.jpg"],
    )
    assert _prepare_image_content_type(ContentType.UNCENSORED, metadata) is ContentType.WESTERN
    assert metadata.poster_urls == ["https://example.com/single-cover.jpg"]
    assert metadata.thumb_urls == ["https://example.com/single-cover.jpg"]
    assert metadata.original_poster_urls == ["https://example.com/single-cover.jpg"]


def test_uncensored_missing_poster_url_uses_western_cover_flow() -> None:
    metadata = AggregatedMetadata(
        number="ABC-003",
        poster_urls=[],
        original_poster_urls=[],
        thumb_urls=["https://example.com/cover.jpg"],
    )
    assert _prepare_image_content_type(ContentType.UNCENSORED, metadata) is ContentType.WESTERN
    assert metadata.poster_urls == ["https://example.com/cover.jpg"]
    assert metadata.original_poster_urls == ["https://example.com/cover.jpg"]


def test_censored_image_flow_is_unchanged() -> None:
    metadata = AggregatedMetadata(number="ABC-004", thumb_urls=["https://example.com/cover.jpg"])
    assert _prepare_image_content_type(ContentType.CENSORED, metadata) is ContentType.CENSORED
    assert metadata.poster_urls == []
    assert metadata.thumb_urls == ["https://example.com/cover.jpg"]


class TestScrapeHandler:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_successful_scrape(self, repo: Repository, handler):
        media = await repo.create_media_file(library_id=1, path="/media/MIDV-123.mp4")
        result = await handler.handle(
            ScrapePayload(media_file_id=media.id, number="MIDV-123", content_type=ContentType.CENSORED)
        )
        assert result.success is True
        # 验证元数据已存储
        metadata = await repo.get_metadata_by_number("MIDV-123")
        assert metadata is not None
        assert metadata.title == "Mock Title"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_missing_poster_does_not_fall_back_to_thumb_for_japanese_content(
        self, repo: Repository, resource_store
    ):
        class ThumbOnlyCrawler(MockCrawler):
            async def fetch(self, query, options=None) -> MediaMetadata | None:
                return MediaMetadata.model_validate(
                    {
                        "number": "MIDV-123",
                        "title": "Mock Title",
                        "thumb_urls": ["https://example.com/thumb.jpg"],
                    }
                )

        config = _config_with({ContentType.CENSORED: [SiteName.JAVDB]})
        h = ScrapeHandler(
            repo=repo,
            factory=FakeFactory({"javdb": ThumbOnlyCrawler()}),
            resource_store=resource_store,
            pipeline_config=config,
        )
        media = await repo.create_media_file(library_id=1, path="/media/MIDV-123.mp4")
        result = await h.handle(
            ScrapePayload(media_file_id=media.id, number="MIDV-123", content_type=ContentType.CENSORED)
        )
        assert result.success is True
        metadata = await repo.get_metadata_by_number("MIDV-123")
        assert metadata is not None
        assert metadata.poster_urls == []
        assert metadata.original_poster_urls == []
        assert metadata.thumb_urls == ["https://example.com/thumb.jpg"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_payload_content_type_lands_on_new_entry(self, repo: Repository, resource_store):
        """payload 的条目类型写进新建条目, 不按番号重新推断."""
        factory = FakeFactory({"theporndb": WesternMockCrawler()})
        config = _config_with({ContentType.WESTERN: [SiteName.THEPORNDB]})
        h = ScrapeHandler(repo=repo, factory=factory, resource_store=resource_store, pipeline_config=config)

        media = await repo.create_media_file(library_id=1, path="/media/MD-0123.mp4")
        result = await h.handle(
            ScrapePayload(
                media_file_id=media.id,
                number="MD-0123",
                content_type=ContentType.WESTERN,
                theporndb_uuid="scene-test-1",
            )
        )

        assert result.success is True
        metadata = await repo.get_metadata_by_theporndb_uuid("scene-test-1")
        assert metadata is not None
        assert metadata.content_type is ContentType.WESTERN

    @pytest.mark.asyncio(loop_scope="function")
    async def test_western_uses_cover_for_all_three_image_fields(self, repo: Repository, resource_store):
        class WesternCoverCrawler(WesternMockCrawler):
            async def fetch(self, query, options=None) -> MediaMetadata | None:
                return MediaMetadata.model_validate(
                    {
                        "number": query.number,
                        "title": "Western Cover",
                        "external_id": "scene-cover-test",
                        "theporndb_uuid": "scene-cover-test",
                        "thumb_urls": ["https://example.com/western-cover.jpg"],
                        "poster_urls": ["https://example.com/old-poster.jpg"],
                        "original_poster_urls": ["https://example.com/old-original.jpg"],
                    }
                )

        config = _config_with({ContentType.WESTERN: [SiteName.THEPORNDB]})
        h = ScrapeHandler(
            repo=repo,
            factory=FakeFactory({"theporndb": WesternCoverCrawler()}),
            resource_store=resource_store,
            pipeline_config=config,
        )
        media = await repo.create_media_file(library_id=1, path="/media/MD-COVER.mp4")
        result = await h.handle(
            ScrapePayload(
                media_file_id=media.id,
                number="MD-COVER",
                content_type=ContentType.WESTERN,
                theporndb_uuid="scene-cover-test",
            )
        )
        assert result.success is True
        metadata = await repo.get_metadata_by_theporndb_uuid("scene-cover-test")
        assert metadata is not None
        cover = ["https://example.com/western-cover.jpg"]
        assert metadata.thumb_urls == cover
        assert metadata.poster_urls == cover
        assert metadata.original_poster_urls == cover

    @pytest.mark.asyncio(loop_scope="function")
    async def test_western_multiple_encodings_share_scene_metadata(self, repo: Repository, resource_store):
        config = _config_with({ContentType.WESTERN: [SiteName.THEPORNDB]})
        handler = ScrapeHandler(
            repo=repo,
            factory=FakeFactory({"theporndb": WesternMockCrawler()}),
            resource_store=resource_store,
            pipeline_config=config,
        )
        first = await repo.create_media_file(
            library_id=1, path="/media/Lancelotstylesevolution.26.10.16-1.mp4", oshash="hash-4k"
        )
        second = await repo.create_media_file(
            library_id=1, path="/media/Lancelotstylesevolution.26.10.16-2.mp4", oshash="hash-1080p"
        )

        first_result = await handler.handle(
            ScrapePayload(
                media_file_id=first.id,
                number="Lancelotstylesevolution.26.10.16",
                content_type=ContentType.WESTERN,
            )
        )
        second_result = await handler.handle(
            ScrapePayload(
                media_file_id=second.id,
                number="Lancelotstylesevolution.26.10.16",
                content_type=ContentType.WESTERN,
            )
        )

        assert first_result.success is True and second_result.success is True
        first_saved = await repo.get_media_file(first.id)
        second_saved = await repo.get_media_file(second.id)
        assert first_saved is not None and second_saved is not None
        assert first_saved.metadata_id == second_saved.metadata_id
        assert await repo.get_metadata_by_theporndb_uuid("scene-test-1") is not None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_western_oshash_cannot_be_manually_rebound_to_another_scene(self, repo: Repository, resource_store):
        number = "Lancelotstylesevolution.26.10.16"
        metadata = await repo.upsert_western_metadata("scene-original", number)
        media = await repo.create_media_file(
            library_id=1, path=f"/media/{number}-1.mp4", oshash="hash-bound", metadata_id=metadata.id
        )
        config = _config_with({ContentType.WESTERN: [SiteName.THEPORNDB]})
        handler = ScrapeHandler(
            repo=repo,
            factory=FakeFactory({"theporndb": WesternMockCrawler()}),
            resource_store=resource_store,
            pipeline_config=config,
        )

        result = await handler.handle(
            ScrapePayload(
                media_file_id=media.id,
                number=number,
                content_type=ContentType.WESTERN,
                theporndb_uuid="scene-other",
            )
        )

        assert result.success is False
        assert "另一条 WESTERN 作品" in (result.error or "")

    @pytest.mark.asyncio(loop_scope="function")
    async def test_western_rescrape_backfills_theporndb_uuid_on_original_metadata(
        self, repo: Repository, resource_store
    ):
        number = "Lancelotstylesevolution.26.10.16"
        legacy = await repo.upsert_metadata(number=number)
        media = await repo.create_media_file(
            library_id=1,
            path=f"/media/{number}-1.mp4",
            oshash="hash-legacy",
            metadata_id=legacy.id,
        )
        config = _config_with({ContentType.WESTERN: [SiteName.THEPORNDB]})
        handler = ScrapeHandler(
            repo=repo,
            factory=FakeFactory({"theporndb": WesternMockCrawler()}),
            resource_store=resource_store,
            pipeline_config=config,
        )

        result = await handler.handle(
            ScrapePayload(number=number, content_type=ContentType.WESTERN, metadata_id=legacy.id)
        )

        assert result.success is True
        updated = await repo.get_metadata_by_theporndb_uuid("scene-test-1")
        assert updated is not None
        assert updated.id == legacy.id
        assert updated.number == number
        assert media.metadata_id == legacy.id

    @pytest.mark.asyncio(loop_scope="function")
    async def test_append_sources_keeps_existing_order_and_skips_fields(self, repo: Repository, handler):
        """「添加来源链接」只补链接: 既有顺序不动, 新来源接在末尾, 其余字段不写.

        它跟手动搜索的区别正在这里: 手动搜索让选中的来源接管字段与来源顺序, 补链接不该动
        用户已经定好的内容.
        """
        seeded = await repo.upsert_metadata(
            number="MIDV-123",
            title="Manual",
            thumb_urls=["https://kept/t.jpg"],
            poster_urls=["https://kept/p.jpg"],
            source_urls={"javbus": "https://javbus/1", "javdb": "https://javdb/1"},
        )
        assert seeded.id is not None

        media = await repo.create_media_file(library_id=1, path="/media/MIDV-123.mp4")
        result = await handler.handle(
            ScrapePayload(
                media_file_id=media.id,
                number="MIDV-123",
                content_type=ContentType.CENSORED,
                manual=True,
                preferred_site="javdb",
                append_sources=True,
            )
        )

        assert result.success is True
        updated = await repo.get_metadata_by_number("MIDV-123")
        assert updated is not None
        # 字段保持原样.
        assert updated.title == "Manual"
        assert updated.thumb_urls == ["https://kept/t.jpg"]
        assert updated.poster_urls == ["https://kept/p.jpg"]
        # 既有两条链接顺序不变; 本次命中的站已在列表里, 不重复插入. 列表里仍是这两条.
        assert list(updated.source_urls) == ["javbus", "javdb"]
        assert updated.source_urls["javbus"] == "https://javbus/1"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_append_sources_adds_new_site_at_end(self, repo: Repository, handler):
        """列表里没有的站点追加到末尾."""
        seeded = await repo.upsert_metadata(
            number="MIDV-123",
            title="Manual",
            source_urls={"javbus": "https://javbus/1"},
        )
        assert seeded.id is not None

        media = await repo.create_media_file(library_id=1, path="/media/MIDV-123.mp4")
        await handler.handle(
            ScrapePayload(
                media_file_id=media.id,
                number="MIDV-123",
                content_type=ContentType.CENSORED,
                manual=True,
                append_sources=True,
            )
        )

        updated = await repo.get_metadata_by_number("MIDV-123")
        assert updated is not None
        assert updated.source_urls["javbus"] == "https://javbus/1"
        assert next(iter(updated.source_urls)) == "javbus"
        assert updated.title == "Manual"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_manual_scrape_overwrites_metadata(self, repo: Repository, resource_store):
        """手动刮削覆盖字段: 手动模式即使 use_cache 里带 metadata 也重新抓取.

        复用 raw 快照会让「手动强制刮削」变成「读上次的旧值」, 用户点完看不出任何变化.
        来源侧换成新标题才能区分「重新抓了」与「读了缓存」.
        """
        fetched: list[str] = []

        class ShiftingCrawler(MockCrawler):
            async def fetch(self, query, options=None) -> MediaMetadata | None:
                fetched.append(query.number)
                # 每次返回不同标题: 读了缓存就还是旧值, 重新抓才会变.
                title = f"Fetched {len(fetched)}"
                return MediaMetadata.model_validate({"number": "MIDV-123", "title": title, "tags": ["Fresh"]})

        h = ScrapeHandler(
            repo=repo,
            factory=FakeFactory({"javdb": ShiftingCrawler()}),
            resource_store=resource_store,
            pipeline_config=HotSettings(),
        )
        # raw 快照是缓存复用的载体: 没有它就根本走不到「读旧值」那条路, 也就测不出差别.
        seeded = await repo.upsert_metadata(
            number="MIDV-123",
            title="Old Title",
            raw={"javdb": {"number": "MIDV-123", "title": "Cached Title", "tags": ["Stale"]}},
        )
        assert seeded.id is not None

        media = await repo.create_media_file(library_id=1, path="/media/MIDV-123.mp4")
        result = await h.handle(
            ScrapePayload(
                media_file_id=media.id,
                number="MIDV-123",
                content_type=ContentType.CENSORED,
                manual=True,
                # 手动模式要无视这个缓存开关, 否则等于什么都没刮.
                use_cache={CacheKind.metadata, CacheKind.trans},
            )
        )

        assert result.success is True
        updated = await repo.get_metadata_by_number("MIDV-123")
        assert updated is not None
        assert updated.title == "Fetched 1"
        assert updated.tags == ["Fresh"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_scrape_does_not_register_file_record(self, repo: Repository, handler):
        """刮削成功后不写文件记录.

        那张表记的是「整理过的文件」, 由整理落盘时写入; 刮削不搬文件, 记进来只会让它变成
        一份流水账, 而界面显示的是「待整理」.
        """
        media = await repo.create_media_file(library_id=1, path="/media/MIDV-123.mp4")
        result = await handler.handle(
            ScrapePayload(media_file_id=media.id, number="MIDV-123", content_type=ContentType.CENSORED)
        )

        assert result.success is True
        assert await repo.list_scraped_filenames(library_id=1) == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_skips_locked_fields_end_to_end(self, repo: Repository, handler):
        """自动刮削跳过锁定字段, 未锁定字段正常更新, 锁定字段的来源标注保留."""
        seeded = await repo.upsert_metadata(
            number="MIDV-123", title="Manual", tags=["keep"], field_sources={"title": "manual-src"}
        )
        assert seeded.id is not None
        await repo.set_metadata_locks(seeded.id, [MetadataField.TITLE])

        media = await repo.create_media_file(library_id=1, path="/media/MIDV-123.mp4")
        result = await handler.handle(
            ScrapePayload(media_file_id=media.id, number="MIDV-123", content_type=ContentType.CENSORED)
        )

        assert result.success is True
        updated = await repo.get_metadata_by_number("MIDV-123")
        assert updated is not None
        assert updated.title == "Manual"
        assert updated.tags == ["Drama"]
        assert updated.field_sources["title"] == "manual-src"
        assert updated.locked_fields == ["title"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_reports_determinate_progress(self, repo: Repository, handler):
        """ScrapeHandler 上报 determinate 进度: 字段获取 → materialize → done."""
        events: list[tuple[int, int, str]] = []

        async def capture(current: int, total: int, message: str = "") -> None:
            events.append((current, total, message))

        handler.set_progress_callback(capture)
        media = await repo.create_media_file(library_id=1, path="/media/MIDV-123.mp4")
        result = await handler.handle(
            ScrapePayload(media_file_id=media.id, number="MIDV-123", content_type=ContentType.CENSORED)
        )
        assert result.success is True

        assert events
        total = events[-1][1]
        assert total > 0
        assert events[0] == (0, total, "fetch")
        assert events[-1] == (total, total, "done")
        assert all(t == total for _, t, _ in events)
        assert [c for c, _, _ in events] == sorted(c for c, _, _ in events)
        assert any(m == "materialize" for _, _, m in events)
        # 获取结束抬到标量满分 (= total - 2, 留给 materialize/persist)
        assert any(c == total - 2 and m == "fetch" for c, _, m in events)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_updates_media_file_status(self, repo: Repository, handler):
        media = await repo.create_media_file(library_id=1, path="/media/MIDV-123.mp4")
        assert media.id is not None
        await handler.handle(
            ScrapePayload(media_file_id=media.id, number="MIDV-123", content_type=ContentType.CENSORED)
        )
        updated = await repo.get_media_file(media.id)
        assert updated is not None
        assert updated.status == MediaFileStatus.SCRAPED

    @pytest.mark.asyncio(loop_scope="function")
    async def test_missing_payload_fields_raises(self, handler):
        """parse_payload 在字段缺失时抛出异常"""
        with pytest.raises((TypeError, KeyError, ValidationError)):
            handler.parse_payload({"media_file_id": 1})

    @pytest.mark.asyncio(loop_scope="function")
    async def test_no_results_marks_failed(self, repo: Repository, resource_store, tmp_path: Path):
        """所有爬虫返回空结果时标记失败, 站点明细随失败结果返回"""
        empty_factory = FakeFactory({"javdb": EmptySearchCrawler()})
        h = ScrapeHandler(
            repo=repo,
            factory=empty_factory,
            resource_store=resource_store,
            pipeline_config=HotSettings(),
        )
        media = await repo.create_media_file(library_id=1, path="/media/TEST-001.mp4")
        # 站点明细来自 `Recorder`, 没有它时失败载荷只有 `failed_sites`.
        recorder = Recorder.begin(
            tmp_path, Task(id=91, type=TaskType.SCRAPE, status=TaskStatus.RUNNING, payload={}), HotSettings()
        )
        try:
            result = await h.handle(
                ScrapePayload(media_file_id=media.id, number="TEST-001", content_type=ContentType.CENSORED)
            )
        finally:
            recorder.close()

        assert result.success is False
        # 失败也要带结果载荷, 否则界面只剩一句错误, 说不清是哪个站点怎么了.
        assert result.result is not None
        assert result.result.metadata_id is None
        assert result.result.failed_sites == ["javdb"]
        assert [(row.site, row.outcome, row.reason) for row in result.result.outcomes] == [
            ("javdb", "failed", "no_usable_metadata")
        ]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_empty_scalars_with_image_still_succeeds(self, repo: Repository, resource_store):
        """标量全空但有海报 → 仍算抓到数据, 不整单失败."""

        class PosterOnlyCrawler:
            name = SiteName.JAVDB

            def __init__(self, client=None):
                self.client = client

            async def fetch(self, query, options=None) -> MediaMetadata | None:
                return MediaMetadata(number=query.number, poster_urls=["http://p.jpg"])

        h = ScrapeHandler(
            repo=repo,
            factory=FakeFactory({"javdb": PosterOnlyCrawler()}),
            resource_store=resource_store,
            pipeline_config=HotSettings(),
        )
        media = await repo.create_media_file(library_id=1, path="/media/POSTER-001.mp4")
        result = await h.handle(
            ScrapePayload(media_file_id=media.id, number="POSTER-001", content_type=ContentType.CENSORED)
        )
        assert result.success is True
        assert result.result is not None
        assert result.result.field_sources == {}
        metadata = await repo.get_metadata_by_number("POSTER-001")
        assert metadata is not None
        assert metadata.poster_urls == ["http://p.jpg"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_locked_source_preserved_when_scalars_empty(self, repo: Repository, resource_store):
        """标量全空的成功刮削也不冲掉锁定字段的来源标注."""
        seeded = await repo.upsert_metadata(number="POSTER-001", title="Manual", field_sources={"title": "manual-src"})
        assert seeded.id is not None
        await repo.set_metadata_locks(seeded.id, [MetadataField.TITLE])

        class PosterOnlyCrawler:
            name = SiteName.JAVDB

            def __init__(self, client=None):
                self.client = client

            async def fetch(self, query, options=None) -> MediaMetadata | None:
                return MediaMetadata(number=query.number, poster_urls=["http://p.jpg"])

        h = ScrapeHandler(
            repo=repo,
            factory=FakeFactory({"javdb": PosterOnlyCrawler()}),
            resource_store=resource_store,
            pipeline_config=HotSettings(),
        )
        media = await repo.create_media_file(library_id=1, path="/media/POSTER-001.mp4")
        result = await h.handle(
            ScrapePayload(media_file_id=media.id, number="POSTER-001", content_type=ContentType.CENSORED)
        )

        assert result.success is True
        metadata = await repo.get_metadata_by_number("POSTER-001")
        assert metadata is not None
        assert metadata.title == "Manual"
        assert metadata.field_sources == {"title": "manual-src"}
        assert metadata.poster_urls == ["http://p.jpg"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_declared_source_receives_partial_result(self, repo: Repository, resource_store):
        """第二段来源收到第一段已定值的标量与已抓到的图片地址, 第一段来源收到 None."""

        class RecordingFetcher:
            def __init__(self, metadata: MediaMetadata) -> None:
                self._metadata = metadata
                self.partials: list[AggregatedMetadata | None] = []

            async def fetch(self, query, options=None) -> MediaMetadata | None:
                self.partials.append(query.partial_result)
                return self._metadata

        declared = RecordingFetcher(
            MediaMetadata(number="PHASE-001", title="FromDeclared", poster_urls=["http://p2.jpg"])
        )
        first_phase = RecordingFetcher(
            MediaMetadata(number="PHASE-001", title="FromPhase1", poster_urls=["http://p1.jpg"])
        )
        catalog = (SourceDescriptor(id="javdb", name="javdb", traits=frozenset({"needs_partial"})),)
        h = ScrapeHandler(
            repo=repo,
            factory=FakeFactory({"dmm": first_phase, "javdb": declared}),
            resource_store=resource_store,
            pipeline_config=HotSettings(),
            source_catalog=catalog,
        )
        media = await repo.create_media_file(library_id=1, path="/media/PHASE-001.mp4")
        result = await h.handle(
            ScrapePayload(media_file_id=media.id, number="PHASE-001", content_type=ContentType.CENSORED)
        )

        assert result.success is True
        assert first_phase.partials == [None]
        assert len(declared.partials) == 1
        # dmm 是默认路由里各标量字段的链首且在第一段已返回, 其取值必须出现在第二段的入参上.
        assert declared.partials[0] is not None
        assert declared.partials[0].title == "FromPhase1"
        assert declared.partials[0].poster_urls == ["http://p1.jpg"]
        # 段间只作参考: 入库列表仍按字段链拼接两段贡献.
        metadata = await repo.get_metadata_by_number("PHASE-001")
        assert metadata is not None
        assert metadata.poster_urls == ["http://p1.jpg", "http://p2.jpg"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_builtin_trait_defers_without_injected_catalog(self, repo: Repository, resource_store):
        """未注入来源目录时, 内置来源的 traits 由 profile 合成的 descriptor 提供."""

        class RecordingFetcher:
            def __init__(self, metadata: MediaMetadata) -> None:
                self._metadata = metadata
                self.partials: list[AggregatedMetadata | None] = []

            async def fetch(self, query, options=None) -> MediaMetadata | None:
                self.partials.append(query.partial_result)
                return self._metadata

        declared = RecordingFetcher(MediaMetadata(number="PHASE-002", title="FromOfficial"))
        first_phase = RecordingFetcher(MediaMetadata(number="PHASE-002", title="FromDMM"))
        h = ScrapeHandler(
            repo=repo,
            factory=FakeFactory({"dmm": first_phase, "official": declared}),
            resource_store=resource_store,
            pipeline_config=_config_with({ContentType.CENSORED: [SiteName.DMM, SiteName.OFFICIAL]}),
        )
        media = await repo.create_media_file(library_id=1, path="/media/PHASE-002.mp4")
        result = await h.handle(
            ScrapePayload(media_file_id=media.id, number="PHASE-002", content_type=ContentType.CENSORED)
        )

        assert result.success is True
        assert first_phase.partials == [None]
        assert declared.partials[0] is not None
        assert declared.partials[0].title == "FromDMM"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_materializes_cropped_poster(self, repo: Repository, resource_store):
        """有 web_client 时, scrape 物化: poster 候选偏矮 → 裁剪 → metadata 记内部 URL."""
        from typing import cast

        from PIL import Image

        from amane.media.pipeline import RESOURCE_URL_PREFIX
        from amane.net.http import WebClient

        class ImgCrawler:
            name = SiteName.JAVDB

            def __init__(self, client=None):
                pass

            async def fetch(self, query, options=None) -> MediaMetadata | None:
                return MediaMetadata(
                    number="MIDV-123",
                    title="T",
                    thumb_urls=["https://s/t.jpg"],
                    poster_urls=["https://s/p.jpg"],
                )

        class FakeWebClient:
            sizes: ClassVar = {"https://s/t.jpg": (800, 538), "https://s/p.jpg": (300, 420)}

            async def download(self, url, dest, **kwargs) -> bool:
                dest.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", self.sizes[url], "blue").save(dest)
                return True

            async def get_filesize(self, url, **kwargs):
                return None

            async def resolve_final_url(self, url, **kwargs) -> str:
                return url

        h = ScrapeHandler(
            repo=repo,
            factory=FakeFactory({"javdb": ImgCrawler()}),
            resource_store=resource_store,
            pipeline_config=HotSettings(),
            web_client=cast("WebClient", FakeWebClient()),
        )
        media = await repo.create_media_file(library_id=1, path="/media/MIDV-123.mp4")
        result = await h.handle(
            ScrapePayload(media_file_id=media.id, number="MIDV-123", content_type=ContentType.CENSORED)
        )
        assert result.success is True
        metadata = await repo.get_metadata_by_number("MIDV-123")
        assert metadata is not None
        assert metadata.poster_urls is not None and len(metadata.poster_urls) == 1
        assert metadata.poster_urls[0].startswith(RESOURCE_URL_PREFIX)  # 内部派生 (裁剪)
        assert metadata.thumb_urls == ["https://s/t.jpg"]  # 外部原样

    @pytest.mark.asyncio(loop_scope="function")
    async def test_materializes_cropped_poster_when_poster_equals_thumb(self, repo: Repository, resource_store):
        """海报与封面同源时也要裁.

        站点没给独立竖版海报时会拿横版大图两处都用, 尺寸比是 1.0, 按「候选够高」的规则会被
        跳过, 片库里于是长期显示那张横版图.
        """
        from typing import cast

        from PIL import Image

        from amane.media.pipeline import RESOURCE_URL_PREFIX
        from amane.net.http import WebClient

        shared = "https://s/same.jpg"

        class ImgCrawler:
            name = SiteName.JAVDB

            def __init__(self, client=None):
                pass

            async def fetch(self, query, options=None) -> MediaMetadata | None:
                return MediaMetadata(
                    number="093014-701",
                    title="T",
                    thumb_urls=[shared],
                    poster_urls=[shared],
                )

        class FakeWebClient:
            sizes: ClassVar = {shared: (800, 538)}

            async def download(self, url, dest, **kwargs) -> bool:
                dest.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", self.sizes[url], "blue").save(dest)
                return True

            async def get_filesize(self, url, **kwargs):
                return None

            async def resolve_final_url(self, url, **kwargs) -> str:
                return url

        h = ScrapeHandler(
            repo=repo,
            factory=FakeFactory({"javdb": ImgCrawler()}),
            resource_store=resource_store,
            pipeline_config=HotSettings(),
            web_client=cast("WebClient", FakeWebClient()),
        )
        media = await repo.create_media_file(library_id=1, path="/media/093014-701.mp4")
        result = await h.handle(
            ScrapePayload(media_file_id=media.id, number="093014-701", content_type=ContentType.CENSORED)
        )
        assert result.success is True
        metadata = await repo.get_metadata_by_number("093014-701")
        assert metadata is not None
        assert metadata.poster_urls is not None
        # 裁成内部派生件, 不再是原横版大图.
        assert metadata.poster_urls[0].startswith(RESOURCE_URL_PREFIX)
        assert metadata.poster_urls[0] != shared

    @pytest.mark.asyncio(loop_scope="function")
    async def test_materializes_cropped_poster_for_single_image_uncensored(self, repo: Repository, resource_store):
        """单图无码 (同源给同一张海报与封面) 仍按无码自动裁切.

        这类条目在图片排布上走「单图对齐」, 但条目的类型是无码, 裁切放行按条目类型判定;
        按图片排布口径判定会让全部单图无码停止裁切.
        """
        from typing import cast

        from PIL import Image

        from amane.media.pipeline import RESOURCE_URL_PREFIX
        from amane.net.http import WebClient

        cover = "https://s/HEYZO-0474.jpg"

        class ImgCrawler:
            name = SiteName.JAVBUS

            def __init__(self, client=None):
                pass

            async def fetch(self, query, options=None) -> MediaMetadata | None:
                # 无码站只给一张播放器静态图, 海报与封面同源, 不声明双页.
                return MediaMetadata(number="HEYZO-0474", title="T", thumb_urls=[cover], poster_urls=[cover])

        class FakeWebClient:
            sizes: ClassVar = {cover: (800, 538)}

            async def download(self, url, dest, **kwargs) -> bool:
                dest.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", self.sizes[url], "blue").save(dest)
                return True

            async def get_filesize(self, url, **kwargs):
                return None

            async def resolve_final_url(self, url, **kwargs) -> str:
                return url

        h = ScrapeHandler(
            repo=repo,
            factory=FakeFactory({"javbus": ImgCrawler()}),
            resource_store=resource_store,
            pipeline_config=_config_with({ContentType.UNCENSORED: [SiteName.JAVBUS]}),
            web_client=cast("WebClient", FakeWebClient()),
        )
        media = await repo.create_media_file(library_id=1, path="/media/HEYZO-0474.mp4")
        result = await h.handle(
            ScrapePayload(media_file_id=media.id, number="HEYZO-0474", content_type=ContentType.UNCENSORED)
        )
        assert result.success is True
        metadata = await repo.get_metadata_by_number("HEYZO-0474")
        assert metadata is not None
        assert metadata.poster_urls is not None
        assert metadata.poster_urls[0].startswith(RESOURCE_URL_PREFIX)
        assert metadata.poster_urls[0] != cover


# --- content_routes 资格真值 + 有序路由 ---


class RecordingFactory:
    """记录被请求的站点名, 返回这些名字对应的 MockCrawler."""

    def __init__(self, available: dict):
        self._available = available
        self.requested: list[str] = []

    async def get_crawlers(self, names):
        self.requested = list(names)
        return {n: self._available[n] for n in names if n in self._available}


def _config_with(routes, field_priority=None, field_blacklist=None):
    scraping = ScrapingConfig(
        content_routes=routes,
        field_priority=field_priority or {},
        field_blacklist=field_blacklist or {},
    )
    return HotSettings(scraping=scraping)


class TestContentRoutesFiltering:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_only_routed_sites_requested(self, repo: Repository, resource_store):
        """content_routes 决定 content_type 下哪些站点被请求."""
        available = {
            "javdb": MockCrawler(),
            "dmm": MockCrawler(),
            "javbus": MockCrawler(),
        }
        factory = RecordingFactory(available)
        config = _config_with({ContentType.CENSORED: [SiteName.JAVDB, SiteName.DMM]})
        h = ScrapeHandler(repo=repo, factory=factory, resource_store=resource_store, pipeline_config=config)

        media = await repo.create_media_file(library_id=1, path="/media/MIDV-123.mp4")
        await h.handle(ScrapePayload(media_file_id=media.id, number="MIDV-123", content_type=ContentType.CENSORED))

        assert set(factory.requested) == {SiteName.JAVDB, SiteName.DMM}
        assert SiteName.JAVBUS not in factory.requested

    @pytest.mark.asyncio(loop_scope="function")
    async def test_empty_route_returns_error(self, repo: Repository, resource_store):
        """content_routes 对应项为空列表时该类型刮削失败."""
        factory = RecordingFactory({"javbus": MockCrawler()})
        config = _config_with({ContentType.CENSORED: [SiteName.JAVBUS], ContentType.FC2: []})
        h = ScrapeHandler(repo=repo, factory=factory, resource_store=resource_store, pipeline_config=config)

        media = await repo.create_media_file(library_id=1, path="/media/FC2-1.mp4")
        result = await h.handle(ScrapePayload(media_file_id=media.id, number="FC2-1", content_type=ContentType.FC2))

        assert result.success is False
        assert result.error is not None
        assert "没有可用来源" in result.error

    @pytest.mark.asyncio(loop_scope="function")
    async def test_prefer_outside_route_not_requested(self, repo: Repository, resource_store):
        """prefer 不在 route 内的站不会被 get_crawlers 请求."""
        available = {
            "javdb": MockCrawler(),
            "iqqtv": MockCrawler(),
        }
        factory = RecordingFactory(available)
        config = _config_with(
            {ContentType.CENSORED: [SiteName.JAVDB]}, {MetadataField.TITLE: [SiteName.IQQTV, SiteName.JAVDB]}
        )
        h = ScrapeHandler(repo=repo, factory=factory, resource_store=resource_store, pipeline_config=config)

        media = await repo.create_media_file(library_id=1, path="/media/MIDV-123.mp4")
        await h.handle(ScrapePayload(media_file_id=media.id, number="MIDV-123", content_type=ContentType.CENSORED))

        assert list(factory.requested) == [SiteName.JAVDB]
        assert SiteName.IQQTV not in factory.requested

    @pytest.mark.asyncio(loop_scope="function")
    async def test_title_blacklist_uses_next_site(self, repo: Repository, resource_store):
        """title 排除 javdb 后取 dmm; studio 仍可用 javdb."""

        class TitledCrawler:
            def __init__(self, title: str, studio: str):
                self._title = title
                self._studio = studio

            async def fetch(self, query, options=None) -> MediaMetadata | None:
                return MediaMetadata.model_validate(
                    {"number": "MIDV-123", "title": self._title, "studio": self._studio}
                )

        factory = RecordingFactory(
            {
                "javdb": TitledCrawler("FromJavDB", "StudioJavDB"),
                "dmm": TitledCrawler("FromDMM", "StudioDMM"),
            }
        )
        config = _config_with(
            {ContentType.CENSORED: [SiteName.JAVDB, SiteName.DMM]},
            field_blacklist={MetadataField.TITLE: [SiteName.JAVDB]},
        )
        h = ScrapeHandler(repo=repo, factory=factory, resource_store=resource_store, pipeline_config=config)

        media = await repo.create_media_file(library_id=1, path="/media/MIDV-123.mp4")
        result = await h.handle(
            ScrapePayload(media_file_id=media.id, number="MIDV-123", content_type=ContentType.CENSORED)
        )
        assert result.success is True

        metadata = await repo.get_metadata_by_number("MIDV-123")
        assert metadata is not None
        assert metadata.title == "FromDMM"
        assert metadata.studio == "StudioJavDB"
        assert set(factory.requested) == {SiteName.JAVDB, SiteName.DMM}


# --- 翻译集成测试 ---


class TestScrapeTranslation:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_translator_applied_to_title(self, repo: Repository, factory, resource_store):
        """配置 translator + field_language 时, 标题被翻译后再持久化."""
        from amane.enums import Language, MetadataField

        class StubTranslator:
            async def translate(self, text, target, field, *, use_cache=True):
                return f"[{target}]{text}"

        config = HotSettings()
        config.scraping.field_language = {MetadataField.TITLE: Language.ZH_CN}
        config.llm.translate_fields = [MetadataField.TITLE]
        h = ScrapeHandler(
            repo=repo,
            factory=factory,
            resource_store=resource_store,
            pipeline_config=config,
            translator=StubTranslator(),
        )
        media = await repo.create_media_file(library_id=1, path="/media/MIDV-123.mp4")
        await h.handle(ScrapePayload(media_file_id=media.id, number="MIDV-123", content_type=ContentType.CENSORED))

        metadata = await repo.get_metadata_by_number("MIDV-123")
        assert metadata is not None
        assert metadata.title == "[zh_cn]Mock Title"
        assert metadata.original_title == "Mock Title"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_western_skips_translation_and_keeps_original_title(self, repo: Repository, resource_store):
        from amane.enums import Language, MetadataField

        class MustNotTranslate:
            async def translate(self, text, target, field, *, use_cache=True):
                raise AssertionError("WESTERN metadata must not be translated")

        config = _config_with({ContentType.WESTERN: [SiteName.THEPORNDB]})
        config.scraping.field_language = {MetadataField.TITLE: Language.ZH_CN}
        config.llm.translate_fields = [MetadataField.TITLE]
        h = ScrapeHandler(
            repo=repo,
            factory=FakeFactory({"theporndb": WesternMockCrawler()}),
            resource_store=resource_store,
            pipeline_config=config,
            translator=MustNotTranslate(),
        )
        media = await repo.create_media_file(library_id=1, path="/media/MD-0123.mp4")
        result = await h.handle(
            ScrapePayload(
                media_file_id=media.id,
                number="MD-0123",
                content_type=ContentType.WESTERN,
                theporndb_uuid="scene-test-1",
            )
        )
        assert result.success is True
        metadata = await repo.get_metadata_by_theporndb_uuid("scene-test-1")
        assert metadata is not None
        assert metadata.title == "Mock Western Title"
        assert metadata.original_title == "Mock Western Title"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_translator_failure_keeps_original(self, repo: Repository, factory, resource_store):
        """translator 抛异常时降级保留原值, 刮削不失败 (失败即跳过)."""
        from amane.enums import Language, MetadataField

        class BrokenTranslator:
            async def translate(self, text, target, field, *, use_cache=True):
                raise RuntimeError("llm down")

        config = HotSettings()
        config.scraping.field_language = {MetadataField.TITLE: Language.ZH_CN}
        h = ScrapeHandler(
            repo=repo,
            factory=factory,
            resource_store=resource_store,
            pipeline_config=config,
            translator=BrokenTranslator(),
        )
        media = await repo.create_media_file(library_id=1, path="/media/MIDV-123.mp4")
        result = await h.handle(
            ScrapePayload(media_file_id=media.id, number="MIDV-123", content_type=ContentType.CENSORED)
        )

        assert result.success is True
        metadata = await repo.get_metadata_by_number("MIDV-123")
        assert metadata is not None
        assert metadata.title == "Mock Title"


# --- RefreshHandler 测试 ---


class TestRefreshHandler:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_scan_and_add_files(self, repo: Repository, tmp_path):
        """扫描目录, 注册新文件"""
        (tmp_path / "MIDV-123.mp4").write_bytes(b"\x00" * 100)
        (tmp_path / "SSIS-456.mkv").write_bytes(b"\x00" * 100)
        (tmp_path / "readme.txt").write_text("not a media file")

        handler = RefreshHandler(repo=repo)
        result = await handler.handle(RefreshPayload(library_id=1, path=str(tmp_path)))

        assert result.success is True
        assert result.result is not None
        assert result.result.added == 2
        assert result.result.removed == 0
        assert result.result.scrape == 2

    @pytest.mark.asyncio(loop_scope="function")
    @pytest.mark.parametrize(
        ("subtitle_extensions", "subtitle_name", "stored", "expected"),
        [
            pytest.param([".srt"], "MIDV-771.srt", False, True, id="命中置真"),
            pytest.param([".srt"], None, True, False, id="同目录无字幕置假"),
            pytest.param([], "MIDV-771.srt", True, False, id="库关闭字幕发现置假"),
        ],
    )
    async def test_scan_syncs_external_subtitle(
        self,
        repo: Repository,
        tmp_path: Path,
        subtitle_extensions: list[str],
        subtitle_name: str | None,
        stored: bool,
        expected: bool,
    ):
        """存量行的外挂字幕标记只在库扫描时补齐: 迁移不回填, 登记与改 path 都不覆盖既有行."""
        video = tmp_path / "MIDV-771.mp4"
        video.write_bytes(b"\x00" * 100)
        if subtitle_name is not None:
            (tmp_path / subtitle_name).touch()
        lib = await repo.create_library(
            name="t", path=str(tmp_path), write_nfo=False, subtitle_extensions=subtitle_extensions
        )
        assert lib.id is not None
        media = await repo.create_media_file(library_id=lib.id, path=str(video), has_external_subtitle=stored)
        assert media.id is not None

        result = await RefreshHandler(repo=repo).handle(
            RefreshPayload(library_id=lib.id, path=str(tmp_path), scrape=set())
        )

        assert result.success is True
        assert result.result is not None
        assert result.result.added == 0
        synced = await repo.get_media_file(media.id)
        assert synced is not None
        assert synced.has_external_subtitle is expected

    @pytest.mark.asyncio(loop_scope="function")
    async def test_scan_syncs_only_walked_rows(self, repo: Repository, tmp_path: Path):
        """按遍历所见回写: 范围外的行不动, 已一致的行不写库."""
        lib_root = tmp_path / "lib"
        (lib_root / "hit").mkdir(parents=True)
        (lib_root / "hit" / "MIDV-772.mp4").write_bytes(b"\x00" * 100)
        (lib_root / "kept").mkdir()
        (lib_root / "kept" / "MIDV-773.mp4").write_bytes(b"\x00" * 100)
        (lib_root / "kept" / "MIDV-773.srt").touch()
        (lib_root / "outside").mkdir()
        (lib_root / "outside" / "MIDV-774.mp4").write_bytes(b"\x00" * 100)
        (lib_root / "outside" / "MIDV-774.srt").touch()
        lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False, subtitle_extensions=[".srt"])
        assert lib.id is not None
        stale = await repo.create_media_file(
            library_id=lib.id, path=str(lib_root / "hit" / "MIDV-772.mp4"), has_external_subtitle=True
        )
        kept = await repo.create_media_file(
            library_id=lib.id, path=str(lib_root / "kept" / "MIDV-773.mp4"), has_external_subtitle=True
        )
        outside = await repo.create_media_file(
            library_id=lib.id, path=str(lib_root / "outside" / "MIDV-774.mp4"), has_external_subtitle=False
        )
        assert stale.id is not None and kept.id is not None and outside.id is not None
        kept_before = await repo.get_media_file(kept.id)
        assert kept_before is not None

        result = await RefreshHandler(repo=repo).handle(
            RefreshPayload(library_id=lib.id, path=str(lib_root / "hit"), scrape=set())
        )

        assert result.success is True
        synced = await repo.get_media_file(stale.id)
        assert synced is not None
        assert synced.has_external_subtitle is False
        untouched_by_scope = await repo.get_media_file(outside.id)
        assert untouched_by_scope is not None
        assert untouched_by_scope.has_external_subtitle is False
        # 范围内已一致的行不写库: 写库会刷新 updated_at.
        kept_after = await repo.get_media_file(kept.id)
        assert kept_after is not None
        assert kept_after.has_external_subtitle is True
        assert kept_after.updated_at == kept_before.updated_at

    @pytest.mark.asyncio(loop_scope="function")
    async def test_unscoped_scan_writes_cleanup_inventory(self, repo: Repository, tmp_path: Path):
        """整库范围的入库扫描顺带产出清单: 无效文件与空目录都在里面, 面板不必再扫一次."""
        (tmp_path / "MIDV-123.mp4").write_bytes(b"\x00" * 100)
        (tmp_path / "ad.mp4").write_bytes(b"\x00" * 10)
        (tmp_path / "empty").mkdir()
        lib = await repo.create_library(name="t", path=str(tmp_path), write_nfo=False, blacklist_patterns=["ad"])
        assert lib.id is not None
        store = InventoryStore()

        result = await RefreshHandler(repo=repo, inventory_store=store).handle(
            RefreshPayload(library_id=lib.id, path=str(tmp_path), scrape=set())
        )

        assert result.success is True
        inventory = store.latest(lib.id, InventorySource.RULES)
        assert inventory is not None
        assert inventory.scope_path is None
        assert {entry.path.name for entry in inventory.entries} == {"ad.mp4", "empty"}
        assert [hit.path.name for hit in inventory.media_hits] == ["MIDV-123.mp4"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_scoped_scan_leaves_inventory_alone(self, repo: Repository, tmp_path: Path):
        """子目录巡检不代表整库, 不覆盖面板用的清单."""
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "ad.mp4").write_bytes(b"\x00" * 10)
        lib = await repo.create_library(name="t", path=str(tmp_path), write_nfo=False, blacklist_patterns=["ad"])
        assert lib.id is not None
        store = InventoryStore()

        result = await RefreshHandler(repo=repo, inventory_store=store).handle(
            RefreshPayload(library_id=lib.id, path=str(sub), scrape=set())
        )

        assert result.success is True
        assert store.latest(lib.id, InventorySource.RULES) is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_truncated_inventory_keeps_media_complete(
        self,
        repo: Repository,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        candidates_first_scandir: None,
    ):
        """清单触顶不影响入库扫描的媒体收集: add+remove 据此判定存在性, 缺项会删掉磁盘上还在的索引行."""
        for index in range(20):
            (tmp_path / f"NSFS-{index:03d}.mp4").write_bytes(b"\x00" * 100)
        for index in range(3):
            (tmp_path / f"ad-{index}.mp4").write_bytes(b"\x00" * 10)
        lib = await repo.create_library(name="t", path=str(tmp_path), write_nfo=False, blacklist_patterns=["ad-"])
        assert lib.id is not None
        rows = []
        for index in range(20):
            row = await repo.create_media_file(lib.id, path=str(tmp_path / f"NSFS-{index:03d}.mp4"))
            assert row.id is not None
            rows.append(row)

        real_scan = scan_inventory

        async def small_limit(scope_dir, **kwargs):
            kwargs["limit"] = 1
            return await real_scan(scope_dir, **kwargs)

        monkeypatch.setattr("amane.handlers.refresh.scan_inventory", small_limit)

        result = await RefreshHandler(repo=repo).handle(
            RefreshPayload(
                library_id=lib.id,
                path=str(tmp_path),
                scan={ScanMode.add, ScanMode.remove},
                scrape=set(),
            )
        )

        assert result.success is True
        assert result.result is not None
        assert result.result.removed == 0
        for row in rows:
            assert await repo.get_media_file(row.id) is not None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_scan_organizes_scraped_without_rescraping(self, repo: Repository, tmp_path):
        """已有元数据的文件扫描时只整理, 不重新刮削.

        路径模板改过或补充了字段之后, 扫描一次就该把文件落到该去的位置; 但同时不该为此
        再打一遍刮削站点.
        """
        done = await repo.create_media_file(
            library_id=1, path=str(tmp_path / "MIDV-123.mp4"), status=MediaFileStatus.SCRAPED
        )
        (tmp_path / "MIDV-123.mp4").write_bytes(b"\x00" * 100)

        result = await RefreshHandler(repo=repo).handle(
            RefreshPayload(library_id=1, path=str(tmp_path), scan={ScanMode.add}, scrape={MediaFileStatus.PENDING})
        )

        assert result.success is True
        kinds = [(f.task_type, f.key) for f in result.followups]
        # 没有刮削后继: 已刮削的行不在 PENDING 集合里.
        assert not any(k is TaskType.SCRAPE for k, _ in kinds)
        # 有一个整理后继, 且把这条已刮削的行包了进去.
        organize = [f for f in result.followups if f.task_type is TaskType.ORGANIZE]
        assert len(organize) == 1
        payload = organize[0].payload
        assert done.id in payload["media_file_ids"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_scan_organize_can_be_disabled(self, repo: Repository, tmp_path):
        """整理后继可关闭: 只想对齐索引时不该顺手搬文件."""
        await repo.create_media_file(library_id=1, path=str(tmp_path / "MIDV-123.mp4"), status=MediaFileStatus.SCRAPED)
        (tmp_path / "MIDV-123.mp4").write_bytes(b"\x00" * 100)

        result = await RefreshHandler(repo=repo).handle(
            RefreshPayload(library_id=1, path=str(tmp_path), scan={ScanMode.add}, organize=False)
        )

        assert result.success is True
        assert not any(f.task_type is TaskType.ORGANIZE for f in result.followups)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_scan_records_number_on_new_rows(self, repo: Repository, tmp_path):
        """扫描登记新文件时把番号一并落库.

        番号是路径的函数; 不记下来, 后续按番号找行的读取方 (关联文件等) 就得各自重新解析.
        """
        path = tmp_path / "MIDV-123.mp4"
        path.write_bytes(b"\x00" * 100)

        await RefreshHandler(repo=repo).handle(
            RefreshPayload(library_id=1, path=str(tmp_path), scan={ScanMode.add}, scrape=set())
        )

        row = await repo.get_media_file_by_path(str(path))
        assert row is not None
        assert row.number == "MIDV-123"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_scan_adopts_existing_metadata_instead_of_rescraping(self, repo: Repository, tmp_path):
        """删库重加/换目录后重新登记的文件, 若已有同番号元数据就直接挂上.

        否则这些行以 PENDING 进库, 下一次扫描会把整库当新文件再刮一遍 —— 既慢又白打站点.
        """
        meta = await repo.upsert_metadata(number="MIDV-123", title="Existing")
        assert meta.id is not None
        path = tmp_path / "MIDV-123.mp4"
        path.write_bytes(b"\x00" * 100)

        result = await RefreshHandler(repo=repo).handle(
            RefreshPayload(library_id=1, path=str(tmp_path), scan={ScanMode.add}, scrape={MediaFileStatus.PENDING})
        )

        assert result.success is True
        row = await repo.get_media_file_by_path(str(path))
        assert row is not None
        assert row.metadata_id == meta.id
        assert row.status is MediaFileStatus.SCRAPED
        # 已经挂上元数据, 不该再排刮削.
        assert not any(f.task_type is TaskType.SCRAPE for f in result.followups)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_handle_files(self, repo: Repository, tmp_path):
        """新文件被注册; PENDING 状态的文件被刮削; SKIP 状态的文件不被刮削."""

        async def new_file(name, db: bool, status: MediaFileStatus = MediaFileStatus.PENDING):
            path = tmp_path / name
            path.write_bytes(b"\x00" * 100)
            if db:
                return await repo.create_media_file(library_id=1, path=str(path), status=status)
            return None

        await new_file("MIDV-123.mp4", db=True, status=MediaFileStatus.PENDING)  # 已注册 PENDING -> 刮削
        await new_file("MIDV-124.mp4", db=False)  # 未注册 -> 注册并刮削 (PENDING)
        await new_file("MIDV-125.mp4", db=True, status=MediaFileStatus.SKIP)  # SKIP -> 不刮削

        handler = RefreshHandler(repo=repo)
        result = await handler.handle(RefreshPayload(library_id=1, path=str(tmp_path)))

        assert result.success is True
        assert result.result is not None
        assert result.result.added == 1  # 仅 MIDV-124 是新文件
        assert result.result.removed == 0
        assert result.result.scrape == 2  # MIDV-123 + MIDV-124 (MIDV-125 是 SKIP 不刮削)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_recursive_scan(self, repo: Repository, tmp_path):
        """递归扫描子目录"""
        subdir = tmp_path / "season1"
        subdir.mkdir()
        (subdir / "EP01.mp4").write_bytes(b"\x00" * 100)
        (tmp_path / "MIDV-123.mp4").write_bytes(b"\x00" * 100)

        handler = RefreshHandler(repo=repo)
        result = await handler.handle(RefreshPayload(library_id=1, path=str(tmp_path), recursive=True))

        assert result.success is True
        assert result.result is not None
        assert result.result.added == 2
        assert result.result.removed == 0
        assert result.result.scrape == 2

    @pytest.mark.asyncio(loop_scope="function")
    async def test_non_recursive_scan(self, repo: Repository, tmp_path):
        """非递归扫描不进入子目录"""
        subdir = tmp_path / "season1"
        subdir.mkdir()
        (subdir / "EP01.mp4").write_bytes(b"\x00" * 100)
        (tmp_path / "MIDV-123.mp4").write_bytes(b"\x00" * 100)

        handler = RefreshHandler(repo=repo)
        result = await handler.handle(RefreshPayload(library_id=1, path=str(tmp_path), recursive=False))

        assert result.success is True
        assert result.result is not None
        assert result.result.added == 1
        assert result.result.removed == 0
        assert result.result.scrape == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_custom_patterns(self, repo: Repository, tmp_path):
        """自定义 glob 模式过滤"""
        (tmp_path / "video.mp4").write_bytes(b"\x00" * 100)
        (tmp_path / "video.mkv").write_bytes(b"\x00" * 100)

        handler = RefreshHandler(repo=repo)
        result = await handler.handle(RefreshPayload(library_id=1, path=str(tmp_path), patterns=["*.mp4"]))

        assert result.success is True
        assert result.result is not None
        assert result.result.added == 1
        assert result.result.removed == 0
        assert result.result.scrape == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_blacklisted_files_skipped_and_records_removed(self, repo: Repository, tmp_path):
        """黑名单命中的文件不进扫描; ScanMode.remove 时旧记录被当作失效清除."""
        lib = await repo.create_library(name="t", path=str(tmp_path), blacklist_patterns=["广告", "(?i)ads"])
        assert lib.id is not None
        ad = tmp_path / "新片广告.mp4"
        ad.write_bytes(b"\x00" * 100)
        (tmp_path / "MIDV-123.mp4").write_bytes(b"\x00" * 100)
        old = await repo.create_media_file(lib.id, path=str(ad), status=MediaFileStatus.SCRAPED, metadata_id=42)
        assert old.id is not None

        handler = RefreshHandler(repo=repo)
        result = await handler.handle(
            RefreshPayload(
                library_id=lib.id,
                path=str(tmp_path),
                scan={ScanMode.add, ScanMode.remove},
                scrape=set(),
            )
        )

        assert result.success is True
        assert result.result is not None
        assert result.result.added == 1
        assert result.result.removed == 1
        # 旧记录已被清除; 注意 id 会被 SQLite 复用, 断言用路径而不是 id
        assert await repo.get_media_file_by_path(str(ad)) is None
        assert await repo.get_media_file_by_path(str(tmp_path / "MIDV-123.mp4")) is not None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_undersized_videos_skipped_and_records_removed(self, repo: Repository, tmp_path):
        """低于 min_file_size 的视频不进扫描; nfo 即使很小也不当视频过滤; ScanMode.remove 清旧记录."""
        lib = await repo.create_library(name="t", path=str(tmp_path), min_file_size=50)
        assert lib.id is not None
        ad = tmp_path / "ad.mp4"
        ad.write_bytes(b"tiny")
        (tmp_path / "MIDV-123.mp4").write_bytes(b"x" * 100)
        (tmp_path / "note.nfo").write_bytes(b"nfo")
        old = await repo.create_media_file(lib.id, path=str(ad), status=MediaFileStatus.SCRAPED, metadata_id=42)
        assert old.id is not None

        handler = RefreshHandler(repo=repo)
        result = await handler.handle(
            RefreshPayload(
                library_id=lib.id,
                path=str(tmp_path),
                scan={ScanMode.add, ScanMode.remove},
                scrape=set(),
            )
        )

        assert result.success is True
        assert result.result is not None
        assert result.result.added == 1
        assert result.result.removed == 1
        assert await repo.get_media_file_by_path(str(ad)) is None
        assert await repo.get_media_file_by_path(str(tmp_path / "MIDV-123.mp4")) is not None
        assert await repo.get_media_file_by_path(str(tmp_path / "note.nfo")) is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_remove_only_checks_db_paths(self, repo: Repository, tmp_path):
        """仅 remove 不对整树 glob, 对库内路径 exists: 磁盘上没有的记录删掉, 还在的保留."""
        lib = await repo.create_library(name="t", path=str(tmp_path))
        assert lib.id is not None
        keep = tmp_path / "MIDV-123.mp4"
        keep.write_bytes(b"\x00" * 100)
        await repo.create_media_file(lib.id, path=str(keep))
        gone = await repo.create_media_file(lib.id, path=str(tmp_path / "missing.mp4"))
        assert gone.id is not None

        result = await RefreshHandler(repo=repo).handle(
            RefreshPayload(library_id=lib.id, path=str(tmp_path), scan={ScanMode.remove}, scrape=set())
        )

        assert result.success is True
        assert result.result is not None
        assert result.result.added == 0
        assert result.result.removed == 1
        assert await repo.get_media_file_by_path(str(keep)) is not None
        assert await repo.get_media_file_by_path(str(tmp_path / "missing.mp4")) is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_remove_only_keeps_nfd_file_indexed_as_nfc(self, repo: Repository, tmp_path):
        """仅 remove 用库内 NFC 判断存在时, 磁盘 NFD 文件不得当作缺失."""
        nfd = tmp_path / "\u3057\u3099.mp4"
        nfd.write_bytes(b"\x00" * 100)
        lib = await repo.create_library(name="t", path=str(tmp_path))
        assert lib.id is not None
        media = await repo.create_media_file(lib.id, path=str(nfd))
        assert media.id is not None
        assert media.path == str(tmp_path / "\u3058.mp4")

        result = await RefreshHandler(repo=repo).handle(
            RefreshPayload(library_id=lib.id, path=str(tmp_path), scan={ScanMode.remove}, scrape=set())
        )
        assert result.success is True
        assert result.result is not None
        assert result.result.removed == 0
        assert await repo.get_media_file(media.id) is not None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_scan_nfc_identity_does_not_add_or_remove(
        self, repo: Repository, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        """目录列出 NFD、库内已是 NFC 时, add+remove 不新增也不删除."""
        nfc = "\u3058"
        nfd = "\u3057\u3099"
        lib = await repo.create_library(name="t", path=str(tmp_path))
        assert lib.id is not None
        stored = tmp_path / f"{nfc}.mp4"
        stored.write_bytes(b"\x00" * 100)
        media = await repo.create_media_file(lib.id, path=str(stored))
        assert media.path == str(stored)

        listed = tmp_path / f"{nfd}.mp4"
        library_id = lib.id
        assert library_id is not None

        async def fake_scan(*_args: object, **_kwargs: object) -> CleanupInventory:
            return CleanupInventory(
                inventory_id="test",
                library_id=library_id,
                root=tmp_path,
                scope_path=None,
                recursive=True,
                patterns=(),
                source=InventorySource.RULES,
                created_at=datetime.now(UTC),
                media_hits=[LibraryHit(listed, LibraryFileKind.MEDIA)],
            )

        monkeypatch.setattr("amane.handlers.refresh.scan_inventory", fake_scan)
        result = await RefreshHandler(repo=repo).handle(
            RefreshPayload(
                library_id=lib.id,
                path=str(tmp_path),
                scan={ScanMode.add, ScanMode.remove},
                scrape=set(),
            )
        )
        assert result.success is True
        assert result.result is not None
        assert result.result.added == 0
        assert result.result.removed == 0
        files = await repo.list_media_files(library_id=lib.id, limit=None)
        assert [f.id for f in files] == [media.id]
        assert files[0].path == str(stored)

    @pytest.mark.parametrize(
        ("scan_modes", "scrape_statuses", "expected_scrape_tasks"),
        [
            ({ScanMode.add}, set(), 0),
            ({ScanMode.add}, {MediaFileStatus.PENDING}, 1),
        ],
    )
    @pytest.mark.asyncio(loop_scope="function")
    async def test_scan_mode_controls_scrape(
        self,
        repo: Repository,
        tmp_path,
        scan_modes: set[ScanMode],
        scrape_statuses: set[MediaFileStatus],
        expected_scrape_tasks: int,
    ):
        """scan + scrape 决定是否注册文件、是否派生 SCRAPE; REFRESH 不整理文件."""
        (tmp_path / "MIDV-123.mp4").write_bytes(b"\x00" * 100)

        handler = RefreshHandler(repo=repo)
        result = await handler.handle(
            RefreshPayload(
                library_id=1,
                path=str(tmp_path),
                scan=scan_modes,
                scrape=scrape_statuses,
            )
        )

        assert result.success is True
        assert result.result is not None
        assert result.result.added == 1
        assert result.result.scrape == expected_scrape_tasks

        scrapes = [f for f in (result.followups or []) if f.task_type == TaskType.SCRAPE]
        assert len(scrapes) == expected_scrape_tasks

    @pytest.mark.asyncio(loop_scope="function")
    async def test_invalid_directory_fails(self, repo: Repository, tmp_path):
        """不存在的目录返回失败"""
        handler = RefreshHandler(repo=repo)
        result = await handler.handle(RefreshPayload(library_id=1, path=str(tmp_path / "nonexistent")))

        assert result.success is False
        assert result.error is not None
        assert "不是目录" in result.error

    @pytest.mark.asyncio(loop_scope="function")
    async def test_submits_scrape_tasks_with_correct_payload(self, repo: Repository, tmp_path):
        """验证派生 SCRAPE followup payload 正确 (use_cache 透传)"""
        (tmp_path / "MIDV-123.mp4").write_bytes(b"\x00" * 100)

        handler = RefreshHandler(repo=repo)
        result = await handler.handle(RefreshPayload(library_id=1, path=str(tmp_path), use_cache=set()))

        scrapes = [f for f in (result.followups or []) if f.task_type == TaskType.SCRAPE]
        assert len(scrapes) == 1
        assert scrapes[0].payload["number"] == "MIDV-123"
        assert scrapes[0].payload["use_cache"] == []  # 空集透传 = 全部强制刷新
        assert scrapes[0].key == f"scrape:{scrapes[0].payload['media_file_id']}"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_scrape_fanout_not_capped_by_list_page_size(self, repo: Repository, tmp_path):
        """list_media_files 默认 50 是 GET /media 分页; REFRESH fan-out 必须全量入队."""
        n = 51
        for i in range(n):
            await repo.create_media_file(
                library_id=1, path=str(tmp_path / f"MIDV-{i:03d}.mp4"), status=MediaFileStatus.PENDING
            )

        handler = RefreshHandler(repo=repo)
        result = await handler.handle(
            RefreshPayload(library_id=1, path=str(tmp_path), scan=set(), scrape={MediaFileStatus.PENDING})
        )

        assert result.success is True
        assert result.result is not None
        assert result.result.scrape == n
        scrapes = [f for f in (result.followups or []) if f.task_type == TaskType.SCRAPE]
        assert len(scrapes) == n


def test_prioritize_source_urls_follows_route_order() -> None:
    """非手动刮削也按类型路由顺序排来源链接.

    聚合写入是「先到先得」, 顺序取决于各站响应快慢; 用户改了路由顺序时不跟着变,
    界面看起来就像没生效.
    """
    from amane.handlers.scrape import _prioritize_source_urls

    src = {"javdb": "u1", "avbase": "u2", "javbus": "u3"}

    out = _prioritize_source_urls(src, {}, manual=False, route=["avbase", "javbus", "javdb"])
    assert list(out) == ["avbase", "javbus", "javdb"]

    # 路由里没有的站点接在末尾, 彼此保持原相对顺序.
    partial = _prioritize_source_urls(src, {}, manual=False, route=["avbase"])
    assert list(partial) == ["avbase", "javdb", "javbus"]


def test_prioritize_source_urls_manual_preferred_wins() -> None:
    """手动搜索仍把选中的那一条提到最前, 哪怕路由把它排在后面."""
    from amane.handlers.scrape import _prioritize_source_urls

    src = {"javdb": "u1", "avbase": "u2"}
    out = _prioritize_source_urls(src, {}, manual=True, preferred_site="javdb", route=["avbase", "javdb"])
    assert list(out) == ["javdb", "avbase"]


class TestGenericFallback:
    """站点没有专属爬虫时, 用通用解析器把页面读一遍."""

    class _GenericCrawler(Crawler):
        @classmethod
        def profile(cls) -> CrawlerProfile:
            return CrawlerProfile(name="generic", base_url="")

        async def _search(self, query, options=None):
            return None

        async def _scrape(self, url, options=None) -> MediaMetadata | None:
            return MediaMetadata.model_validate(
                {"number": "020616_348", "title": "From generic", "source_url": url}
            )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_url_without_dedicated_crawler_falls_back_to_generic(
        self, repo: Repository, resource_store
    ):
        """域名没有专属爬虫时, 通用解析照样能读出来.

        以前这种地址直接返回空, 用户看到「各来源都没有找到结果」;
        而地址末段往往就是番号, 值得读一遍.
        """
        h = ScrapeHandler(
            repo=repo,
            factory=FakeFactory({"javdb": object()}, generic=self._GenericCrawler(client=None)),
            resource_store=resource_store,
            pipeline_config=HotSettings(),
        )

        items = await h.preview("https://www.muramura.tv/movies/020616_348/", ContentType.CENSORED)

        assert items, "通用解析没有产出任何结果"
        assert items[0]["site"] == "generic"
        assert items[0]["number"] == "020616_348"


def test_owns_host_accepts_builtin_domain_after_custom_base_url() -> None:
    """配了自定义域名后, 官方域名的链接仍归这个站.

    只比当前 base_url 会让官方域名认不出来, 链接落到通用解析里读出一堆无意义标题.
    """

    class _CustomDomainCrawler(Crawler):
        @classmethod
        def profile(cls) -> CrawlerProfile:
            return CrawlerProfile(name=SiteName.JAVBUS, base_url="https://www.javbus.com")

        async def _search(self, query, options=None):
            return None

        async def _scrape(self, url, options=None):
            return None

    crawler = _CustomDomainCrawler(client=None)
    # 模拟站点配置把 base_url 改成了自定义域名.
    crawler.base_url = "https://mirror.example.com"

    assert ScrapeHandler._owns_host(crawler, "www.javbus.com"), "官方域名应当仍被认领"
    assert ScrapeHandler._owns_host(crawler, "mirror.example.com"), "自定义域名应当被认领"
    assert not ScrapeHandler._owns_host(crawler, "www.other.com")
