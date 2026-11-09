from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, cast

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import TypeAdapter

from ...aggregate import compute_merge_updates
from ...db.embeddings import EmbeddingKind
from ...db.models import Metadata, MetadataSortField, SavedQueryEntity, SortOrder, TaskType
from ...db.repo_types import WriteMode
from ...db.repos.media import file_phase_of
from ...enums import MetadataField
from ...handlers import ScrapePayload
from ...llm import build_translator
from ...media import manual_crop_image
from ...media.images import crop_poster
from ...media.resource_store import RESOURCE_URL_PREFIX, ResourceStore
from ...media.upload import StoredUpload, UploadRejected, store_upload
from ...parsing import ContentType, summarize_file_phases
from ...parsing import FilePhaseSummary as ParsedFilePhase
from ...semantic import SemanticUnavailable
from ...utils.dates import normalize_calendar_date
from ...utils.model import to_resp
from ..deps import RepoDep, RuntimeDep
from ..models import (
    CommentResponse,
    CropPosterRequest,
    FilePhaseSummary,
    MergeRequest,
    MetadataBatchDeleteResponse,
    MetadataBatchIdsRequest,
    MetadataBatchScrapeRequest,
    MetadataBatchScrapeResponse,
    MetadataDetailResponse,
    MetadataListResponse,
    MetadataLocksRequest,
    MetadataResponse,
    MetadataUserTagsRequest,
    PartialMetadata,
    SemanticHitInfo,
    UserTagLinksResponse,
    UserTagResponse,
    to_media_file_response,
)
from ..support.metadata_filters import MetadataFilters, parse_metadata_filters, require_known_definition
from .saved_queries import resolve_saved_query_id_subquery

if TYPE_CHECKING:
    from ...db.repo_types import MetadataFields

logger = structlog.get_logger()

router = APIRouter(prefix="/metadata", tags=["metadata"])


async def build_metadata_responses(
    repo: RepoDep, items: Sequence[Metadata], runtime: RuntimeDep
) -> list[MetadataResponse]:
    """列表与语义检索共用: 补 file_count / file_phase 聚合."""
    summaries = await repo.summarize_media_by_metadata(items)
    responses: list[MetadataResponse] = []
    for m in items:
        summary = summaries.get(m.id) if m.id is not None else None
        response = to_resp(MetadataResponse, m).model_copy(update={"tags": runtime.tag_mappings.map_tags(m.tags)})
        if summary is not None:
            response = response.model_copy(
                update={
                    "file_count": summary.file_count,
                    "file_phase": _file_phase_resp(summary.phase),
                }
            )
        responses.append(response)
    return responses


def _file_phase_resp(summary: ParsedFilePhase) -> FilePhaseSummary:
    return FilePhaseSummary(
        has_subtitle=summary.has_subtitle,
        censored=summary.censored,
        uncensored=summary.uncensored,
        mosaics=list(summary.mosaics),
        definition=summary.definition,
        ltx=summary.ltx,
    )


@router.get("/schema")
async def get_metadata_schema() -> dict:
    return TypeAdapter(PartialMetadata).json_schema()


