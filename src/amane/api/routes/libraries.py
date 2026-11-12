from pathlib import Path
from typing import TYPE_CHECKING, cast

import structlog
from fastapi import APIRouter, HTTPException, Response

from ...db.models import TaskType
from ...handlers import RefreshPayload, ScanMode
from ...utils.model import to_resp
from ..deps import RepoDep, RuntimeDep
from ..models import (
    LibraryCreateRequest,
    LibraryListResponse,
    LibraryResponse,
    LibraryUpdateRequest,
    PathTemplateSchemaResponse,
    path_template_schema,
)
from ..support.path_validation import validate_directory_path

if TYPE_CHECKING:
    from ...db.repo_types import LibraryUpdates

logger = structlog.get_logger()

router = APIRouter(prefix="/libraries", tags=["libraries"])


@router.get("/path-template-schema")
async def get_path_template_schema() -> PathTemplateSchemaResponse:
    """与 resolve_paths 同源."""
    return path_template_schema()


@router.get("")
async def list_libraries(repo: RepoDep) -> LibraryListResponse:
    items = await repo.list_libraries()
    return LibraryListResponse(items=[to_resp(LibraryResponse, lib) for lib in items])


@router.post("", status_code=201)
async def create_library(req: LibraryCreateRequest, repo: RepoDep, runtime: RuntimeDep) -> LibraryResponse:
    # 多路径配置: 兼容旧客户端的 path / organize_path, 新客户端优先传列表。
    scan_paths = list(dict.fromkeys(p.strip() for p in (req.scan_paths or [req.path]) if p and p.strip()))
    organize_paths = list(
        dict.fromkeys(
            p.strip()
            for p in (req.organize_paths or ([req.organize_path] if req.organize_path else []))
            if p and p.strip()
        )
    )
    if not scan_paths:
        raise HTTPException(status_code=422, detail="至少需要设置一个扫描路径")
    for scan_path in scan_paths:
        await validate_directory_path(scan_path, runtime.safe_dirs)
    for organize_path in organize_paths:
        await validate_directory_path(organize_path, runtime.safe_dirs)
    name = req.name or Path(scan_paths[0]).name

    try:
        lib = await repo.create_library(
            name=name,
            path=scan_paths[0],
            scan_paths=scan_paths,
            organize_path=organize_paths[0] if len(organize_paths) == 1 else None,
            organize_paths=organize_paths,
            automation=req.automation,
            auto_organize=req.auto_organize,
            ingest=req.ingest,
            cloud_path=req.cloud_path,
            recursive=req.recursive,
            patterns=req.patterns,
            move_mode=req.move_mode,
            video_template=req.video_template,
            link_template=req.link_template,
            link_mode=req.link_mode,
            strm_content_template=req.strm_content_template,
            thumb_template=req.thumb_template,
            poster_template=req.poster_template,
            fanart_template=req.fanart_template,
            extrafanart_template=req.extrafanart_template,
            nfo_template=req.nfo_template,
            trailer_template=req.trailer_template,
            subtitle_template=req.subtitle_template,
            subtitle_extensions=req.subtitle_extensions,
            write_nfo=req.write_nfo,
            copy_resources=req.copy_resources,
            trailer_pattern=req.trailer_pattern,
            blacklist_patterns=req.blacklist_patterns,
            min_file_size=req.min_file_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    assert lib.id is not None

    logger.info(
        "library created",
        library_id=lib.id,
        name=name,
        path=scan_paths[0],
        scan_paths=scan_paths,
        automation=req.automation,
        ingest=req.ingest,
    )

    if runtime.watcher_service:
        runtime.watcher_service.sync_library(lib)

    # 一个 REFRESH 任务按 paths 顺序串行扫描全部扫描根目录。
    if req.scan:
        await repo.create_task(
            TaskType.REFRESH,
            RefreshPayload(
                library_id=lib.id,
                recursive=req.recursive,
                patterns=req.patterns,
                path=lib.scan_paths[0] if lib.scan_paths else lib.path,
                paths=lib.scan_paths or [lib.path],
                scan={ScanMode.add},
                scrape=set(),
            ),
        )

    return to_resp(LibraryResponse, lib)


@router.get("/{library_id}")
async def get_library(library_id: int, repo: RepoDep) -> LibraryResponse:
    lib = await repo.get_library(library_id)
    if lib is None:
        raise HTTPException(status_code=404, detail="媒体库不存在")
    return to_resp(LibraryResponse, lib)


@router.patch("/{library_id}")
async def update_library(
    library_id: int,
    req: LibraryUpdateRequest,
    repo: RepoDep,
    runtime: RuntimeDep,
) -> LibraryResponse:
    updates = cast("LibraryUpdates", req.model_dump(exclude_unset=True))
    if not updates:
        raise HTTPException(status_code=422, detail="没有需要修改的字段")

    if "scan_paths" in updates:
        updates["scan_paths"] = list(dict.fromkeys(p.strip() for p in updates["scan_paths"] if p and p.strip()))
        if not updates["scan_paths"]:
            raise HTTPException(status_code=422, detail="至少需要设置一个扫描路径")
        for path in updates["scan_paths"]:
            await validate_directory_path(path, runtime.safe_dirs)
    elif "path" in updates and updates["path"] is not None:
        await validate_directory_path(updates["path"], runtime.safe_dirs)
    if "organize_paths" in updates:
        updates["organize_paths"] = list(dict.fromkeys(p.strip() for p in updates["organize_paths"] if p and p.strip()))
        for path in updates["organize_paths"]:
            await validate_directory_path(path, runtime.safe_dirs)
    elif organize_path := updates.get("organize_path"):
        await validate_directory_path(organize_path, runtime.safe_dirs)

    try:
        lib = await repo.update_library(library_id, **updates)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if lib is None:
        raise HTTPException(status_code=404, detail="媒体库不存在")
    logger.info("library updated", library_id=library_id, fields=list(updates.keys()))

    watch_fields = {
        "automation",
        "ingest",
        "cloud_path",
        "path",
        "scan_paths",
        "recursive",
        "patterns",
        "trailer_pattern",
        "blacklist_patterns",
        "min_file_size",
    }
    if runtime.watcher_service and watch_fields & updates.keys():
        runtime.watcher_service.sync_library(lib)
    # 清单记录生成时的库根: 路径改掉后按新库根重解释相对路径会删错文件.
    if "path" in updates or "scan_paths" in updates:
        runtime.inventory_store.drop_library(library_id)

    return to_resp(LibraryResponse, lib)


@router.delete("/{library_id}", status_code=204)
async def delete_library(library_id: int, repo: RepoDep, runtime: RuntimeDep):
    """级联删除该库 MediaFile (library_id 非空 FK). 仅删除数据库索引, 不动磁盘文件."""
    existing = await repo.list_libraries()
    if not any(lib.id == library_id for lib in existing):
        raise HTTPException(status_code=404, detail="媒体库不存在")

    # 先停止监控, 避免删库后 watcher 仍为已删库 id 创建悬空 MediaFile
    if runtime.watcher_service:
        runtime.watcher_service.remove_library(library_id)

    deleted_media = await repo.delete_library(library_id)
    runtime.inventory_store.drop_library(library_id)
    logger.info("library deleted", library_id=library_id, deleted_media=deleted_media)
    return Response(status_code=204)
