from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from unicodedata import category as unicode_category
from urllib.parse import urlsplit

from structlog.contextvars import bind_contextvars

from ..aggregate import (
    SCALAR_FIELDS,
    AggregatedMetadata,
    CrawlerLike,
    FieldLanguage,
    aggregate,
    aggregate_single,
    compile_priority,
)
from ..crawlers.base import Crawler
from ..crawlers.models import MediaMetadata, SearchQuery
from ..crawlers.site_roles import FILM_METADATA_SITES, builtin_descriptors
from ..db.models import MediaFileStatus, MediaIdentityType, TaskType
from ..db.repo_types import WriteMode
from ..enums import ActorGender, MetadataField, SiteName
from ..media import materialize_images
from ..media.crop_rules import find_rule
from ..observability import current
from ..observability.models import SiteOutcomeKind
from ..parsing import ContentType, parse_file_info
from ..plugins.models import SourceDescriptor, SourceTrait
from ..utils.litepan_strm import read_litepan_115_file_id
from ..utils.path import path_is_under
from ._common import ensure_oshash, finalize_media_file
from .models import ActorScrapePayload, CacheKind, OrganizePayload, ScrapePayload, ScrapeResult
from .protocol import FollowupTask, TaskHandler, TaskResult

if TYPE_CHECKING:
    from ..config import HotSettings
    from ..db.models import MediaFile
    from ..db.repository import Repository
    from ..llm import Translator
    from ..media import ResourceStore
    from ..net.http import WebClient
    from ..tags.mapping import TagMappingStore

# 进度: 聚合按已满足标量字段计数; 其后固定两步 (物化图片 / 持久化).
_PROGRESS_POST_STEPS = 2


def _prepare_image_content_type(content_type: ContentType, metadata: AggregatedMetadata) -> ContentType:
    """无码仅在同一个来源明确提供原海报与双页封面时走日本双页裁剪流程."""
    if content_type != ContentType.UNCENSORED:
        return content_type

    if (
        metadata.double_page_poster_urls
        and metadata.double_page_original_poster_urls
        and metadata.double_page_thumb_urls
    ):
        # 强制使用同一个来源的成对图片, 防止聚合后的跨来源 URL 错配.
        metadata.poster_urls = list(metadata.double_page_poster_urls)
        metadata.original_poster_urls = list(metadata.double_page_original_poster_urls)
        metadata.thumb_urls = list(metadata.double_page_thumb_urls)
        return ContentType.UNCENSORED

    # 没有同源的完整双页组合, 一律按单图处理. poster_urls 可能只是
    # thumb 的派生尺寸, 不能单独作为双页结构证据.
    cover_urls = list(metadata.thumb_urls or metadata.poster_urls)
    if cover_urls:
        metadata.poster_urls = list(cover_urls)
        metadata.thumb_urls = list(cover_urls)
        metadata.original_poster_urls = list(cover_urls)
    else:
        metadata.original_poster_urls = []
    return ContentType.WESTERN


class CrawlerFactoryLike(Protocol):
    async def get_crawlers(self, names: Iterable[str]) -> dict[str, CrawlerLike]: ...


def _searchable_title(title: str | None) -> str | None:
    """去掉标题里的标点, 留作搜索词.

    站点标题常带 ``【】``、``！``、``・`` 这类装饰, 原样拿去搜别的站几乎搜不到;
    去掉标点后主体词相同, 命中率高得多. 空白折叠成一个, 避免多段拼接出怪词.
    """
    if not title:
        return None
    cleaned = "".join(" " if ch.isspace() else ch for ch in title if not unicode_category(ch).startswith("P"))
    collapsed = " ".join(cleaned.split())
    return collapsed or None


def _preview_item(site: str, meta: MediaMetadata) -> dict:
    """预览项: 只带界面挑选用得上的字段."""
    return {
        "site": site,
        "number": meta.number,
        "title": meta.title,
        "release": meta.release,
        "studio": meta.studio,
        "poster_url": meta.poster_urls[0] if meta.poster_urls else None,
        "thumb_url": meta.thumb_urls[0] if meta.thumb_urls else None,
        "source_url": meta.source_url,
    }


def _same_site(host: str, base_host: str) -> bool:
    """两个主机是否属于同一站点: 相等, 或一方是另一方的子域.

    站点的 base_url 常带 ``www.`` 而用户粘的地址不一定带; 反过来 my.tokyo-hot.com
    这类子域也可能是唯一入口, 所以按后缀匹配而不要求逐字相等.
    """
    host = host.removeprefix("www.")
    base_host = base_host.removeprefix("www.")
    return host == base_host or host.endswith(f".{base_host}") or base_host.endswith(f".{host}")