@router.get("")
async def list_metadata(
    repo: RepoDep,
    runtime: RuntimeDep,
    filters: Annotated[MetadataFilters, Depends(parse_metadata_filters)],
    search: Annotated[str | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    sort_by: Annotated[MetadataSortField, Query(description="Sort field")] = MetadataSortField.UPDATED_AT,
    order: Annotated[SortOrder, Query(description="Sort order")] = SortOrder.DESC,
    semantic_query: Annotated[
        str | None, Query(min_length=1, max_length=200, description="按画面描述排序; 提供时忽略 sort_by / order")
    ] = None,
    semantic_kind: Annotated[Literal["all", "cover", "still"], Query(description="参与语义排序的图片来源")] = "all",
) -> MetadataListResponse:
    definition = require_known_definition(filters.definition)
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
    return MetadataListResponse(items=await build_metadata_responses(repo, items, runtime), total=total)


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


@router.post("/batch/delete")
async def batch_delete_metadata(req: MetadataBatchIdsRequest, repo: RepoDep) -> MetadataBatchDeleteResponse:
    """级联行为与单条删除一致."""
    deleted, missing = await repo.batch_delete_metadata(req.ids)
    logger.info("metadata batch deleted", deleted=deleted, missing=missing)
    return MetadataBatchDeleteResponse(deleted=deleted, missing=missing)


@router.post("/batch/scrape", status_code=202)
async def batch_scrape_metadata(req: MetadataBatchScrapeRequest, repo: RepoDep) -> MetadataBatchScrapeResponse:
    """以各自 Metadata ID / ThePornDB UUID 重新刮削."""
    missing = 0
    payloads: list[ScrapePayload] = []
    for metadata_id in req.ids:
        metadata = await repo.get_metadata(metadata_id)
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
    logger.info("metadata batch scrape submitted", submitted=len(task_ids), missing=missing)
    return MetadataBatchScrapeResponse(submitted=len(task_ids), missing=missing, task_ids=task_ids)


@router.post("/batch/user-tags")
async def batch_metadata_user_tags(req: MetadataUserTagsRequest, repo: RepoDep) -> UserTagLinksResponse:
    """把一组用户标签应用到一组影片; 未知标签 id 返回 404, 不存在的影片 id 计入 missing."""
    try:
        result = await repo.apply_metadata_user_tags(req.ids, req.user_tag_ids, action=req.action)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    logger.info(
        "metadata user tags applied",
        action=req.action,
        changed=result.changed,
        unchanged=result.unchanged,
        missing=result.missing,
    )
    return UserTagLinksResponse(changed=result.changed, unchanged=result.unchanged, missing=result.missing)


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
    tag_ids = runtime.tag_mappings.map_id_map(tag_ids)
    return MetadataDetailResponse(
        metadata=to_resp(MetadataResponse, metadata).model_copy(
            update={
                "tags": runtime.tag_mappings.map_tags(metadata.tags),
                "file_count": len(files),
                "file_phase": _file_phase_resp(
                    summarize_file_phases((file_phase_of(f) for f in files), metadata.content_type)
                ),
            }
        ),
        files=[to_media_file_response(f, metadata.content_type) for f in files],
        user_tags=[to_resp(UserTagResponse, t) for t in user_tags],
        comments=[to_resp(CommentResponse, c) for c in comments],
        actor_ids=actor_ids,
        actor_genders=actor_genders,
        director_ids=director_ids,
        tag_ids=tag_ids,
        studio_id=studio_id,
        publisher_id=publisher_id,
        series_id=series_id,
        series_ids=series_ids,
    )


@router.patch("/{metadata_id}")
async def update_metadata(
    metadata_id: int, req: PartialMetadata, repo: RepoDep, runtime: RuntimeDep
) -> MetadataResponse:
    updates = cast("MetadataFields", {k: v for k, v in req.model_dump().items() if v is not None})
    if not updates:
        raise HTTPException(status_code=422, detail="没有需要修改的字段")
    if "release" in updates:
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
    return to_resp(MetadataResponse, metadata).model_copy(update={"tags": runtime.tag_mappings.map_tags(metadata.tags)})


@router.put("/{metadata_id}/locks")
async def set_metadata_locks(
    metadata_id: int, req: MetadataLocksRequest, repo: RepoDep, runtime: RuntimeDep
) -> MetadataResponse:
    """整体替换锁定字段集合."""
    metadata = await repo.set_metadata_locks(metadata_id, req.fields)
    if metadata is None:
        raise HTTPException(status_code=404, detail="影片不存在")
    logger.info("metadata locks updated", metadata_id=metadata_id, fields=[str(field) for field in req.fields])
    return to_resp(MetadataResponse, metadata).model_copy(update={"tags": runtime.tag_mappings.map_tags(metadata.tags)})


@router.delete("/{metadata_id}", status_code=204)
async def delete_metadata(metadata_id: int, repo: RepoDep) -> None:
    deleted = await repo.delete_metadata(metadata_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="影片不存在")
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

    # 写回 poster_urls, 原有条目保留在后面.
    existing = [url for url in metadata.poster_urls if url != poster_url]
    updated = await repo.update_metadata(metadata_id, mode=WriteMode.MANUAL, poster_urls=[poster_url, *existing])
    assert updated is not None
    logger.info(
        "poster cropped",
        metadata_id=metadata_id,
        box=box,
        rotation=req.rotation,
        source_url=source_url,
        poster_url=poster_url,
    )
    return to_resp(MetadataResponse, updated).model_copy(update={"tags": runtime.tag_mappings.map_tags(updated.tags)})


def _resource_url(store: ResourceStore, locator: str) -> str:
    """locator → 前端可访问的内部图片地址."""
    return f"{RESOURCE_URL_PREFIX}/{store.url_hash(locator)}"


#: 上传图落到 data_dir/uploads, 与刮削下载的 Resource 分开; 原图与旧地址都保留.
UPLOAD_KIND_POSTER = "poster"
UPLOAD_KIND_THUMB = "thumb"


@router.post("/{metadata_id}/upload-image")
async def upload_image(
    metadata_id: int,
    repo: RepoDep,
    runtime: RuntimeDep,
    file: Annotated[bytes, Body(media_type="application/octet-stream")],
    target: Annotated[Literal["poster", "thumb"], Query(description="作为海报还是封面")] = "poster",
) -> MetadataResponse:
    """上传自定义图片, 作为海报或封面.

    海报: 存好后按当前比例**自动裁剪一次**, 结果插到 ``poster_urls`` 头部; 用户不满意可以
    再调 ``crop-poster`` 手工调整. 封面: 保持原图与原比例, 直接插到 ``thumb_urls`` 头部.

    **不删除**原有地址, 只把新地址放到最前面; 想退回原来的图, 删掉首项即可.
    """
    metadata = await repo.get_metadata(metadata_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="影片不存在")

    store = runtime.resource_store
    try:
        stored = await store_upload(store, file, kind=target)
    except UploadRejected as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if target == UPLOAD_KIND_THUMB:
        internal = _resource_url(store, stored.url)
        existing = [url for url in metadata.thumb_urls if url != internal]
        updated = await repo.update_metadata(metadata_id, mode=WriteMode.MANUAL, thumb_urls=[internal, *existing])
        assert updated is not None
        logger.info("thumb uploaded", metadata_id=metadata_id, url=internal)
    else:
        ratio = (
            runtime.config.hot.scraping.uncensored_poster_ratio
            if metadata.content_type == ContentType.UNCENSORED
            else runtime.config.hot.scraping.poster_ratio
        )
        poster_url = await _crop_upload_to_poster(stored, ratio, store, runtime)
        existing = [url for url in metadata.poster_urls if url != poster_url]
        updated = await repo.update_metadata(metadata_id, mode=WriteMode.MANUAL, poster_urls=[poster_url, *existing])
        assert updated is not None
        logger.info("poster uploaded", metadata_id=metadata_id, url=poster_url, ratio=ratio)

    return to_resp(MetadataResponse, updated).model_copy(update={"tags": runtime.tag_mappings.map_tags(updated.tags)})


async def _crop_upload_to_poster(stored: StoredUpload, ratio: float, store: ResourceStore, runtime: RuntimeDep) -> str:
    """把上传图按海报比例裁一次 (人脸优先), 返回内部 URL."""
    source_url = _resource_url(store, stored.url)

    async def producer(dest: Path) -> bool:
        return crop_poster(
            stored.path,
            dest,
            poster_ratio=ratio,
            jpeg_quality=runtime.config.hot.scraping.jpeg_quality,
            detect_face=runtime.config.hot.scraping.poster_crop_detect_face,
        )

    crop_res = await store.acquire_derived(stored.url, "crop", f"upload:{ratio:.4f}", producer)
    if crop_res is None:
        # 裁剪失败就退回原图, 不让上传直接失败.
        return source_url
    return _resource_url(store, crop_res.url)


@router.post("/{metadata_id}/adopt-image")
async def adopt_image(
    metadata_id: int,
    repo: RepoDep,
    runtime: RuntimeDep,
    source_url: Annotated[str, Query(description="要采用的图地址 (剧照或其它候选图)")],
    target: Annotated[Literal["poster", "thumb"], Query(description="作为海报还是封面")] = "thumb",
) -> MetadataResponse:
    """把一张已有图 (剧照 / 其它候选图) 设为封面或海报.

    由**服务端**下载那张图再按上传流程处理 —— 站点图片不带 CORS 头, 前端直接 fetch 会被
    浏览器拦下 (Failed to fetch).
    """
    metadata = await repo.get_metadata(metadata_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="影片不存在")

    store = runtime.resource_store
    path = await store.acquire(source_url, runtime.web_client)
    if path is None:
        raise HTTPException(status_code=400, detail="无法获取该图片")
    data = path.read_bytes()

    try:
        stored = await store_upload(store, data, kind=target)
    except UploadRejected as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if target == UPLOAD_KIND_THUMB:
        internal = _resource_url(store, stored.url)
        existing = [url for url in metadata.thumb_urls if url != internal]
        updated = await repo.update_metadata(metadata_id, mode=WriteMode.MANUAL, thumb_urls=[internal, *existing])
    else:
        ratio = (
            runtime.config.hot.scraping.uncensored_poster_ratio
            if metadata.content_type == ContentType.UNCENSORED
            else runtime.config.hot.scraping.poster_ratio
        )
        poster_url = await _crop_upload_to_poster(stored, ratio, store, runtime)
        existing = [url for url in metadata.poster_urls if url != poster_url]
        updated = await repo.update_metadata(metadata_id, mode=WriteMode.MANUAL, poster_urls=[poster_url, *existing])

    assert updated is not None
    logger.info("image adopted", metadata_id=metadata_id, source_url=source_url, target=target)
    return to_resp(MetadataResponse, updated).model_copy(update={"tags": runtime.tag_mappings.map_tags(updated.tags)})


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
    return to_resp(MetadataResponse, updated).model_copy(update={"tags": runtime.tag_mappings.map_tags(updated.tags)})
