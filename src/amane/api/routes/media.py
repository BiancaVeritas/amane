from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated, cast

import structlog
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from ...db.models import MediaFile, MediaFileStatus, MediaSortField, SortOrder
from ...parsing import ContentType, Mosaic, canonical_definition
from ..deps import RepoDep
from ..models import (
    MediaBatchDeleteRequest,
    MediaBatchDeleteResponse,
    MediaFileResponse,
    MediaFileUpdateRequest,
    MediaListResponse,
    to_media_file_response,
)

if TYPE_CHECKING:
    from ...db.repo_types import MediaFileUpdates
    from ...db.repository import Repository

logger = structlog.get_logger()

router = APIRouter(prefix="/media", tags=["media"])


def _require_known_definition(definition: str | None) -> str | None:
    if definition is None:
        return None
    # 读侧接受旧写法: 库里可能还留着 4K / HD, 客户端也可能发旧值.
    canonical = canonical_definition(definition)
    if canonical is None:
        raise HTTPException(status_code=422, detail=f"未知分辨率: {definition}")
    return canonical


async def _phase_responses(repo: Repository, items: Sequence[MediaFile]) -> list[MediaFileResponse]:
    """文件行的有码 / 无码取自所属条目类型, 因此要批量补一次条目类型."""
    types = await repo.content_types_by_metadata_ids([m.metadata_id for m in items if m.metadata_id is not None])
    return [to_media_file_response(m, types.get(m.metadata_id) if m.metadata_id is not None else None) for m in items]


@router.get("")
async def list_media(
    repo: RepoDep,
    status: Annotated[MediaFileStatus | None, Query(description="Filter by media file status")] = None,
    search: Annotated[str | None, Query(description="Search by path or number")] = None,
    library_id: Annotated[int | None, Query(description="Filter by owning library")] = None,
    has_subtitle: Annotated[bool | None, Query(description="Filter by subtitle marker")] = None,
    mosaic: Annotated[Mosaic | None, Query(description="Filter by mosaic marker")] = None,
    uncensored: Annotated[
        bool | None, Query(description="Filter by uncensored (own marker or uncensored number content type)")
    ] = None,
    definition: Annotated[str | None, Query(description="Filter by definition (8K/4K/1080p/…)")] = None,
    content_type: Annotated[ContentType | None, Query(description="Filter by owning number content type")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort_by: Annotated[MediaSortField, Query(description="Sort field")] = MediaSortField.UPDATED_AT,
    order: Annotated[SortOrder, Query(description="Sort order")] = SortOrder.DESC,
) -> MediaListResponse:
    definition = _require_known_definition(definition)
    status_filter = [status] if status is not None else None
    items = await repo.list_media_files(
        status_filter,
        limit,
        offset,
        search,
        library_id,
        sort_by,
        order,
        has_subtitle=has_subtitle,
        mosaic=mosaic,
        uncensored=uncensored,
        definition=definition,
        content_type=content_type,
    )
    total = await repo.count_media_files(
        status=status_filter,
        search=search,
        library_id=library_id,
        has_subtitle=has_subtitle,
        mosaic=mosaic,
        uncensored=uncensored,
        definition=definition,
        content_type=content_type,
    )
    return MediaListResponse(items=await _phase_responses(repo, items), total=total)


def _relative_to_library(path: str, roots: Sequence[str]) -> str:
    """把绝对路径截成库内相对路径.

    哪个库根前缀最长就用哪个 (库可能嵌套); 都匹配不上时原样返回完整路径,
    总比猜错一段更好.
    """
    if not path:
        return ""
    normalized = path.replace("\\", "/").rstrip("/")
    best = ""
    for root in roots:
        prefix = root.replace("\\", "/").rstrip("/")
        if prefix and normalized.casefold().startswith(prefix.casefold()):
            candidate = normalized[len(prefix) :].lstrip("/")
            if len(candidate) < len(normalized) - len(best):
                best = candidate
    return best or normalized



class ScrapedFilenameItem(BaseModel):
    """一条登记: 原始名 + 整理后名 + 两侧路径."""

    filename: str
    renamed: str = Field(default="", description="整理后的文件名; 空表示还没整理")
    source_path: str = Field(default="", description="入库时所在路径")
    target_path: str = Field(default="", description="整理后的路径; 空表示还没整理")


@router.get("/scraped-filenames")
async def list_scraped_filenames(
    repo: RepoDep,
    library_id: Annotated[int | None, Query(description="只看某个库")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[ScrapedFilenameItem]:
    """已登记的「刮过就不再刮」文件名."""
    rows = await repo.list_scraped_filenames(library_id=library_id, limit=limit)
    # 相对路径在这里算: 前端不知道库根, 自己截字符串迟早出错.
    roots = [str(lib.path) for lib in await repo.list_libraries() if lib.path]
    return [
        ScrapedFilenameItem(
            filename=name,
            renamed=renamed,
            source_path=_relative_to_library(source, roots),
            target_path=_relative_to_library(target, roots),
        )
        for name, renamed, source, target in rows
    ]


@router.delete("/scraped-filenames")
async def clear_scraped_filenames(
    repo: RepoDep,
    library_id: Annotated[int | None, Query(description="只清某个库; 不给则全清")] = None,
    filename: Annotated[str | None, Query(description="只清这一条; 不给则按 library_id 决定范围")] = None,
) -> dict[str, int]:
    """清掉登记, 让同名文件重新可刮. 给了 filename 就只删那一条."""
    if filename is not None:
        removed = await repo.forget_scraped_filename(filename, library_id=library_id)
        return {"cleared": removed}
    cleared = await repo.forget_scraped_filenames(library_id=library_id)
    return {"cleared": cleared}


@router.post("/batch/delete")
async def batch_delete_media(req: MediaBatchDeleteRequest, repo: RepoDep) -> MediaBatchDeleteResponse:
    """批量删除媒体文件记录; 不存在的 id 计入 missing 而不是报错."""
    deleted, missing = await repo.batch_delete_media_files(req.ids)
    logger.info("media files batch deleted", deleted=deleted, missing=missing)
    return MediaBatchDeleteResponse(deleted=deleted, missing=missing)


@router.get("/{media_id}")
async def get_media(media_id: int, repo: RepoDep) -> MediaFileResponse:
    media = await repo.get_media_file(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="媒体文件不存在")
    return (await _phase_responses(repo, [media]))[0]


@router.patch("/{media_id}")
async def update_media(media_id: int, req: MediaFileUpdateRequest, repo: RepoDep) -> MediaFileResponse:
    updates = cast("MediaFileUpdates", req.model_dump(exclude_unset=True))
    if not updates:
        raise HTTPException(status_code=422, detail="没有需要修改的字段")
    media = await repo.update_media_file(media_id, **updates)
    if media is None:
        raise HTTPException(status_code=404, detail="媒体文件不存在")
    logger.info("media file updated", media_id=media_id, fields=list(updates.keys()))
    return (await _phase_responses(repo, [media]))[0]


@router.delete("/{media_id}", status_code=204)
async def delete_media(media_id: int, repo: RepoDep):
    deleted = await repo.delete_media_file(media_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="媒体文件不存在")
    logger.info("media file deleted", media_id=media_id)
    return Response(status_code=204)