def _prioritize_source_urls(
    source_urls: dict[str, str],
    field_sources: dict[str, str],
    *,
    manual: bool,
    preferred_site: str | None = None,
    route: Sequence[SiteName | str] | None = None,
) -> dict[str, str]:
    """按当前类型路由的顺序排列来源链接.

    来源列表按 dict 插入顺序展示, 而聚合写入是「先到先得」—— 谁先返回结果谁排前面,
    于是顺序取决于各站响应快慢, 改了路由顺序也不变. 给了 ``route`` 就按路由排,
    路由里没有的站点接在末尾 (保持它们之间的相对顺序).

    手动搜索仍优先把它选中的那一条提到最前: 用户已经挑了具体来源, 那条就该在最前.
    """
    if not source_urls:
        return source_urls
    if manual and preferred_site and preferred_site in source_urls:
        first = {preferred_site: source_urls[preferred_site]}
        return {**first, **{k: v for k, v in source_urls.items() if k != preferred_site}}
    if route:
        order = {str(site): idx for idx, site in enumerate(route)}
        known = sorted(
            (site for site in source_urls if site in order),
            key=lambda site: order[site],
        )
        unknown = [site for site in source_urls if site not in order]
        return {site: source_urls[site] for site in [*known, *unknown]}
    if not manual:
        return source_urls
    used = {site for site in field_sources.values() if site}
    if not used:
        return source_urls
    first = {site: url for site, url in source_urls.items() if site in used}
    rest = {site: url for site, url in source_urls.items() if site not in used}
    return {**first, **rest}


def _append_new_sources(existing: dict[str, str], fresh: dict[str, str]) -> dict[str, str]:
    """把 ``fresh`` 里既有列表没有的站点追加到 ``existing`` 末尾.

    已有站点的地址保持原位 (哪怕 ``fresh`` 给了新地址): 「添加来源链接」只补链接,
    不改既有顺序, 也不让这次抓到的地址顶掉已记录的来源.
    """
    merged = dict(existing)
    for site, url in fresh.items():
        merged.setdefault(site, url)
    return merged


