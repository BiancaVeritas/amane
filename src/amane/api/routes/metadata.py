import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, cast

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import TypeAdapter

from ...aggregate import compute_merge_updates
from ...crawlers import registry
from ...crawlers.site_roles import site_publishers
from ...db.embeddings import EmbeddingKind
from ...db.models import FacetKind, FacetSortField, Metadata, MetadataSortField, SavedQueryEntity, SortOrder, TaskType
from ...db.repo_types import WriteMode
from ...db.repos.media import file_phase_of
from ...enums import MetadataField, SiteName
from ...handlers import ScrapePayload
from ...llm import build_translator
from ...media import manual_crop_image
from ...media.images import crop_poster, probe_oriented_size
from ...media.resource_store import RESOURCE_URL_PREFIX, ResourceStore, internal_url_hash
from ...media.upload import StoredUpload, UploadRejected, store_upload
from ...parsing import ContentType, canonical_number, summarize_file_phases
from ...parsing import FilePhaseSummary as ParsedFilePhase
from ...semantic import SemanticUnavailable
from ...tags import MappingGroup, vendor_names
from ...utils.collections import unique_ids
from ...utils.dates import normalize_calendar_date
from ...utils.model import to_resp
from ..deps import RepoDep, RuntimeDep
from ..models import (
    CommentResponse,
    CropPosterRequest,
    FilePhaseSummary,
    ImageInfo,
    ImageInfoRequest,
    ImageInfoResponse,
    ImageKind,
    MergeRequest,
    MetadataBatchDeleteResponse,
    MetadataBatchIdsRequest,
    MetadataBatchScrapeRequest,
    MetadataBatchScrapeResponse,
    MetadataCreateRequest,
    MetadataDetailResponse,
    MetadataListItem,
    MetadataListResponse,
    MetadataLocksRequest,
    MetadataMergeEntriesRequest,
    MetadataResponse,
    MetadataUserTagsRequest,
    MetadataUserTagsResponse,
    PartialMetadata,
    SemanticHitInfo,
    TagPair,
    UserTagResponse,
    to_media_file_response,
)
from ..support.metadata_filters import MetadataFilters, parse_metadata_filters, require_known_definition
from .saved_queries import resolve_saved_query_id_subquery

if TYPE_CHECKING:
    from ...db.repo_types import MetadataFields
    from ...net.http import WebClient

logger = structlog.get_logger()

router = APIRouter(prefix="/metadata", tags=["metadata"])


async def build_metadata_responses(
    repo: RepoDep, items: Sequence[Metadata], runtime: RuntimeDep
) -> list[MetadataListItem]:
    """列表与语义检索共用: 补 file_count / file_phase 聚合; 列表项不含 raw / field_sources."""
    summaries = await repo.summarize_media_by_metadata(items)
    responses: list[MetadataListItem] = []
    for m in items:
        summary = summaries.get(m.id) if m.id is not None else None
        # 列表项也要中文映射: 片库卡片直接显示这些标签.
        response = to_resp(MetadataListItem, m).model_copy(
            update={"tags": runtime.mappings.store(MappingGroup.TAG).map_tags(m.tags), **_vendor_display(runtime, m)}
        )
        if summary is not None:
            response = response.model_copy(
                update={
                    "file_count": summary.file_count,
                    "file_phase": _file_phase_resp(summary.phase, response.tags, from_tangxin=_is_tangxin(m)),
                }
            )
        responses.append(response)
    return responses


def _vendor_display(runtime: RuntimeDep, metadata: Metadata) -> dict[str, object]:
    """片商三件套的展示值 (已映射, 缺发行商时按制作商补齐).

    原名仍留在库里: 搜索、筛选与去重一律按原名, 只有展示与 NFO 走这一份.
    """
    shown = vendor_names(metadata).translated(runtime.mappings.vendor_roots())
    return {
        "studio": shown.studio,
        "publisher": shown.publisher,
        "series": shown.series[0] if shown.series else None,
        "series_names": shown.series,
    }


def _display_response(runtime: RuntimeDep, metadata: Metadata) -> MetadataResponse:
    """条目的对外形态: 标签与片商三件套都换成展示值.

    映射只改展示; 搜索、筛选与去重仍用库里的原名.
    """
    return to_resp(MetadataResponse, metadata).model_copy(
        update={
            "tags": runtime.mappings.store(MappingGroup.TAG).map_tags(metadata.tags),
            **_vendor_display(runtime, metadata),
        }
    )


async def _split_merged_tag_ids(
    repo: RepoDep, runtime: RuntimeDep, tag_ids: Sequence[int] | None
) -> tuple[list[int] | None, list[int] | None]:
    """把请求里的 tag_id 拆成「按 AND 的」与「按 OR 的」两批.

    界面上按译名合并过的标签 (同一概念的多个原名) 必须取并集: 只取其中一个原名会漏掉
    另一半条目, 而两个都按 AND 传则得到空集. 未被合并的标签仍保持 AND 语义.

    合并依据是**展示名**: 有译文取译文, 没译文取原名. 所以一个本来就叫「二穴同入」的
    中文标签, 会和译文同样为「二穴同入」的日文标签并成一组 —— 后者要按展示名反查,
    光靠映射表够不到那些没配映射的标签, 因此这里遍历实际存在的标签名.
    """
    if not tag_ids:
        return list(tag_ids or []), None
    rows, _ = await repo.list_facets(
        FacetKind.TAG, search=None, offset=0, limit=1000, sort_by=FacetSortField.NAME, order=SortOrder.ASC
    )
    display_by_id = {row.id: runtime.mappings.store(MappingGroup.TAG).map_tags([row.name])[0] for row in rows}
    ids_by_display: dict[str, list[int]] = {}
    for fid, display in display_by_id.items():
        ids_by_display.setdefault(display, []).append(fid)

    and_ids: list[int] = []
    or_ids: list[int] = []
    for tag_id in tag_ids:
        siblings = ids_by_display.get(display_by_id.get(tag_id, ""), [])
        if len(siblings) <= 1:
            and_ids.append(tag_id)
            continue
        or_ids.extend(siblings)
    return and_ids or None, or_ids or None


