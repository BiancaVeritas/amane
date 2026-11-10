from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from structlog.contextvars import bind_contextvars

from ..aggregate import SCALAR_FIELDS, AggregatedMetadata, CrawlerLike, FieldLanguage, aggregate, compile_priority
from ..crawlers.models import SearchQuery
from ..crawlers.site_roles import builtin_descriptors
from ..db.models import MediaIdentityType, TaskType
from ..enums import ActorGender, MetadataField, SiteName
from ..media import materialize_images
from ..media.crop_rules import find_rule
from ..observability import current
from ..observability.models import SiteOutcomeKind
from ..parsing import ContentType
from ..plugins.models import SourceDescriptor, SourceTrait
from ..utils.litepan_strm import read_litepan_115_file_id
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
        use_metadata_cache = CacheKind.metadata in payload.use_cache
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
                    content_type=image_content_type,
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
            source_urls=result.metadata.source_urls,
            field_sources=result.field_sources,
            raw=result.raw,
        )
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
                **metadata_fields,
            )

        if self._tag_mappings is not None:
            self._tag_mappings.observe(meta.tags, source=meta.field_sources.get("tags"))

        if payload.content_type != ContentType.WESTERN:
            await finalize_media_file(self._repo, payload.media_file_id, meta.id)

        # 为尚未刮削的演员扇出 ACTOR_SCRAPE.
        actor_followups = await self._actor_scrape_followups(meta.actors)
        # 库开启自动整理时为本次刮削的文件扇出 ORGANIZE.
        organize_followups = await self._auto_organize_followups(payload, file)

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

    async def _auto_organize_followups(self, payload: ScrapePayload, file: MediaFile | None) -> list[FollowupTask]:
        """库开启自动整理时为本次刮削的那一个文件描述 ORGANIZE 后继.

        范围只有该文件, 且后继只在父成功后创建, 因此整理必然发生在它刮削完成之后.
        按番号刮削没有可落盘的 `MediaFile` (库因此未知), 不在此列; `media_file_id` 非空但读不到
        文件或库说明索引与任务不一致, 记 debug 后不链.
        ``priority=-1``: 不抢占交互提交的任务; 自动整理彼此仍按 FIFO.

        整段 ``except`` 只为不让已写库的刮削任务转为 FAILED; 这一支不可达于任何预期输入, 出现即模型或
        数据库有问题, 按异常记日志. 此时整理不会发生, 该文件停在「已刮削未落盘」, 与完成事务失败同属不覆盖窗口.
        """
        if payload.media_file_id is None:
            return []
        if file is None or file.id is None:
            current().debug("auto organize skipped: media file missing", media_file_id=payload.media_file_id)
            return []
        try:
            library = await self._repo.get_library(file.library_id)
            if library is None:
                current().debug("auto organize skipped: library missing", library_id=file.library_id)
                return []
            if not library.auto_organize:
                return []
            organize = OrganizePayload(library_id=file.library_id, media_file_ids=[file.id])
            return [
                FollowupTask(
                    key=f"organize:{file.id}",
                    task_type=TaskType.ORGANIZE,
                    payload=organize.model_dump(mode="json"),
                    priority=-1,
                )
            ]
        except Exception:
            current().exception("failed to build auto organize followup", media_file_id=file.id)
            return []

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