class ScrapeHandler(TaskHandler[ScrapePayload, ScrapeResult]):
    """不移动库内文件; 库目录落盘由 ORGANIZE 负责."""

    def __init__(
        self,
        repo: Repository,
        factory: CrawlerFactoryLike,
        resource_store: ResourceStore,
        pipeline_config: HotSettings,
        web_client: WebClient | None = None,
        translator: Translator | None = None,
        source_catalog: Sequence[SourceDescriptor] | None = None,
        tag_mappings: TagMappingStore | None = None,
    ):
        super().__init__(payload_t=ScrapePayload, result_t=ScrapeResult)
        self._repo = repo
        self._factory = factory
        self._config = pipeline_config
        self._web_client = web_client
        self._resource_store = resource_store
        self._translator = translator
        self._tag_mappings = tag_mappings
        # 调度事实只从来源目录读取: 内置来源的 descriptor 由 profile() 合成, 插件来源由插件声明.
        catalog = source_catalog if source_catalog is not None else builtin_descriptors()
        self._multi_language_sources = frozenset(d.id for d in catalog if SourceTrait.MULTI_LANGUAGE in d.traits)
        self._partial_sources = frozenset(d.id for d in catalog if SourceTrait.NEEDS_PARTIAL in d.traits)
        self._file_hash_sources = frozenset(d.id for d in catalog if SourceTrait.USES_FILE_HASH in d.traits)

    async def preview(self, input_text: str, content_type: ContentType) -> list[dict]:
        """只抓不写: 把命中项原样交回调用方.

        手动搜索要让用户先挑再落库, 所以这里不碰 DB / 文件; 数据交回 UI 由用户决定.
        输入可以是番号, 也可以是来源页地址 —— 后者先打开页面读出番号, 再按番号问各来源:
        地址里往往只有站点内部 ID (javdb 的 ``/v/dgVq9``), 拿它当番号哪个来源都搜不到.
        """
        url = input_text.strip()
        if url.startswith(("http://", "https://")):
            return await self._preview_url(url, content_type)
        return await self._preview_number(url, content_type)

    async def _preview_number(self, term: str, content_type: ContentType) -> list[dict]:
        """拿一个搜索词问当前类型路由里的每个来源.

        ``term`` 通常是番号; 番号对不上时也接受去标点的标题 —— 各站自己的搜索逻辑决定能否命中.
        """
        route = self._config.scraping.content_routes.get(content_type)
        if not route:
            return []
        crawlers = await self._factory.get_crawlers(route)
        if not crawlers:
            return []
        q = SearchQuery(term, None, None, content_type)
        out: list[dict] = []
        for name, crawler in crawlers.items():
            try:
                meta = await crawler.fetch(q)
            except Exception:
                continue
            if meta is None:
                continue
            out.append(_preview_item(name, meta))
        return out

    async def _preview_url(self, url: str, content_type: ContentType) -> list[dict]:
        """打开地址读出番号/标题, 再拿它们问当前类型路由里的其余来源.

        找地址属于哪一家用与「添加来源链接」同一套三级查找; 之后按番号搜, 没有番号就用
        去标点的标题搜 —— 贴进来的页面往往只有一个站认识, 用户要的却是各站的对照.
        """
        route = self._config.scraping.content_routes.get(content_type) or []
        crawlers = await self._factory.get_crawlers(route)
        found = await self._scrape_one_source(url, crawlers, content_type, any_site=True)
        if found is None:
            return []
        name, meta = found
        # 地址那一家的结果先放进去, 它是用户亲手贴的, 最可能是想要的.
        out = [_preview_item(name, meta)]
        seen = {name}
        # 番号为主, 标题只兜底: 有番号时各站都能按番号精确命中, 再按标题搜一轮只会多出
        # 一堆噪声命中 (标题里的通用词能匹配到别的片).
        term = meta.number.strip() or _searchable_title(meta.title)
        # 只有当前类型的路由: 拿别类型的站去搜会混进别的类型的结果.
        if term:
            for item in await self._preview_number(term, content_type):
                if item["site"] not in seen:
                    seen.add(item["site"])
                    out.append(item)
        return out

    async def _scrape_one_source(
        self,
        url: str,
        crawlers: Mapping[str, CrawlerLike],
        content_type: ContentType,
        *,
        any_site: bool = False,
    ) -> tuple[str, MediaMetadata] | None:
        """按域名找到能解析这个地址的那一家.

        查找顺序 (``any_site`` 时逐级退让):
        1. 本次类型路由内的来源 —— 用户给的类型决定了哪些站可比;
        2. 全部类型路由的并集 —— 别处配置过的站同样能解析这个域名;
        3. 兜底解析器 —— 地址属于哪个站都不会被路由配置挡住.
        """
        host = (urlsplit(url).hostname or "").casefold()
        if not host:
            return None
        found = await self._first_matching_crawler(url, host, crawlers)
        if found is not None or not any_site:
            return found
        # 第二级: 所有路由里配置过的站. 只在当前类型里找不到时才查, 免得本该按当前类型判定
        # 的地址被别的类型抢走.
        configured = {str(site) for route in self._config.scraping.content_routes.values() for site in route}
        if configured:
            known = await self._factory.get_crawlers(sorted(configured))
            found = await self._first_matching_crawler(url, host, known)
            if found is not None:
                return found
        # 第三级: 兜底解析器, 与路由无关.
        everything = await self._factory.get_crawlers([str(site) for site in FILM_METADATA_SITES])
        return await self._first_matching_crawler(url, host, everything)

    async def _first_matching_crawler(
        self, url: str, host: str, crawlers: Mapping[str, CrawlerLike]
    ) -> tuple[str, MediaMetadata] | None:
        for name, crawler in crawlers.items():
            if not isinstance(crawler, Crawler):
                continue
            if not _same_site(host, urlsplit(crawler.base_url).hostname or ""):
                continue
            try:
                meta = await crawler.scrape_url(url)
            except Exception:
                continue
            if meta is not None:
                return name, meta
        return None

    async def handle(self, payload: ScrapePayload) -> TaskResult[ScrapeResult]:
        bind_contextvars(number=payload.number)

        content_type = payload.content_type
        progress_total = len(SCALAR_FIELDS) + _PROGRESS_POST_STEPS
        rec = current()
        if payload.theporndb_uuid and content_type != ContentType.WESTERN:
            return TaskResult(success=False, error="ThePornDB UUID 只能用于 WESTERN 刮削")

        identity_metadata = None
        if payload.metadata_id is not None:
            identity_metadata = await self._repo.get_metadata(payload.metadata_id)
            if identity_metadata is None:
                return TaskResult(success=False, error=f"Metadata {payload.metadata_id} 不存在")
            if identity_metadata.content_type != content_type:
                return TaskResult(success=False, error="重新刮削的内容类型与原 Metadata 不一致")
            stored_theporndb_uuid = identity_metadata.theporndb_uuid
            if (
                payload.theporndb_uuid
                and stored_theporndb_uuid
                and payload.theporndb_uuid.casefold() != stored_theporndb_uuid.casefold()
            ):
                return TaskResult(success=False, error="手动指定的 ThePornDB UUID 与目标 Metadata 的身份不一致")

        # 当前文件先读取出来; WESTERN 的缓存身份不能仅凭番号确定.
        file = None
        if payload.media_file_id:
            file = await self._repo.get_media_file(media_id=payload.media_file_id)

        # 校验 content_type 路由; 无资格站点则失败.
        route = self._config.scraping.content_routes.get(content_type)
        if not route:
            rec.warning("no eligible crawlers for content type", content_type=content_type)
            return TaskResult(success=False, error=f"内容类型 {content_type} 没有可用来源")
        if content_type == ContentType.WESTERN and str(SiteName.THEPORNDB) not in {str(site) for site in route}:
            return TaskResult(
                success=False, error="WESTERN 作品身份依赖 ThePornDB UUID，请在欧美来源路由中启用 ThePornDB"
            )
        if (
            content_type == ContentType.WESTERN
            and file is None
            and not payload.theporndb_uuid
            and payload.metadata_id is None
        ):
            return TaskResult(success=False, error="WESTERN 刮削需要媒体文件 OSHASH 或手动指定 ThePornDB UUID")
        rec.info("scraping started", content_type=str(content_type), crawlers=route)
        rec.update_summary(eligible_sites=[str(s) for s in route])

        crawlers = await self._factory.get_crawlers(route)
        if not crawlers:
            current().warning("no crawlers available", requested=route)
            return TaskResult(success=False, error=f"番号 {payload.number} 没有可用来源")

        # LitePan STRM 的 115 原生文件 ID 与 OSHASH 是两种独立身份。
        file_115_id = None
        if payload.content_type == ContentType.WESTERN and file is not None:
            file_115_id = await read_litepan_115_file_id(Path(file.path))
            if file_115_id:
                file_115_metadata = await self._repo.get_metadata_by_identity(
                    MediaIdentityType.FILE_115_ID, file_115_id
                )
                if file_115_metadata is not None:
                    if identity_metadata is not None and identity_metadata.id != file_115_metadata.id:
                        return TaskResult(
                            success=False,
                            error="当前 LitePan 115 文件 ID 已关联到另一条 WESTERN 作品，拒绝跨作品关联",
                        )
                    identity_metadata = identity_metadata or file_115_metadata

        # 仅当本次可用来源声明需要指纹时计算 oshash.
        file_hash = file.oshash if file else None
        if file is not None and file_hash is None and any(name in self._file_hash_sources for name in crawlers):
            file_hash = await ensure_oshash(self._repo, file)

        # 重新刮削始终取得一个已关联文件的 OSHASH (即使 Metadata 已有 ThePornDB UUID).
        # 只取一个指纹, 不批量提交多个文件 hash.
        if payload.content_type == ContentType.WESTERN and payload.metadata_id is not None and file_hash is None:
            linked_files = await self._repo.get_media_by_metadata_id(payload.metadata_id)
            for linked_file in linked_files:
                linked_hash = linked_file.oshash or await ensure_oshash(self._repo, linked_file)
                if linked_hash:
                    file_hash = linked_hash
                    if file is None:
                        file = linked_file
                    break

        # OSHASH → Metadata 关系来自已入库 MediaFile.metadata_id; 不受 raw-cache 开关影响.
        oshash_matches = (
            await self._repo.get_metadata_by_oshash_matches(file_hash)
            if payload.content_type == ContentType.WESTERN and file_hash
            else []
        )
        if len(oshash_matches) > 1:
            return TaskResult(
                success=False, error="当前 OSHASH 已关联到多条 WESTERN Metadata，存在身份冲突，拒绝自动刮削"
            )
        oshash_metadata = oshash_matches[0] if oshash_matches else None
        if (
            payload.content_type == ContentType.WESTERN
            and identity_metadata is not None
            and oshash_metadata is not None
            and oshash_metadata.id != identity_metadata.id
        ):
            return TaskResult(success=False, error="重新刮削所选 OSHASH 不属于目标 Metadata，拒绝跨作品关联")

        # WESTERN raw 只能在番号一致且当前 OSHASH 已关联到目标 Metadata 时复用.
        # metadata_id / ThePornDB UUID 本身是作品身份, 但不能证明 raw 快照对应当前文件.
        # 手动刮削要覆盖元数据, 因此不复用 raw 快照: 缓存命中会让这次「刮」变成「读上次的旧值」,
        # 用户点完看不出任何变化. 译文缓存仍按 use_cache 走, 它不影响字段真值.
        use_metadata_cache = CacheKind.metadata in payload.use_cache and not payload.manual
        db_data = None
        if use_metadata_cache:
            if payload.content_type == ContentType.WESTERN:
                candidate = identity_metadata or oshash_metadata
                if (
                    candidate is not None
                    and candidate.id is not None
                    and candidate.number.casefold() == payload.number.casefold()
                    and oshash_metadata is not None
                    and oshash_metadata.id == candidate.id
                ):
                    db_data = candidate
            elif identity_metadata is not None:
                db_data = identity_metadata
            elif payload.theporndb_uuid:
                db_data = await self._repo.get_metadata_by_theporndb_uuid(payload.theporndb_uuid)
            else:
                db_data = await self._repo.get_metadata_by_number(payload.number, content_type=payload.content_type)
        if db_data is not None and db_data.raw:
            rec.write_raw_cache(db_data.raw)

        theporndb_uuid = payload.theporndb_uuid
        if theporndb_uuid is None and identity_metadata is not None:
            theporndb_uuid = identity_metadata.theporndb_uuid
        if theporndb_uuid is None and oshash_metadata is not None:
            theporndb_uuid = oshash_metadata.theporndb_uuid
        if theporndb_uuid is None and db_data is not None:
            theporndb_uuid = db_data.theporndb_uuid
        linked_theporndb_uuid = oshash_metadata.theporndb_uuid if oshash_metadata is not None else None
        if theporndb_uuid and linked_theporndb_uuid and theporndb_uuid.casefold() != linked_theporndb_uuid.casefold():
            return TaskResult(
                success=False, error="当前文件 OSHASH 已关联到另一条 WESTERN 作品, 拒绝跨 ThePornDB UUID 绑定"
            )
        q = SearchQuery(
            payload.number,
            file.path if file else None,
            file_hash,
            payload.content_type,
            theporndb_uuid=theporndb_uuid,
        )

        field_priority = compile_priority(
            route, self._config.scraping.field_priority, self._config.scraping.field_blacklist
        )
        field_language = self._config.scraping.field_language

        await self.report_progress(0, progress_total, "fetch")

        async def _on_fetch_progress(current: int, _total: int, message: str = "") -> None:
            # aggregate 上报的 current 是已满足标量字段数; 分母由 handler 统一为含后续步骤的 total.
            await self.report_progress(current, progress_total, message)

        # 出站: 执行获取图 (execute_graph), 声明依赖的来源在第二段; 按 use_cache 复用 raw 快照.
        if payload.source_url:
            # 「添加来源链接」只抓用户贴的那一条: 不走类型路由, 不把全部来源重新问一遍.
            # any_site: 路由里没有这一家时也要去全部影片来源里找 —— 用户贴的链接就是权威.
            single = await self._scrape_one_source(payload.source_url, crawlers, content_type, any_site=True)
            if single is None:
                return TaskResult(success=False, error=f"无法从该地址取到 {payload.number} 的元数据")
            fetched_site, fetched_meta = single
            result = aggregate_single(q, fetched_site, fetched_meta, field_priority)
        else:
            result = await aggregate(
                q,
                crawlers,
                field_priority,
                field_language,
                db_data.raw if db_data else None,
                on_progress=_on_fetch_progress,
                multi_lang_sites=self._multi_language_sources,
                deferred_sites=frozenset(name for name in crawlers if name in self._partial_sources),
            )

        # 站点结果已由引擎 _fetch_one 逐条上报到 summary.outcomes; 这里只记录调度顺序.
        rec.update_summary(sites_queried=list(result.sites_queried))

        # 标量可以全空: 只要有来源返回结果, 海报 / 评分 / external_id 仍可入库.
        if not result.raw:
            current().warning("no data found from any source", failed_sites=result.failed_sites)
            # 失败也要能回答"为什么刮不到": 站点明细随失败结果落库.
            return TaskResult(
                success=False,
                error=f"未找到 {payload.number} 的元数据",
                result=ScrapeResult(
                    field_sources=result.field_sources,
                    failed_sites=list(result.failed_sites),
                    outcomes=current().site_outcomes(),
                ),
            )

        # 获取结束: 进度分子对齐标量字段数, 其后为物化与持久化.
        await self.report_progress(len(SCALAR_FIELDS), progress_total, "fetch")

        # 在翻译前保存聚合后最终选定来源的原始文本; 原始字段不参与 LLM 翻译.
        result.metadata.original_title = result.metadata.title
        result.metadata.original_plot = result.metadata.plot

        # WESTERN (包括识别失败后的 WESTERN 回退) 保留原文, 其余类型按现有配置翻译.
        if self._translator is not None and payload.content_type != ContentType.WESTERN:
            await self._translate_metadata(result.metadata, field_language, CacheKind.trans in payload.use_cache)

        # WESTERN 作品统一以封面作为三个图片字段的输入来源。必须在物化/裁剪前完成，
        # 使 poster 与 thumb 指向同一张图，skip_ratio 判断自然跳过裁剪；原海报字段独立保留该地址。
        if payload.content_type == ContentType.WESTERN and result.metadata.thumb_urls:
            western_cover_urls = list(result.metadata.thumb_urls)
            result.metadata.poster_urls = list(western_cover_urls)
            result.metadata.original_poster_urls = list(western_cover_urls)

        # 无码单图改走欧美式对齐; 双页仍使用无码独立裁剪比例.
        image_content_type = _prepare_image_content_type(payload.content_type, result.metadata)

        # 在物化前独立保留原海报 URL; 后续 poster_out 可被裁剪资源 URL 替换.
        original_poster_urls = list(result.metadata.original_poster_urls)

        # 物化到 Resource; 失败保留原始 URL.
        poster_out = result.metadata.poster_urls
        thumb_out = result.metadata.thumb_urls
        trailer_out = result.metadata.trailer_urls
        if self._web_client is not None:
            try:
                # 系列 / 番号前缀 / 供应商 / 发行商命中的固定裁切窗口, 优先于人脸检测.
                crop_rule = find_rule(
                    self._config.scraping.poster_crop_rules,
                    number=result.metadata.number,
                    series=result.metadata.series,
                    studio=result.metadata.studio,
                    publisher=result.metadata.publisher,
                    # 文件名优先, 没有就退回番号 (见 crop_rules._searchable_text).
                    media_path=file.path if file is not None else None,
                )
                materialized = await materialize_images(
                    result.metadata.poster_urls,
                    result.metadata.thumb_urls,
                    result.metadata.trailer_urls,
                    self._resource_store,
                    self._web_client,
                    self._config,
                    self._resource_store.data_dir,
                    content_type=payload.content_type,
                    image_content_type=image_content_type,
                    extrafanart_urls=result.metadata.extrafanart_urls,
                    # 强制刮削时把海报重裁一遍: 派生键只含「源 + 比例」, 裁切算法升级后
                    # 旧产物不会自动失效, 不重跑就一直留着旧构图.
                    recrop=not payload.use_cache,
                    crop_rule=crop_rule,
                )
                poster_out = materialized.poster_urls
                thumb_out = materialized.thumb_urls
                trailer_out = materialized.trailer_urls
            except Exception:
                current().warning("image materialization failed, keeping raw urls", number=payload.number)

        await self.report_progress(len(SCALAR_FIELDS) + 1, progress_total, "materialize")

        # 写库并关联 MediaFile. Metadata.actors 仍是展示名; 性别写入 Actor 空位.
        cast = result.metadata.actors
        source_out = _prioritize_source_urls(
            result.metadata.source_urls,
            result.field_sources,
            manual=payload.manual and not payload.append_sources,
            preferred_site=payload.preferred_site,
            # 非手动也按路由顺序排: 用户改了类型路由的顺序, 来源链接应当跟着走.
            route=route if not payload.append_sources else None,
        )
        if payload.append_sources:
            # 只补来源链接: 既有链接保持原顺序, 新来源接在末尾.
            # identity_metadata 只在传了 metadata_id 时有值; 按番号进来的调用方要靠番号查一次,
            # 否则会拿空字典当既有链接, 把原有来源整段覆盖掉.
            previous = identity_metadata or await self._repo.get_metadata_by_number(
                payload.number, content_type=payload.content_type
            )
            source_out = _append_new_sources(dict(previous.source_urls or {}) if previous else {}, source_out)
        metadata_fields = dict(
            actor_genders={item.name: item.gender for item in cast if item.gender != ActorGender.UNKNOWN},
            title=result.metadata.title,
            original_title=result.metadata.original_title,
            actors=[item.name for item in cast],
            studio=result.metadata.studio,
            publisher=result.metadata.publisher,
            release=result.metadata.release,
            runtime=result.metadata.runtime,
            tags=result.metadata.tags,
            series=result.metadata.series,
            series_names=result.metadata.series_names or ([result.metadata.series] if result.metadata.series else []),
            plot=result.metadata.plot,
            original_plot=result.metadata.original_plot,
            directors=result.metadata.directors,
            original_poster_urls=original_poster_urls,
            poster_urls=poster_out,
            thumb_urls=thumb_out,
            trailer_urls=trailer_out,
            extrafanart_urls=result.metadata.extrafanart_urls,
            scores={s.site: s.score for s in result.metadata.scores},
            external_ids=result.metadata.external_ids,
            # 手动搜索把这次命中的站点提到最前: 来源列表按插入顺序展示, 用户最想看到刚抓的那一条.
            # 「添加来源链接」已在上面合并成「旧顺序 + 新来源」.
            source_urls=source_out,
            field_sources=result.field_sources,
            raw=result.raw,
        )
        if payload.append_sources:
            # 只补来源链接: 其余字段一个不写, 免得这次抓到的值顶掉用户已有的内容.
            # 走到这里说明调用方要的是「加一条来源」, 不是让这次刮削接管整条记录.
            previous = identity_metadata or await self._repo.get_metadata_by_number(
                payload.number, content_type=payload.content_type
            )
            previous_raw = dict(previous.raw or {}) if previous is not None else {}
            previous_sources = dict(previous.field_sources or {}) if previous is not None else {}
            metadata_fields = {
                "source_urls": source_out,
                # raw 只并进去: 整片覆盖会让「对比来源」页只剩这一次抓的那家.
                "raw": {**previous_raw, **result.raw},
                # field_sources 反过来以旧的为准: 这次只补链接, 不接管任何字段的归属,
                # 否则「字段来自哪家」会凭空变成新来源, 而字段值其实没动.
                "field_sources": {**result.field_sources, **previous_sources},
            }
        if payload.content_type == ContentType.WESTERN:
            returned_theporndb_uuid = result.metadata.external_ids.get("theporndb")
            if (
                theporndb_uuid
                and returned_theporndb_uuid
                and returned_theporndb_uuid.casefold() != theporndb_uuid.casefold()
            ):
                return TaskResult(
                    success=False,
                    error=f"ThePornDB UUID 不匹配：预期 {theporndb_uuid}，实际 {returned_theporndb_uuid}",
                )
            theporndb_uuid = returned_theporndb_uuid or theporndb_uuid
            if not theporndb_uuid or not returned_theporndb_uuid:
                return TaskResult(
                    success=False, error="ThePornDB 未验证并返回有效 ThePornDB UUID，无法建立欧美作品身份"
                )
            meta = await self._repo.finalize_western_scrape(
                theporndb_uuid=theporndb_uuid,
                number=payload.number,
                metadata_id=payload.metadata_id,
                media_file_id=payload.media_file_id,
                oshash=file_hash,
                file_115_id=file_115_id,
                **metadata_fields,
            )
        else:
            meta = await self._repo.upsert_metadata(
                number=payload.number,
                content_type=payload.content_type,
                mode=WriteMode.MANUAL if payload.manual else WriteMode.AUTO,
                **metadata_fields,
            )

        if self._tag_mappings is not None:
            self._tag_mappings.observe(meta.tags, source=meta.field_sources.get("tags"))

        if payload.content_type != ContentType.WESTERN:
            await finalize_media_file(self._repo, payload.media_file_id, meta.id)

        # 手动搜索: 用户点的那条来源排到第一位.
        # 走 repo 的专用方法直接回写 JSON 列; ORM 赋值在 identity map 命中时会拿回缓存顺序,
        # 看着像没写进去.
        if payload.manual and not payload.append_sources and payload.preferred_site:
            await self._repo.reorder_source_urls(meta.id, payload.preferred_site)
            refreshed = await self._repo.get_metadata(meta.id)
            if refreshed is not None:
                meta = refreshed

        # 为尚未刮削的演员扇出 ACTOR_SCRAPE; 与整理并行, 二者互不依赖:
        # 整理需要的性别由 _genders_from_raw 从本次刮削快照里直接读, 不等演员刮削.
        actor_followups = await self._actor_scrape_followups(meta.actors)
        organize_followups = await self._auto_organize_followups(payload, file, meta.id)

        await self.report_progress(progress_total, progress_total, "done")

        current().info(
            "scrape completed",
            metadata_id=meta.id,
            sites_used=len(set(result.field_sources.values())),
            fields_resolved=len(result.field_sources),
            failed_sites=result.failed_sites,
            actor_followups=len(actor_followups),
            organize_followups=len(organize_followups),
        )

        assert meta.id is not None
        return TaskResult(
            success=True,
            result=ScrapeResult(
                metadata_id=meta.id,
                field_sources=result.field_sources,
                failed_sites=result.failed_sites,
                outcomes=current().site_outcomes(),
            ),
            followups=[*actor_followups, *organize_followups],
        )

    def failure_result(self, payload: ScrapePayload) -> ScrapeResult | None:
        """抓取之后崩掉时, 站点明细仍要能回答"为什么刮不到"."""
        outcomes = current().site_outcomes(include_unqueried=True)
        if not outcomes:
            return None
        return ScrapeResult(
            field_sources={},
            failed_sites=[row.site for row in outcomes if row.outcome is SiteOutcomeKind.FAILED],
            outcomes=outcomes,
        )

    async def _adopt_sibling_discs(self, first: MediaFile, metadata_id: int) -> list[int]:
        """把同番号的后续盘片并入本次整理, 并把首盘的元数据挂到它们身上.

        后续盘片按设计不发 SCRAPE (同番号共用一份元数据), 不在这里接手就会一直停在原处,
        既没命名也没搬走.
        """
        if first.id is None or first.library_id is None:
            return []
        first_parsed = parse_file_info(first.path)
        if first_parsed.number is None:
            return []

        candidates = await self._repo.list_media_files(library_id=first.library_id, limit=None)
        adopted: list[int] = []
        for candidate in candidates:
            if candidate.id is None or candidate.id == first.id:
                continue
            parsed = parse_file_info(candidate.path)
            if parsed.number != first_parsed.number or parsed.cd is None or parsed.cd == 1:
                continue
            await self._repo.update_media_file(
                candidate.id,
                metadata_id=metadata_id,
                number=first.number,
                status=MediaFileStatus.SCRAPED,
            )
            adopted.append(candidate.id)
        return adopted

    async def _auto_organize_followups(
        self, payload: ScrapePayload, file: MediaFile | None, metadata_id: int
    ) -> list[FollowupTask]:
        """库开启自动整理时为本次刮削的那一个文件描述 ORGANIZE 后继.

        范围只有该文件, 且后继只在父成功后创建, 因此整理必然发生在它刮削完成之后.
        按番号刮削没有可落盘的 `MediaFile` (库因此未知), 不在此列; `media_file_id` 非空但读不到
        文件或库说明索引与任务不一致, 记 debug 后不链.
        ``priority=0``: 与影片任务同档. 早先排最低 (-1), 结果是只要还有刮削排队,
        整理就一直轮不上 —— 每个刮削都派发一条整理, 而刮削一批要几十分钟,
        文件长时间停在「已刮削未落盘」.

        整段 ``except`` 只为不让已写库的刮削任务转为 FAILED; 这一支不可达于任何预期输入, 出现即模型或
        数据库有问题, 按异常记日志. 此时整理不会发生, 该文件停在「已刮削未落盘」, 与完成事务失败同属不覆盖窗口.
        """
        if payload.media_file_id is None:
            return []
        organize = await self._build_auto_organize(payload, file, metadata_id)
        if organize is None:
            return []
        assert file is not None and file.id is not None
        return [
            FollowupTask(
                key=f"organize:{file.id}",
                task_type=TaskType.ORGANIZE,
                payload=organize.model_dump(mode="json"),
                priority=0,
            )
        ]

    async def _build_auto_organize(
        self, payload: ScrapePayload, file: MediaFile | None, metadata_id: int
    ) -> OrganizePayload | None:
        """按库配置判断本次刮削该不该整理, 该则交出整理 payload.

        判定与后继包装分开: 判定部分要被多处复用, 各自包装成不同的后继形状.
        """
        if payload.media_file_id is None:
            return None
        if file is None or file.id is None:
            current().debug("auto organize skipped: media file missing", media_file_id=payload.media_file_id)
            return None
        try:
            library = await self._repo.get_library(file.library_id)
            if library is None:
                current().debug("auto organize skipped: library missing", library_id=file.library_id)
                return None
            # 启用监控目录后整理不再由 auto_organize 决定 (界面那开关跟着置灰).
            # 下载目录进来的文件必须整理, 否则永远落不到库路径;
            # 目标目录内的文件是否整理, 取决于 watch_rename (只改名不搬动).
            from_download = bool(library.watch_path) and path_is_under(Path(file.path), Path(library.watch_path))
            if library.watch_dir_enabled:
                if not from_download and not library.watch_rename:
                    current().debug("auto organize skipped: in-place mode", library_id=library.id)
                    return None
            elif not library.auto_organize:
                return None
            # 多盘片的后续张不发 SCRAPE, 靠首盘这次的元数据一起落盘. 同番号、同库、
            # 还没整理的行都并进来, 否则 CD2 会永远留在原处.
            siblings = await self._adopt_sibling_discs(file, metadata_id)
            return OrganizePayload(library_id=file.library_id, media_file_ids=[file.id, *siblings])
        except Exception:
            current().exception("failed to build auto organize followup", media_file_id=file.id)
            return None

    async def _actor_scrape_followups(self, actor_names: list[str]) -> list[FollowupTask]:
        """为尚未刮削的影片演员描述 ACTOR_SCRAPE 后继; 失败不阻断主流程.

        ``meta.actors`` 已是 FacetRule 后的规范名, 与 sync_metadata_facets 创建的 Actor 对应, 缺失名静默跳过.
        跳过 ``Actor.raw`` 非空者 (站点快照可复用).
        ``priority=-1``: 批量产生的演员任务不能抢占影片任务.
        """
        if not self._config.actor_scraping.auto_scrape or not actor_names:
            return []
        try:
            actors = await self._repo.get_actors_by_names(actor_names)
            return [
                FollowupTask(
                    key=f"actor-scrape:{actor.id}",
                    task_type=TaskType.ACTOR_SCRAPE,
                    payload=ActorScrapePayload(actor_id=actor.id).model_dump(mode="json"),
                    priority=-1,
                )
                for actor in actors
                if actor.id is not None and not actor.raw
            ]
        except Exception:
            current().warning("failed to build actor scrape followups", actors=actor_names)
            return []

    async def _translate_metadata(
        self, metadata: AggregatedMetadata, field_language: FieldLanguage, use_cache: bool
    ) -> None:
        """就地翻译 ``llm.translate_fields`` 中的文本标量. 单字段失败不影响其余, 全程不抛.
        ``use_cache`` 为 False 时跳过译文缓存读取, 但仍刷新缓存.
        """
        assert self._translator is not None
        fields = self._config.llm.translate_fields
        if MetadataField.TITLE in fields and metadata.title:
            translated = await self._translate_field(metadata.title, field_language, MetadataField.TITLE, use_cache)
            if translated:
                metadata.title = translated
        if MetadataField.PLOT in fields and metadata.plot:
            translated = await self._translate_field(metadata.plot, field_language, MetadataField.PLOT, use_cache)
            if translated:
                metadata.plot = translated

    async def _translate_field(
        self, value: str, field_language: FieldLanguage, field: MetadataField, use_cache: bool
    ) -> str | None:
        """翻译单字段; 异常忽略. 无目标语言或失败时返回 None."""
        assert self._translator is not None
        target = field_language.get(field)
        if target is None:
            return None
        try:
            result = await self._translator.translate(value, target, field, use_cache=use_cache)
        except Exception as e:
            current().warning("translation failed, keeping original", field=str(field), error=str(e))
            return None
        if result:
            current().debug("field translated", field=str(field), target=str(target))
        return result
