from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ..db import Library
from ..db.models import MediaIdentityType
from ..library import LibraryHit, LibraryScan, has_companion_subtitle
from ..parsing import ContentType, parse_file_info
from ..utils.litepan_strm import read_litepan_115_file_id
from ..utils.oshash import compute_oshash
from ..utils.threads import existing_disk_path, in_thread

logger = structlog.get_logger(__name__)


if TYPE_CHECKING:
    from ..db.models import MediaFile
    from ..db.repository import Repository


class LibraryTaskLocks:
    """同库 REFRESH、ORGANIZE 与 DELETE 共用, 执行期串行."""

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def get(self, library_id: int) -> asyncio.Lock:
        async with self._guard:
            lock = self._locks.get(library_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[library_id] = lock
            return lock


def _maybe_file(f: Path) -> bool:
    """常规文件或符号链接 (含断链) 视为文件入口; 目录不产出."""
    return f.is_file() or f.is_symlink()


@in_thread
def scan_library(scan_dir: Path, *, recursive: bool, scan: LibraryScan) -> list[LibraryHit]:
    """目录本身与回收目录不产出."""
    glob_pattern = "**/*" if recursive else "*"
    hits: list[LibraryHit] = []
    # 跳过目录与回收目录; 其余按规则分类.
    for file_path in scan_dir.glob(glob_pattern):
        if not _maybe_file(file_path):
            continue
        kind = scan.classify(file_path)
        if kind is None:
            continue
        hits.append(LibraryHit(file_path, kind))
    return hits


async def register_media_file(
    repo: Repository, library_id: int, path: Path, *, library: Library | None = None
) -> MediaFile | None:
    """按全局路径唯一性登记; 已被其它媒体库登记时不重复插入、不自动转移."""
    media, created = await repo.create_media_file_if_absent(library_id=library_id, path=str(path))
    if media.library_id != library_id:
        logger.debug(
            "file path already indexed in another library",
            path=str(path),
            existing_library_id=media.library_id,
            requested_library_id=library_id,
        )
        return None
    if created and library is not None:
        await refresh_external_subtitle(repo, media, library)
    return media


async def refresh_external_subtitle(repo: Repository, media: MediaFile, library: Library) -> None:
    """按视频当前所在目录检查一次外挂字幕, 写回 ``MediaFile.has_external_subtitle``.

    库 ``subtitle_extensions`` 为空表示关闭发现, 此时写入假值, 早先命中的角标随之熄灭.
    """
    extensions = library.subtitle_extensions or []
    found = await has_companion_subtitle(Path(media.path), extensions)
    if media.id is None:
        return
    media.has_external_subtitle = found
    await repo.update_media_file(media.id, has_external_subtitle=found)


async def refresh_media_association(repo: Repository, media: MediaFile) -> bool:
    """只使用已登记的内部身份恢复关联；绝不计算/写入新 OSHASH 或覆盖番号/身份字段。"""
    if media.id is None or media.metadata_id is not None:
        return False
    parsed = parse_file_info(media.path)
    metadata = None
    if parsed.number and parsed.content_type != ContentType.WESTERN:
        metadata = await repo.get_metadata_by_number(parsed.number, content_type=parsed.content_type)
    elif parsed.content_type == ContentType.WESTERN:
        file_115_id = await read_litepan_115_file_id(Path(media.path))
        if file_115_id:
            metadata = await repo.get_metadata_by_identity(MediaIdentityType.FILE_115_ID, file_115_id)
        if metadata is None and media.oshash:
            metadata = await repo.get_metadata_by_identity(MediaIdentityType.LOCAL_OSHASH, media.oshash)
            if metadata is None:
                metadata = await repo.get_metadata_by_oshash(media.oshash)
    if metadata is None or metadata.id is None:
        return False
    # finalize_media_file 只提交 status / metadata_id / MetadataLibrary，不写番号、115 ID 或 OSHASH。
    await repo.finalize_media_file(media.id, metadata.id)
    return True


async def ensure_oshash(repo: Repository, media: MediaFile) -> str | None:
    """已有指纹直接返回; STRM 是播放指针, 不计算 OSHASH."""
    if Path(media.path).suffix.casefold() == ".strm":
        return None
    if media.oshash is not None:
        return media.oshash
    disk = await existing_disk_path(Path(media.path))
    if disk is None:
        return None
    media_hash = await compute_oshash(disk)
    if media_hash is None or media.id is None:
        return None
    updated = await repo.update_media_file(media.id, oshash=media_hash)
    return updated.oshash if updated is not None else media_hash


async def finalize_media_file(repo: Repository, media_file_id: int | None, metadata_id: int | None) -> None:
    """media_file_id 为 None 时静默跳过."""
    if media_file_id is None:
        return
    await repo.finalize_media_file(media_file_id, metadata_id)
