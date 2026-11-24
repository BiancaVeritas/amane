"""临时任务: 只重取时长, 不动其它字段.

**这是过渡代码.** 存量条目的 ``runtime_seconds`` 为空 (该列后加), 而重跑完整 SCRAPE 会
把用户手工改过的标题 / 标签 / 演员一并覆盖. 这个任务只问各来源要时长, 写回 ``runtime``
与 ``runtime_seconds`` 两列, 其余字段原样不动.

取时长的顺序:

1. **该条目已记的来源页地址** —— 直接打开那一页读时长. 地址就在 ``Metadata.source_urls``
   里, 不必让每个站点按番号重搜一遍: 那些搜索既多花几秒, 又可能搜到别的片.
2. 没有来源地址时才退回按番号问该类型路由的各站点.

存量补齐后可以整套删掉: ``TaskType.RESCRAPE_RUNTIME`` 枚举项、本文件、路由注册与前端下拉.
"""

import asyncio
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import structlog

from ..aggregate import CrawlerLike
from ..crawlers.base import Crawler
from ..crawlers.models import SearchQuery
from ..crawlers.site_roles import FILM_METADATA_SITES
from ..parsing import ContentType
from .models import RescrapeRuntimePayload, RescrapeRuntimeResult
from .protocol import TaskHandler, TaskResult

if TYPE_CHECKING:
    from ..config import HotSettings
    from ..db.models import Metadata
    from ..db.repository import Repository
    from .scrape import CrawlerFactoryLike

logger = structlog.get_logger()


class RescrapeRuntimeHandler(TaskHandler[RescrapeRuntimePayload, RescrapeRuntimeResult]):
    """按来源页地址 (或番号) 要时长; 只写时长两列."""

    def __init__(self, repo: Repository, factory: CrawlerFactoryLike, pipeline_config: HotSettings) -> None:
        super().__init__(payload_t=RescrapeRuntimePayload, result_t=RescrapeRuntimeResult)
        self._repo = repo
        self._factory = factory
        self._config = pipeline_config

    async def handle(self, payload: RescrapeRuntimePayload) -> TaskResult[RescrapeRuntimeResult]:
        metadata = await self._repo.get_metadata(payload.metadata_id) if payload.metadata_id is not None else None
        if metadata is None and payload.number:
            metadata = await self._repo.get_metadata_by_number(payload.number, content_type=payload.content_type)
        if metadata is None:
            return TaskResult(success=False, error=f"条目不存在: {payload.number or payload.metadata_id}")

        seconds = await self._fetch_seconds(metadata)
        if seconds is None:
            return TaskResult(
                success=False,
                error="各来源都没有给出时长",
                result=RescrapeRuntimeResult(number=metadata.number),
            )

        # 只传时长字段: upsert 只写传入的列, 其余字段不会被动到.
        await self._repo.upsert_metadata(
            metadata.number,
            content_type=metadata.content_type,
            runtime_seconds=seconds,
        )
        logger.info("runtime refreshed", number=metadata.number, seconds=seconds)
        return TaskResult(
            success=True,
            result=RescrapeRuntimeResult(number=metadata.number, runtime_seconds=seconds, updated=True),
        )

    async def _fetch_seconds(self, metadata: Metadata) -> int | None:
        """先按已记的来源地址抓, 抓不到再按番号问各站点."""
        from_source = await self._seconds_from_sources(metadata)
        if from_source is not None:
            return from_source
        return await self._seconds_from_search(metadata.number, metadata.content_type)

    async def _seconds_from_sources(self, metadata: Metadata) -> int | None:
        """直接打开 ``source_urls`` 里的地址读时长.

        一条条目可能记着多个来源页, 并发都试一遍 —— 它们互不依赖, 串行只会叠加等待.
        """
        urls = [url for url in (metadata.source_urls or {}).values() if url]
        if not urls:
            return None
        names: list[str] = [*(self._config.scraping.content_routes.get(metadata.content_type) or [])]
        names.extend(str(site) for site in FILM_METADATA_SITES)
        crawlers = await self._factory.get_crawlers(dict.fromkeys(names))
        if not crawlers:
            return None

        async def one(url: str) -> int | None:
            return _seconds_of(await _scrape_url(url, crawlers))

        results = await asyncio.gather(*(one(url) for url in urls))
        return next((value for value in results if value), None)

    async def _seconds_from_search(self, number: str, content_type: ContentType) -> int | None:
        """没有来源地址时的退路: 并发问该类型的各来源.

        站点只给分钟时按分钟折成秒 —— 那些站的原文里就没有秒, 折出来的秒是整分钟.
        """
        route = list(self._config.scraping.content_routes.get(content_type) or [])
        if not route:
            return None
        crawlers = await self._factory.get_crawlers(route)
        query = SearchQuery(number, None, None, content_type)

        async def one(crawler: CrawlerLike) -> int | None:
            try:
                return _seconds_of(await crawler.fetch(query))
            except Exception:
                return None

        results = await asyncio.gather(*(one(crawler) for crawler in crawlers.values()))
        return next((value for value in results if value), None)


def _same_site(host: str, base_host: str) -> bool:
    """两个主机是否属于同一站点: 相等, 或一方是另一方的子域 (与 ScrapeHandler 同一判据)."""
    host = host.removeprefix("www.")
    base_host = base_host.removeprefix("www.")
    return host == base_host or host.endswith(f".{base_host}") or base_host.endswith(f".{host}")


def _owns_host(crawler: Crawler, host: str) -> bool:
    """认当前 base_url 与内置域名: 配了自定义域名后, 官方域名的链接仍该被认领."""
    current = (urlsplit(crawler.base_url).hostname or "").casefold()
    if current and _same_site(host, current):
        return True
    builtin = (urlsplit(crawler.profile().base_url).hostname or "").casefold()
    return bool(builtin) and _same_site(host, builtin)


async def _scrape_url(url: str, crawlers: dict[str, CrawlerLike]) -> object | None:
    """按域名找认识这个地址的那一家, 直接抓这一页."""
    host = (urlsplit(url).hostname or "").casefold()
    if not host:
        return None
    for crawler in crawlers.values():
        if not isinstance(crawler, Crawler) or not _owns_host(crawler, host):
            continue
        try:
            return await crawler.scrape_url(url)
        except Exception:
            continue
    return None


def _seconds_of(found: object) -> int | None:
    """从抓取结果里取时长; 只有分钟的站点按分钟折成秒."""
    if found is None:
        return None
    seconds = getattr(found, "runtime_seconds", None)
    if isinstance(seconds, int) and seconds > 0:
        return seconds
    minutes = getattr(found, "runtime", None)
    if isinstance(minutes, int) and minutes > 0:
        return minutes * 60
    return None