def _file_phase_resp(
    summary: ParsedFilePhase, tags: Sequence[str] = (), *, from_tangxin: bool = False
) -> FilePhaseSummary:
    """``tags`` 传条目已映射的标签: 前端据此点亮「无码」, 缺了它就只剩文件标记可看.

    ``from_tangxin`` 是糖心条目: 该站只放无马赛克的原创国产片, 类型却是国产 (国产不触发
    无码), 因此在这里直接给无码, 不靠塞标签或改文件相位 —— 那是两个不相干的真值来源.
    """
    return FilePhaseSummary(
        has_subtitle=summary.has_subtitle,
        censored=summary.censored and not from_tangxin,
        uncensored=summary.uncensored or from_tangxin,
        mosaics=list(summary.mosaics),
        definition=summary.definition,
        ltx=summary.ltx,
        tags=list(tags),
    )


def _is_tangxin(metadata: Metadata) -> bool:
    """条目是否来自糖心. 用 ``source_urls`` 的站点键判定, 与刮削实际写过哪个来源一致."""
    return str(SiteName.TANGXIN) in (metadata.source_urls or {})


def _site_home(site: str) -> str:
    """来源主页地址, 用作手建条目的占位来源链接; 认不出的站点返回其名, 只为占键."""
    cls = registry.get(site)
    return cls.profile().base_url if cls is not None else site


@router.get("/schema")
async def get_metadata_schema() -> dict:
    return TypeAdapter(PartialMetadata).json_schema()


@router.get("/source-presets")
async def get_source_presets(runtime: RuntimeDep) -> dict[str, dict[str, str]]:
    """按内容类型给出可选的「来源」→ 该来源固定义发行商与制作商.

    候选范围就是**类型路由**: 只有该类型路由里出现的来源才列出来, 所以「糖心」只会在选国产时
    出现, 与刮削实际会走哪些站一致. 路由里的索引站 (javdb / javbus) 覆盖多家厂牌, 没有固定
    厂牌, 因而不在结果里 —— 选中它们也不会预填任何字段.

    结构是 ``{content_type: {site: publisher}}``; 真值仍在 `CrawlerProfile.publisher`.
    """
    publishers = site_publishers()
    routes = runtime.config.hot.scraping.content_routes
    by_type: dict[str, dict[str, str]] = {}
    for content_type, sites in routes.items():
        presets = {site: publishers[site] for site in sites if site in publishers}
        if presets:
            by_type[str(content_type)] = presets
    return by_type


