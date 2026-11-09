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
from ...media.images import crop_poster, probe_oriented_size
from ...media.resource_store import RESOURCE_URL_PREFIX, ResourceStore, internal_url_hash
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
    ImageInfo,
    ImageInfoRequest,
    ImageInfoResponse,
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


async def _apply_thumb(
    store: ResourceStore, repo: RepoDep, metadata_id: int, thumb_urls: list[str], url: str
) -> MetadataResponse:
    """把 ``url`` 设为当前封面. 候选池顺序保持不变.

    ``thumb_url`` 只是指向某张图的引用: 换封面改引用即可. 列表顺序是用户认图的编号,
    每换一次就重排会让人看不出改了什么.
    """
    # 源图可能已在池里 (用户点的就是候选图), 这时必须沿用池中那条地址 —— 重新拼一遍
    # 得到的字符串未必相同 (例如带查询串), 会被当成新图追加, 池子越点越长.
    target = next((item for item in thumb_urls if item == url), url)
    cleaned = await _dedupe_by_content(store, thumb_urls, target)
    # 已在池中的保持原位; 新图追加到末尾.
    ordered = cleaned if target in cleaned else [*cleaned, target]
    updated = await repo.update_metadata(metadata_id, mode=WriteMode.MANUAL, thumb_urls=ordered, thumb_selected=target)
    assert updated is not None
    return to_resp(MetadataResponse, updated)


async def _apply_poster(
    store: ResourceStore, repo: RepoDep, metadata_id: int, poster_urls: list[str], url: str
) -> MetadataResponse:
    """把 ``url`` 设为当前海报. 候选池顺序保持不变, 理由同 ``_apply_thumb``."""
    cleaned = await _dedupe_by_content(store, poster_urls, url)
    ordered = cleaned if url in cleaned else [*cleaned, url]
    updated = await repo.update_metadata(metadata_id, mode=WriteMode.MANUAL, poster_urls=ordered, poster_selected=url)
    assert updated is not None
    return to_resp(MetadataResponse, updated)


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
    """上传自定义封面. 原样使用, 不裁剪. 旧封面保留在列表后面."""
    metadata = await repo.get_metadata(metadata_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="影片不存在")

    store = runtime.resource_store
    stored = await _store_upload_or_400(store, file, kind="thumb")
    url = _resource_url(store, stored.url)
    updated = await _apply_thumb(store, repo, metadata_id, metadata.thumb_urls, url)
    logger.info("thumb uploaded", metadata_id=metadata_id, url=url)
    return updated.model_copy(update={"tags": runtime.tag_mappings.map_tags(updated.tags)})


@router.post("/{metadata_id}/upload-poster")
async def upload_poster(
    metadata_id: int,
    repo: RepoDep,
    runtime: RuntimeDep,
    file: Annotated[bytes, Body(media_type="application/octet-stream")],
) -> MetadataResponse:
    """上传自定义图片作为海报.

    存好后按当前比例**自动裁剪一次** (人脸优先定位主体); 不满意可以再调 ``crop-poster``
    手工调整. 旧海报保留在列表后面.
    """
    metadata = await repo.get_metadata(metadata_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="影片不存在")

    store = runtime.resource_store
    stored = await _store_upload_or_400(store, file, kind="poster")
    ratio = _poster_ratio(runtime, metadata.content_type)
    url = await _crop_source_to_poster(stored.url, stored.path, ratio, store, runtime)
    updated = await _apply_poster(store, repo, metadata_id, metadata.poster_urls, url)
    logger.info("poster uploaded", metadata_id=metadata_id, url=url, ratio=ratio)
    return updated.model_copy(update={"tags": runtime.tag_mappings.map_tags(updated.tags)})


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
    updated = await _apply_thumb(store, repo, metadata_id, metadata.thumb_urls, source_url)
    logger.info("thumb adopted", metadata_id=metadata_id, source_url=source_url)
    return updated.model_copy(update={"tags": runtime.tag_mappings.map_tags(updated.tags)})


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
    updated = await _apply_poster(store, repo, metadata_id, metadata.poster_urls, url)
    logger.info("poster adopted", metadata_id=metadata_id, source_url=source_url, ratio=ratio)
    return updated.model_copy(update={"tags": runtime.tag_mappings.map_tags(updated.tags)})


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
    fanart = [item for item in metadata.extrafanart if item != url]
    if (
        len(thumbs) == len(metadata.thumb_urls)
        and len(posters) == len(metadata.poster_urls)
        and len(fanart) == len(metadata.extrafanart)
    ):
        raise HTTPException(status_code=404, detail="该图片不在候选池中")

    thumb_selected = metadata.thumb_selected
    if thumb_selected == url:
        thumb_selected = thumbs[0] if thumbs else None
    poster_selected = metadata.poster_selected
    if poster_selected == url:
        poster_selected = posters[0] if posters else None

    updated = await repo.update_metadata(
        metadata_id,
        mode=WriteMode.MANUAL,
        thumb_urls=thumbs,
        poster_urls=posters,
        thumb_selected=thumb_selected,
        poster_selected=poster_selected,
    )
    assert updated is not None

    # 真正删盘: 文件丢了不影响后续, 重新刮削会重新下载.
    await _purge_image(runtime, url)

    logger.info("image removed", metadata_id=metadata_id, url=url, target=target)
    return to_resp(MetadataResponse, updated).model_copy(update={"tags": runtime.tag_mappings.map_tags(updated.tags)})


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
    return to_resp(MetadataResponse, updated).model_copy(update={"tags": runtime.tag_mappings.map_tags(updated.tags)})


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
    items: list[ImageInfo] = []
    for url in req.urls:
        origin, raw_url, size = await _resolve_image_origin(store, url)
        width, height = size if size is not None else (None, None)
        items.append(
            ImageInfo(
                url=url,
                source=_source_label(url, origin=origin, raw_url=raw_url),
                width=width,
                height=height,
            )
        )
    return ImageInfoResponse(items=items)


async def _resolve_image_origin(
    store: ResourceStore, url: str
) -> tuple[str | None, str | None, tuple[int, int] | None]:
    """内部地址 → (溯源地址, 记录原 url, 像素尺寸).

    外链没有本地记录, 三个值都是 None; 内部地址若尚未缓存, 同样拿不到尺寸.
    """
    url_hash = internal_url_hash(url)
    if url_hash is None:
        return (None, None, None)
    found = await store.get_by_url_hash(url_hash)
    if found is None:
        return (None, None, None)
    record, path = found
    size = probe_oriented_size(path)
    meta = record.meta or {}
    origin = meta.get("src") if isinstance(meta, dict) else None
    return (origin if isinstance(origin, str) else None, record.url, size)