@router.get("")
async def list_metadata(
    repo: RepoDep,
    runtime: RuntimeDep,
    filters: Annotated[MetadataFilters, Depends(parse_metadata_filters)],
    search: Annotated[str | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    # 前端页大小选项最高 500 (见 web/src/lib/page-size.ts); 对不上时 422, 列表看起来就是空的.
    limit: Annotated[int, Query(ge=1, le=500)] = 20,
    sort_by: Annotated[MetadataSortField, Query(description="Sort field")] = MetadataSortField.UPDATED_AT,
    order: Annotated[SortOrder, Query(description="Sort order")] = SortOrder.DESC,
    semantic_query: Annotated[
        str | None, Query(min_length=1, max_length=200, description="按画面描述排序; 提供时忽略 sort_by / order")
    ] = None,
    semantic_kind: Annotated[Literal["all", "cover", "still"], Query(description="参与语义排序的图片来源")] = "all",
) -> MetadataListResponse:
    definition = require_known_definition(filters.definition)
    # 标签在界面按译名合并显示: 「完全無修正」和「無碼」都译作「无修正」, 是同一个概念.
    # 选中那一条时要按整组算 (并集), 否则只命中其中一个原名下的条目.
    tag_ids, tag_any_ids = await _split_merged_tag_ids(repo, runtime, filters.tag_id)
    id_subquery_sql = None
    if filters.saved_query_id is not None:
        id_subquery_sql = await resolve_saved_query_id_subquery(repo, filters.saved_query_id, SavedQueryEntity.METADATA)
    if semantic_query is not None:
        return await _semantic_list(
            runtime=runtime,
            repo=repo,
            filters=filters,
            search=search,
            semantic_query=semantic_query,
            semantic_kind=semantic_kind,
            definition=definition,
            id_subquery_sql=id_subquery_sql,
            offset=offset,
            limit=limit,
        )
    items, total = await repo.list_metadata(
        keyword=search,
        offset=offset,
        limit=limit,
        sort_by=sort_by,
        order=order,
        actor_ids=filters.actor_id,
        director_ids=filters.director_id,
        tag_ids=tag_ids,
        tag_any_ids=tag_any_ids,
        studio_ids=filters.studio_id,
        publisher_ids=filters.publisher_id,
        series_ids=filters.series_id,
        user_tag_ids=filters.user_tag_id,
        has_files=filters.has_files,
        has_subtitle=filters.has_subtitle,
        vr=filters.vr,
        mosaic=filters.mosaic,
        uncensored=filters.uncensored,
        definition=definition,
        content_type=filters.content_type,
        id_subquery_sql=id_subquery_sql,
    )
    return MetadataListResponse(
        items=await build_metadata_responses(repo, items, runtime),
        total=total,
        actor_display_names=_actor_names_of(runtime, items),
    )


def _actor_names_of(runtime, items) -> dict[str, str]:
    """把这一页出现过的演员名收敛成一次映射查询, 避免逐条取."""
    names: set[str] = set()
    for item in items:
        names.update(item.actors or [])
    return runtime.mappings.store(MappingGroup.ACTOR).display_map(sorted(names)) if names else {}


async def _semantic_list(
    *,
    runtime: RuntimeDep,
    repo: RepoDep,
    filters: MetadataFilters,
    search: str | None,
    semantic_query: str,
    semantic_kind: Literal["all", "cover", "still"],
    definition: str | None,
    id_subquery_sql: str | None,
    offset: int,
    limit: int,
) -> MetadataListResponse:
    """语义参数出现时接管排序: 候选集来自同一套筛选, 返回结构与普通列表一致."""
    service = runtime.semantic
    if service is None or not service.enabled:
        raise HTTPException(status_code=503, detail="语义检索未启用")
    allowed_ids = None
    if filters.active or search:
        allowed_ids = await repo.list_metadata_ids(
            keyword=search,
            actor_ids=filters.actor_id,
            director_ids=filters.director_id,
            tag_ids=filters.tag_id,
            studio_ids=filters.studio_id,
            publisher_ids=filters.publisher_id,
            series_ids=filters.series_id,
            user_tag_ids=filters.user_tag_id,
            has_files=filters.has_files,
            has_subtitle=filters.has_subtitle,
            vr=filters.vr,
            mosaic=filters.mosaic,
            uncensored=filters.uncensored,
            definition=definition,
            content_type=filters.content_type,
            id_subquery_sql=id_subquery_sql,
        )
    kind = None if semantic_kind == "all" else EmbeddingKind(semantic_kind)
    try:
        hits = await service.search(semantic_query, limit, kind, allowed_ids, offset)
        total = await service.count(kind, allowed_ids)
    except SemanticUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not hits:
        return MetadataListResponse(items=[], total=total)
    ids = [hit.metadata_id for hit in hits]
    items, _ = await repo.list_metadata(ids=ids, limit=len(ids))
    by_id = {item.id: item for item in items}
    ordered = [by_id[hit.metadata_id] for hit in hits if hit.metadata_id in by_id]
    responses = {response.id: response for response in await build_metadata_responses(repo, ordered, runtime)}
    return MetadataListResponse(
        items=[responses[hit.metadata_id] for hit in hits if hit.metadata_id in responses],
        total=total,
        actor_display_names=_actor_names_of(runtime, ordered),
        semantic_hits=[
            SemanticHitInfo(
                id=hit.metadata_id,
                kind=hit.kind,
                resource_url=hit.resource_url,
                similarity=hit.score,
            )
            for hit in hits
            if hit.metadata_id in responses
        ],
    )


@router.post("", status_code=201)
async def create_metadata(req: MetadataCreateRequest, repo: RepoDep, runtime: RuntimeDep) -> MetadataResponse:
    """手动创建条目: 番号 + 类型 + 详情字段.

    与刮削落库的区别只在入口: 类型由用户指定 (留空按番号推断), 字段按 ``WriteMode.MANUAL``
    写入, 因此不会被既有锁定挡住. 番号已存在时返回 409, 让用户去编辑那一条 ——
    静默覆盖既有条目会把刮削来的字段冲掉.
    """
    number = canonical_number(req.number.strip()) or req.number.strip()
    existing = await repo.get_metadata_by_number(number)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"番号 {number} 已存在")

    updates = cast("MetadataFields", req.fields.model_dump(exclude_unset=True))
    if "release" in updates and updates["release"] is not None:
        raw_release = updates["release"]
        normalized = normalize_calendar_date(raw_release) if isinstance(raw_release, str) else None
        if normalized is None:
            raise HTTPException(status_code=422, detail="发行日期必须为 YYYY-MM-DD")
        updates["release"] = normalized
    # 选了来源就落一条占位链接: 无码判定 (糖心) 与「来源」展示都读 source_urls,
    # 手建条目没有真实刮削地址, 用来源主页占位即可.
    if req.source:
        site = req.source.strip()
        if site not in site_publishers() and site not in {str(s.name) for s in SiteName}:
            raise HTTPException(status_code=422, detail=f"未知来源: {site}")
        updates["source_urls"] = {site: f"{_site_home(site)}"}
    try:
        metadata = await repo.upsert_metadata(
            number,
            content_type=req.content_type,
            mode=WriteMode.MANUAL,
            **updates,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info("metadata created", metadata_id=metadata.id, number=number, content_type=str(metadata.content_type))
    return _display_response(runtime, metadata)


@router.post("/batch/delete")
async def batch_delete_metadata(
    req: MetadataBatchIdsRequest, repo: RepoDep, runtime: RuntimeDep
) -> MetadataBatchDeleteResponse:
    """级联行为与单条删除一致; 重复 id 去重后只处理一次."""
    ids = unique_ids(req.ids)
    deleted, missing = await repo.batch_delete_metadata(ids)
    skipped = len(req.ids) - len(ids)
    # 删完再清空分类: 计数归零只可能由这次删除造成, 在这里处理最及时.
    pruned = await repo.prune_empty_facets(runtime.mappings)
    logger.info("metadata batch deleted", deleted=deleted, missing=missing, skipped=skipped, pruned=pruned)
    return MetadataBatchDeleteResponse(deleted=deleted, missing=missing, skipped=skipped)


@router.post("/batch/scrape", status_code=202)
async def batch_scrape_metadata(req: MetadataBatchScrapeRequest, repo: RepoDep) -> MetadataBatchScrapeResponse:
    """以各自 Metadata ID / ThePornDB UUID 重新刮削; 重复 id 去重后只提交一次."""
    ids = unique_ids(req.ids)
    metadata_by_id = {meta.id: meta for meta in await repo.get_metadata_by_ids(ids) if meta.id is not None}
    missing = 0
    payloads: list[ScrapePayload] = []
    for metadata_id in ids:
        metadata = metadata_by_id.get(metadata_id)
        if metadata is None:
            missing += 1
            continue
        resolved_type = req.content_type if req.content_type is not None else metadata.content_type
        if resolved_type != metadata.content_type and (
            resolved_type == ContentType.WESTERN or metadata.content_type == ContentType.WESTERN
        ):
            raise HTTPException(status_code=422, detail="WESTERN 重新刮削不能更改作品内容类型")
        preserve_identity = resolved_type == metadata.content_type
        payloads.append(
            ScrapePayload(
                number=metadata.number,
                content_type=resolved_type,
                metadata_id=metadata.id if preserve_identity else None,
                theporndb_uuid=metadata.theporndb_uuid if preserve_identity else None,
                use_cache=req.use_cache,
            )
        )
    # 批量提交动辄数百条, 逐条提交会重复付出事务开销
    tasks = await repo.create_tasks(TaskType.SCRAPE, payloads)
    task_ids = [task.id for task in tasks if task.id is not None]
    skipped = len(req.ids) - len(ids)
    logger.info("metadata batch scrape submitted", submitted=len(task_ids), missing=missing, skipped=skipped)
    return MetadataBatchScrapeResponse(submitted=len(task_ids), missing=missing, skipped=skipped, task_ids=task_ids)


@router.post("/batch/user-tags")
async def batch_metadata_user_tags(req: MetadataUserTagsRequest, repo: RepoDep) -> MetadataUserTagsResponse:
    """把一组用户标签应用到一组影片; 未知标签 id 返回 404, 不存在的影片 id 计入 missing."""
    ids = unique_ids(req.ids)
    try:
        result = await repo.apply_metadata_user_tags(ids, req.user_tag_ids, action=req.action)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    skipped = len(req.ids) - len(ids)
    logger.info(
        "metadata user tags applied",
        action=req.action,
        changed=result.changed,
        unchanged=result.unchanged,
        missing=result.missing,
        skipped=skipped,
    )
    return MetadataUserTagsResponse(
        changed=result.changed, unchanged=result.unchanged, missing=result.missing, skipped=skipped
    )


@router.post("/merge-entries")
async def merge_metadata_entries(
    req: MetadataMergeEntriesRequest, repo: RepoDep, runtime: RuntimeDep
) -> MetadataResponse:
    """把来源条目的 raw 快照补入目标并删除来源: 目标字段不变, 关联记录迁入目标.

    ``rescrape`` 要求合并提交后再按目标番号入队 SCRAPE: 只有 raw 已落库, 复用快照的聚合
    才能按当前优先级重建字段与站点级字典.
    """
    try:
        metadata = await repo.merge_metadata_entries(req.target_id, req.source_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if metadata is None:
        raise HTTPException(status_code=404, detail="影片不存在")
    rescrape_task_id: int | None = None
    if req.rescrape:
        task = await repo.create_task(
            TaskType.SCRAPE,
            ScrapePayload(number=metadata.number, content_type=metadata.content_type),
        )
        rescrape_task_id = task.id
    logger.info(
        "metadata entries merged",
        target_id=req.target_id,
        source_ids=req.source_ids,
        rescrape_task_id=rescrape_task_id,
    )
    return _display_response(runtime, metadata)


@router.get("/{metadata_id}")
async def get_metadata(metadata_id: int, repo: RepoDep, runtime: RuntimeDep) -> MetadataDetailResponse:
    metadata = await repo.get_metadata(metadata_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="影片不存在")
    # 关联文件 / 用户 tag / 评论 / 分类 id
    files = await repo.get_media_by_metadata_id(metadata_id)
    user_tags = await repo.list_metadata_user_tags(metadata_id)
    comments = await repo.list_comments(metadata_id)
    (
        actor_ids,
        actor_genders,
        director_ids,
        tag_ids,
        studio_id,
        publisher_id,
        series_id,
    ) = await repo.resolve_metadata_facet_ids(metadata)
    series_ids = await repo.resolve_metadata_series_ids(metadata)
    tag_ids = runtime.mappings.store(MappingGroup.TAG).map_id_map(tag_ids)
    mapped_tags = runtime.mappings.store(MappingGroup.TAG).map_tags(metadata.tags)
    return MetadataDetailResponse(
        metadata=_display_response(runtime, metadata).model_copy(
            update={
                "tags": mapped_tags,
                "file_count": len(files),
                "file_phase": _file_phase_resp(
                    summarize_file_phases((file_phase_of(f) for f in files), metadata.content_type),
                    mapped_tags,
                    from_tangxin=_is_tangxin(metadata),
                ),
            }
        ),
        files=[to_media_file_response(f, metadata.content_type) for f in files],
        user_tags=[to_resp(UserTagResponse, t) for t in user_tags],
        comments=[to_resp(CommentResponse, c) for c in comments],
        actor_ids=actor_ids,
        actor_genders=actor_genders,
        actor_display_names=runtime.mappings.store(MappingGroup.ACTOR).display_map(list(metadata.actors or [])),
        tags=[
            TagPair(raw=raw, shown=shown)
            for raw, shown in runtime.mappings.store(MappingGroup.TAG).map_tag_pairs(metadata.tags)
        ],
        director_ids=director_ids,
        tag_ids=tag_ids,
        studio_id=studio_id,
        # 发行商按制作商补齐时没有自己的实体行, 用制作商的 id: 徽章仍能点进那道分类页.
        publisher_id=publisher_id if metadata.publisher else studio_id,
        series_id=series_id,
        series_ids=series_ids,
    )


@router.patch("/{metadata_id}")
async def update_metadata(
    metadata_id: int, req: PartialMetadata, repo: RepoDep, runtime: RuntimeDep
) -> MetadataResponse:
    # exclude_unset 区分省略键与显式 null; 三态见 docs/dev/data-model.md.
    updates = cast("MetadataFields", req.model_dump(exclude_unset=True))
    if not updates:
        raise HTTPException(status_code=422, detail="没有需要修改的字段")
    if "release" in updates and updates["release"] is not None:
        raw_release = updates["release"]
        if isinstance(raw_release, str):
            normalized = normalize_calendar_date(raw_release)
            if normalized is None:
                raise HTTPException(status_code=422, detail="发行日期必须为 YYYY-MM-DD")
            updates["release"] = normalized
        else:
            raise HTTPException(status_code=422, detail="发行日期必须为 YYYY-MM-DD")
    try:
        metadata = await repo.update_metadata(metadata_id, mode=WriteMode.MANUAL, **updates)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if metadata is None:
        raise HTTPException(status_code=404, detail="影片不存在")
    logger.info("metadata updated", metadata_id=metadata_id, fields=list(updates.keys()))
    return _display_response(runtime, metadata)


@router.put("/{metadata_id}/locks")
async def set_metadata_locks(
    metadata_id: int, req: MetadataLocksRequest, repo: RepoDep, runtime: RuntimeDep
) -> MetadataResponse:
    """整体替换锁定字段集合."""
    metadata = await repo.set_metadata_locks(metadata_id, req.fields)
    if metadata is None:
        raise HTTPException(status_code=404, detail="影片不存在")
    logger.info("metadata locks updated", metadata_id=metadata_id, fields=[str(field) for field in req.fields])
    return _display_response(runtime, metadata)


@router.delete("/{metadata_id}", status_code=204)
async def delete_metadata(metadata_id: int, repo: RepoDep, runtime: RuntimeDep) -> None:
    deleted = await repo.delete_metadata(metadata_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="影片不存在")
    await repo.prune_empty_facets(runtime.mappings)
    logger.info("metadata deleted", metadata_id=metadata_id)


@router.post("/{metadata_id}/crop-poster")
async def crop_poster_from_thumb(
    metadata_id: int,
    req: CropPosterRequest,
    repo: RepoDep,
    runtime: RuntimeDep,
) -> MetadataResponse:
    """按像素框裁切海报, 写入派生 Resource 并更新 poster_urls.

    基准图默认取封面 (``thumb_urls[0]``); 传入 ``source_url`` 时用那张 (另一张封面、剧照
    或上传图). 先按 EXIF 转正、再顺时针旋转 ``req.rotation``, 框相对旋转后的图像.
    """
    metadata = await repo.get_metadata(metadata_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="影片不存在")

    source_url = req.source_url or (metadata.thumb_urls[0] if metadata.thumb_urls else None)
    if not source_url:
        raise HTTPException(status_code=400, detail="无封面图可裁切")

    box = (req.left, req.top, req.right, req.bottom)
    try:
        poster_url = await manual_crop_image(
            source_url,
            box,
            runtime.resource_store,
            runtime.web_client,
            runtime.config.hot,
            runtime.config.cold.data_dir,
            subject="封面" if req.source_url is None else "图源",
            rotation=req.rotation,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # 海报只有一个: 裁一次就替换一次. 早先把旧海报留在后面 (当作候选池), 于是反复裁切会
    # 一直堆积 —— 海报没有「换回上一张」的场景, 旧的那份没有任何用处.
    updated = await _apply_poster(runtime.resource_store, repo, metadata_id, metadata, poster_url)
    logger.info(
        "poster cropped",
        metadata_id=metadata_id,
        box=box,
        rotation=req.rotation,
        source_url=source_url,
        poster_url=poster_url,
    )
    return _display_response(runtime, updated)


def _resource_url(store: ResourceStore, locator: str) -> str:
    """locator → 前端可访问的内部图片地址."""
    return f"{RESOURCE_URL_PREFIX}/{store.url_hash(locator)}"


def _poster_ratio(runtime: RuntimeDep, content_type: ContentType) -> float:
    """海报比例按内容类型分别配置."""
    hot = runtime.config.hot.scraping
    return hot.uncensored_poster_ratio if content_type == ContentType.UNCENSORED else hot.poster_ratio


async def _store_upload_or_400(store: ResourceStore, data: bytes, *, kind: str) -> StoredUpload:
    try:
        return await store_upload(store, data, kind=kind)
    except UploadRejected as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


async def _content_hash_of(store: ResourceStore, url: str) -> str | None:
    """内部图片地址 → 该资源的内容哈希; 外链或尚未缓存时返回 None."""
    url_hash = internal_url_hash(url)
    if url_hash is None:
        return None
    found = await store.get_by_url_hash(url_hash)
    return None if found is None else found[0].content_hash


async def _dedupe_by_content(store: ResourceStore, urls: list[str], keep: str) -> list[str]:
    """去掉 ``urls`` 里与 ``keep`` **内容相同**的条目, 但保留 ``keep`` 自身原位.

    早期版本的「采用剧照」把同一张图各存一份, 列表里于是堆积了内容相同、地址不同的副本;
    只按地址去重清不掉它们. 读不到内容的条目原样保留, 不误删.

    返回值保持原顺序; ``keep`` 在列表中则留在原位置, 不在则不出现在结果里 (由调用方决定
    追加到末尾).
    """
    keep_hash = await _content_hash_of(store, keep)
    out: list[str] = []
    for url in urls:
        if url != keep and keep_hash is not None and await _content_hash_of(store, url) == keep_hash:
            # 内容相同的另一份副本: 去掉.
            continue
        out.append(url)
    return out


async def _admit(store: ResourceStore, pool: list[str], url: str, current: str | None) -> list[str]:
    """把 ``url`` 并进候选池: 保持既有顺序, 同址或同内容的不重复入池.

    ``current`` 是当前正在用的那张. 旧数据没有候选池 (只有 ``thumb_urls``), 首次换图时
    要把它补进来, 否则原封面会被挤掉; 已有的池仍按原顺序, 不因为它是当前项就提到最前.
    """
    out = list(dict.fromkeys(pool))
    if current and current not in out:
        out.insert(0, current)
    if url in out:
        return out
    # 内容相同但地址不同 (站点给同一张图的多个尺寸、派生件): 只留已有的那份.
    url_hash = await _content_hash_of(store, url)
    if url_hash is not None:
        for existing in out:
            if await _content_hash_of(store, existing) == url_hash:
                return out
    return [*out, url]


async def _poster_equals_cover(store: ResourceStore, metadata: Metadata) -> bool:
    """海报是否就是封面本身.

    **不能比 URL**: 上传时的 locator 是 ``upload:{digest}:{kind}``, 同一份字节以封面传和以
    海报传会落到两个不同地址, 地址相同只在"把封面的 URL 直接塞给海报"时才成立.
    因此按内容哈希判定 —— 字节一样就是同一张图.

    两者的任一地址还不是本地资源 (外链未缓存) 时判为不同: 宁可不动海报, 也不误删用户的图.
    """
    thumb = metadata.thumb_urls[0] if metadata.thumb_urls else None
    poster = metadata.poster_urls[0] if metadata.poster_urls else None
    if thumb is None or poster is None:
        return False
    if thumb == poster:
        return True
    thumb_hash = await _content_hash_of(store, thumb)
    poster_hash = await _content_hash_of(store, poster)
    return thumb_hash is not None and thumb_hash == poster_hash


async def _apply_thumb(store: ResourceStore, repo: RepoDep, metadata_id: int, metadata: Metadata, url: str) -> Metadata:
    """把 ``url`` 设为当前封面.

    ``thumb_candidates`` 是候选池: 选过的图按首次出现的顺序留在里面, 换图不重排, 所以
    选图框里的编号不会因为换一次图就全变. ``thumb_urls`` 仍只存当前的那**一张** ——
    下游 (NFO / 整理 / 媒体库) 直接取它, 不该看到候选池.
    """
    pool = await _admit(store, metadata.thumb_candidates, url, metadata.thumb_urls[0] if metadata.thumb_urls else None)
    updated = await repo.update_metadata(
        metadata_id,
        mode=WriteMode.MANUAL,
        thumb_urls=[url],
        thumb_candidates=pool,
    )
    assert updated is not None
    return updated


async def _apply_poster(
    store: ResourceStore, repo: RepoDep, metadata_id: int, metadata: Metadata, url: str
) -> Metadata:
    """把 ``url`` 设为当前海报.

    海报没有候选池: 它由封面裁出 (一次只裁一张), 裁切对话框换的是**图源**而不是海报本身,
    不存在「换一张海报、想留回上一张」的场景.
    """
    del store, metadata
    updated = await repo.update_metadata(metadata_id, mode=WriteMode.MANUAL, poster_urls=[url])
    assert updated is not None
    return updated


async def _crop_source_to_poster(
    locator: str, path: Path, ratio: float, store: ResourceStore, runtime: RuntimeDep
) -> str:
    """把 ``locator`` 指向的图按海报比例裁一次 (人脸优先), 返回内部 URL.

    ``acquire_derived`` 以 (源, op, 参数) 为键, 同一源反复调用只会得到同一份裁剪结果,
    不会堆积.
    """
    source_url = _resource_url(store, locator)

    async def producer(dest: Path) -> bool:
        return crop_poster(
            path,
            dest,
            poster_ratio=ratio,
            jpeg_quality=runtime.config.hot.scraping.jpeg_quality,
            detect_face=runtime.config.hot.scraping.poster_crop_detect_face,
        )

    crop_res = await store.acquire_derived(locator, "crop", f"upload:{ratio:.4f}", producer)
    if crop_res is None:
        # 裁剪失败就退回原图, 不让操作直接失败.
        return source_url
    return _resource_url(store, crop_res.url)


@router.post("/{metadata_id}/upload-thumb")
async def upload_thumb(
    metadata_id: int,
    repo: RepoDep,
    runtime: RuntimeDep,
    file: Annotated[bytes, Body(media_type="application/octet-stream")],
) -> MetadataResponse:
    """上传自定义封面. 原样使用, 不裁剪. 旧封面保留在列表后面.

    若当前海报就是封面本身 (两者 URL 相同), 海报也跟着换成新封面 —— 那种状态下「海报=封面」
    是用户的既定选择, 换了封面却留着旧海报等于把这个关系悄悄破坏掉. 海报另有过独立来源
    (单独上传或手工裁切) 时不动它: 那是另一份资源, 不该被换封面带走.
    """
    metadata = await repo.get_metadata(metadata_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="影片不存在")

    store = runtime.resource_store
    stored = await _store_upload_or_400(store, file, kind="thumb")
    url = _resource_url(store, stored.url)
    poster_equals_cover = await _poster_equals_cover(store, metadata)
    updated = await _apply_thumb(store, repo, metadata_id, metadata, url)
    if poster_equals_cover:
        updated = await _apply_poster(store, repo, metadata_id, updated, url)
    logger.info(
        "thumb uploaded",
        metadata_id=metadata_id,
        url=url,
        poster_followed=poster_equals_cover,
    )
    return _display_response(runtime, updated)


@router.post("/{metadata_id}/upload-poster")
async def upload_poster(
    metadata_id: int,
    repo: RepoDep,
    runtime: RuntimeDep,
    file: Annotated[bytes, Body(media_type="application/octet-stream")],
    crop: Annotated[bool, Query(description="是否按海报比例自动裁剪; false 则原样使用")] = True,
) -> MetadataResponse:
    """上传自定义图片作为海报.

    默认按当前比例**自动裁剪一次** (人脸优先定位主体), 不满意可以再调 ``crop-poster``
    手工调整. ``crop=false`` 时原样使用 —— 手工建条目时用户传的往往就是竖版海报本身,
    再裁一次反而会把画面切掉. 旧海报保留在列表后面.
    """
    metadata = await repo.get_metadata(metadata_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="影片不存在")

    store = runtime.resource_store
    stored = await _store_upload_or_400(store, file, kind="poster")
    if crop:
        ratio = _poster_ratio(runtime, metadata.content_type)
        url = await _crop_source_to_poster(stored.url, stored.path, ratio, store, runtime)
    else:
        ratio = None
        url = _resource_url(store, stored.url)
    updated = await _apply_poster(store, repo, metadata_id, metadata, url)
    logger.info("poster uploaded", metadata_id=metadata_id, url=url, ratio=ratio, cropped=crop)
    return _display_response(runtime, updated)


@router.post("/{metadata_id}/adopt-thumb")
async def adopt_thumb(
    metadata_id: int,
    repo: RepoDep,
    runtime: RuntimeDep,
    source_url: Annotated[str, Query(description="要采用的图地址 (剧照或候选图)")],
) -> MetadataResponse:
    """把一张已有图 (剧照 / 候选图) 设为封面. **只换引用, 不复制文件**.

    源图可能还没本地化, 先确保它在 Resource 里落地, 然后把它 join 到 ``thumb_urls`` 头部.
    复制一份会让来回切换不断堆积内容相同的副本, 列表越切越长.

    与 ``upload-thumb`` 同一口径: 当前海报就是封面时, 海报跟着换.
    """
    metadata = await repo.get_metadata(metadata_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="影片不存在")

    store = runtime.resource_store
    # 候选图可能是外链, 也可能是已缓存的内部地址 (`/api/resources/{hash}`); 后者只有
    # `resolve_source` 认, `acquire` 只处理 http(s). 这一步只是确认图能拿到。
    resolved = await store.resolve_source(source_url, runtime.web_client)
    if resolved is None:
        raise HTTPException(status_code=400, detail="无法获取该图片")

    # 直接引用用户点的那条地址, 不重新拼: 重拼得到的字符串未必与池中一致 (查询串、
    # 外部 URL vs 内部地址), 会被当成新图追加, 池子越点越长.
    poster_equals_cover = await _poster_equals_cover(store, metadata)
    updated = await _apply_thumb(store, repo, metadata_id, metadata, source_url)
    if poster_equals_cover:
        updated = await _apply_poster(store, repo, metadata_id, updated, source_url)
    logger.info(
        "thumb adopted",
        metadata_id=metadata_id,
        source_url=source_url,
        poster_followed=poster_equals_cover,
    )
    return _display_response(runtime, updated)


@router.post("/{metadata_id}/adopt-poster")
async def adopt_poster(
    metadata_id: int,
    repo: RepoDep,
    runtime: RuntimeDep,
    source_url: Annotated[str, Query(description="要采用的图地址 (剧照或候选图)")],
) -> MetadataResponse:
    """把一张已有图 (剧照 / 候选图) 按海报比例裁剪后作为海报.

    直接从源图派生裁剪结果, **不另存一份原图** —— 否则来回切换会不断堆积副本.
    """
    metadata = await repo.get_metadata(metadata_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="影片不存在")

    store = runtime.resource_store
    # 候选图可能是外链, 也可能是已缓存的内部地址 (`/api/resources/{hash}`); 后者只有
    # `resolve_source` 认, `acquire` 只处理 http(s).
    resolved = await store.resolve_source(source_url, runtime.web_client)
    if resolved is None:
        raise HTTPException(status_code=400, detail="无法获取该图片")

    ratio = _poster_ratio(runtime, metadata.content_type)
    url = await _crop_source_to_poster(resolved.locator, resolved.path, ratio, store, runtime)
    updated = await _apply_poster(store, repo, metadata_id, metadata, url)
    logger.info("poster adopted", metadata_id=metadata_id, source_url=source_url, ratio=ratio)
    return _display_response(runtime, updated)


@router.delete("/{metadata_id}/images")
async def remove_image(
    metadata_id: int,
    repo: RepoDep,
    runtime: RuntimeDep,
    url: Annotated[str, Query(description="要移除的图片地址")],
    target: Annotated[Literal["thumb", "poster"], Query(description="封面还是海报候选池")] = "thumb",
) -> MetadataResponse:
    """从候选池移除一张图, 并**删掉本地文件**.

    只删这一张: 其它条目可能引用同一份文件 (裁剪派生以源为键), 所以删之前先确认没有别的
    条目还在用. 文件丢了不影响后续刮削 —— 重新刮削会重新下载.
    """
    metadata = await repo.get_metadata(metadata_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="影片不存在")

    # 选图框把封面、海报、剧照并列显示, 用户点的可能是任一来源; 因此从**所有**候选池里
    # 一并移除, 否则删完它还会在框里出现.
    thumbs = [item for item in metadata.thumb_urls if item != url]
    posters = [item for item in metadata.poster_urls if item != url]
    thumb_pool = [item for item in metadata.thumb_candidates if item != url]
    # 剧照按站点分组存, 只从被命中的那几组里剔除 (整组为空就删掉该站, 保持结构干净).
    fanart_groups: dict[str, list[str]] = {}
    fanart_changed = False
    for site, items in metadata.extrafanart_urls.items():
        kept = [item for item in items if item != url]
        if len(kept) != len(items):
            fanart_changed = True
        if kept:
            fanart_groups[site] = kept
    if (
        len(thumbs) == len(metadata.thumb_urls)
        and len(posters) == len(metadata.poster_urls)
        and len(thumb_pool) == len(metadata.thumb_candidates)
        and not fanart_changed
    ):
        raise HTTPException(status_code=404, detail="该图片不在候选池中")

    # 删掉的正好是正在用的那张时, 顺延到池里第一张 (池空则留空).
    thumb_urls = thumbs if url not in metadata.thumb_urls else ([thumb_pool[0]] if thumb_pool else [])

    # 从候选池移除图片不是手动改字段, 不该顺带锁定:
    # MANUAL 会把写入的每一列都加锁, 于是删一张图就把封面与海报一起锁上.
    updated = await repo.update_metadata(
        metadata_id,
        mode=WriteMode.AUTO,
        force=True,
        thumb_urls=thumb_urls,
        poster_urls=posters,
        thumb_candidates=thumb_pool,
        extrafanart_urls=fanart_groups,
    )
    assert updated is not None

    # 真正删盘: 文件丢了不影响后续, 重新刮削会重新下载.
    await _purge_image(runtime, url)

    logger.info("image removed", metadata_id=metadata_id, url=url, target=target)
    return _display_response(runtime, updated)


async def _purge_image(runtime: RuntimeDep, url: str) -> None:
    """删掉这张图对应的本地文件与记录; 找不到就当作已清理."""
    store = runtime.resource_store
    url_hash = internal_url_hash(url)
    if url_hash is None:
        return
    found = await store.get_by_url_hash(url_hash)
    if found is not None:
        await store.invalidate(found[0].url)


async def _translate_selected_text(updates: dict[str, object], runtime: RuntimeDep, content_type: ContentType) -> None:
    """Source picks refresh original text and re-run configured LLM translation; manual edits do not use this path."""
    if content_type == ContentType.WESTERN:
        return
    hot = runtime.config.hot
    if not hot.llm.enabled:
        return
    translator = build_translator(
        enabled=hot.llm.enabled,
        api_type=hot.llm.api_type,
        api_key=hot.llm.api_key,
        base_url=hot.llm.base_url,
        model=hot.llm.model,
        rate_limit=hot.llm.rate_limit,
        proxy=hot.network.proxy,
        system_prompt=hot.llm.system_prompt,
        field_prompts=hot.llm.field_prompts,
        cache=runtime.translation_cache,
        coalescer=runtime.translation_coalescer,
    )
    if translator is None:
        return
    for field, original_field in ((MetadataField.TITLE, "original_title"), (MetadataField.PLOT, "original_plot")):
        original = updates.get(original_field)
        if field.value not in updates or not isinstance(original, str) or not original:
            continue
        if field not in hot.llm.translate_fields:
            updates[field.value] = original
            continue
        target = hot.scraping.field_language.get(field)
        if target is None:
            updates[field.value] = original
            continue
        try:
            translated = await translator.translate(original, target, field, use_cache=False)
        except Exception:
            logger.warning("metadata merge translation failed", field=field.value)
            continue
        if translated:
            updates[field.value] = translated


@router.post("/{metadata_id}/merge")
async def merge_metadata(metadata_id: int, req: MergeRequest, repo: RepoDep, runtime: RuntimeDep) -> MetadataResponse:
    if not req.selections:
        raise HTTPException(status_code=400, detail="未提供选择项")

    metadata = await repo.get_metadata(metadata_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="影片不存在")

    # 按 selections 从 raw 合并字段; title/plot 的来源选择同时刷新原标题/原剧情并重跑翻译.
    try:
        updates = compute_merge_updates(metadata.raw, metadata.field_sources, req.selections)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    if not updates:
        raise HTTPException(status_code=400, detail="没有有效的选择项")
    await _translate_selected_text(updates, runtime, metadata.content_type)

    # 写回元数据
    updated = await repo.update_metadata(metadata_id, mode=WriteMode.MANUAL, **cast("MetadataFields", updates))
    assert updated is not None
    logger.info("metadata merged", metadata_id=metadata_id, selections=req.selections)
    return _display_response(runtime, updated)


#: 站点域名片段 → 展示名. 顺序敏感: 更具体的域名放前面.
_SOURCE_LABELS: tuple[tuple[str, str], ...] = (
    ("caribbeancompr", "Caribbeancom"),
    ("caribbeancom", "Caribbeancom"),
    ("1pondo", "1Pondo"),
    ("10musume", "10Musume"),
    ("pacopacomama", "Pacopacomama"),
    ("tokyo-hot", "Tokyo-Hot"),
    ("tokyohot", "Tokyo-Hot"),
    ("heyzo", "HEYZO"),
    ("d2pass", "D2Pass"),
    ("aventertainments", "AVEntertainments"),
    ("javbus", "JavBus"),
    ("javdb", "JavDb"),
    ("jdbstatic", "JavDb"),
    ("dmm.co.jp", "DMM"),
    ("airav", "Airav"),
    ("themoviedb", "TheMovieDb"),
    ("gfriends", "GFriends"),
    ("tushyraw", "TushyRaw"),
    ("tushy", "Tushy"),
    ("theporndb", "ThePornDB"),
    ("wikipedia", "Wikipedia"),
    ("wikimedia", "Wikipedia"),
    ("minnano", "Minnano"),
)


def _source_label(url: str, *, origin: str | None, raw_url: str | None = None) -> str:
    """图片来源名.

    优先看溯源地址 (裁切件的 ``meta.src``); 没有就看记录本身的 url —— 刮削缓存下来的原图,
    其 url 就是站点地址. 两者都认不出时按大类标注.
    """
    for candidate in (origin, raw_url, url):
        if not candidate:
            continue
        lower = candidate.casefold()
        for needle, label in _SOURCE_LABELS:
            if needle in lower:
                return label

    if raw_url is not None and raw_url.startswith("upload:"):
        return "自定义"
    if origin is not None:
        return "裁切"
    if url.startswith("/api/resources/"):
        return "自定义"
    return ""


#: 候选图尺寸解析的并发上限; 同时最多下载这么多张.
_IMAGE_INFO_CONCURRENCY = 5


@router.post("/{metadata_id}/image-info")
async def image_info(
    metadata_id: int,
    req: ImageInfoRequest,
    repo: RepoDep,
    runtime: RuntimeDep,
) -> ImageInfoResponse:
    """候选图的来源与分辨率, 供选图框展示.

    内部地址会溯源到原始站点 (裁切件的 ``meta.src``、上传件的 ``meta.kind``), 拿不到就按
    大类标注. 分辨率只对已本地化的图有效, 未下载的返回空.
    """
    metadata = await repo.get_metadata(metadata_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="影片不存在")

    store = runtime.resource_store
    # 并发解析: 外链要逐张下载才能量尺寸, 串行时一张两秒、六张就十几秒, 详情页会一直转圈.
    # 上限 5 张: 候选图可能几十张, 一次全发会把带宽占满, 反而拖慢首张的完成时间.
    semaphore = asyncio.Semaphore(_IMAGE_INFO_CONCURRENCY)

    async def resolve(url: str) -> tuple[str | None, str | None, tuple[int, int] | None]:
        async with semaphore:
            return await _resolve_image_origin(store, url, runtime.web_client)

    resolved = await asyncio.gather(*(resolve(url) for url in req.urls))
    items = [
        ImageInfo(
            url=url,
            source=_source_label(url, origin=origin, raw_url=raw_url),
            width=size[0] if size is not None else None,
            height=size[1] if size is not None else None,
            kind=_image_kind(url, metadata, raw_url=raw_url),
        )
        for url, (origin, raw_url, size) in zip(req.urls, resolved, strict=True)
    ]
    return ImageInfoResponse(items=items)


def _image_kind(url: str, metadata: Metadata, *, raw_url: str | None) -> ImageKind:
    """候选图归类: 上传件 → 自定义; 剧照池里的 → 剧照; 其余刮削封面."""
    if raw_url is not None and raw_url.startswith("upload:"):
        return ImageKind.CUSTOM
    if url in metadata.extrafanart:
        return ImageKind.STILL
    if url in metadata.thumb_urls or url in metadata.poster_urls:
        return ImageKind.COVER
    return ImageKind.OTHER


async def _resolve_image_origin(
    store: ResourceStore, url: str, client: WebClient | None = None
) -> tuple[str | None, str | None, tuple[int, int] | None]:
    """候选图 → (溯源地址, 记录原 url, 像素尺寸).

    外链会先**下载到本地**再量尺寸 —— 站点图在浏览器里读 ``naturalWidth`` 会被跨域拦,
    统一在服务端落地后两边都能用, 之后显示也走本地地址.
    """
    url_hash = internal_url_hash(url)
    if url_hash is None:
        # 外链: 先落地再读尺寸; 失败就当没有尺寸, 不影响展示.
        if client is None or not url.startswith(("http://", "https://")):
            return (None, None, None)
        local = await store.acquire(url, client)
        if local is None:
            return (None, None, None)
        return (None, url, probe_oriented_size(local))

    found = await store.get_by_url_hash(url_hash)
    if found is None:
        return (None, None, None)
    record, path = found
    size = probe_oriented_size(path)
    meta = record.meta or {}
    origin = meta.get("src") if isinstance(meta, dict) else None
    return (origin if isinstance(origin, str) else None, record.url, size)
