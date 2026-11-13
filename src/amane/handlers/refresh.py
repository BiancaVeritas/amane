from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ..db import MediaFileStatus, TaskType
from ..library import MEDIA_EXTENSIONS, InventoryStore, LibraryHit, LibraryScan, OrphanScan, scan_inventory
from ..parsing import parse_file_info
from ..utils.path import nfc_path
from ..utils.threads import path_exists, path_is_dir
from ._common import register_media_file
from .models import OrganizePayload, RefreshPayload, RefreshResult, ScanMode, ScrapePayload
from .protocol import FollowupTask, TaskHandler, TaskResult

if TYPE_CHECKING:
    from ..db.models import MediaFile
    from ..db.repository import Repository

logger = structlog.get_logger()

_WALK_LOG_EVERY = 500


class RefreshHandler(TaskHandler[RefreshPayload, RefreshResult]):
    """扫描增删 MediaFile; 整库范围时顺带把清理清单写入清单存放."""

    def __init__(
        self,
        repo: Repository,
        media_extensions: Sequence[str] | None = None,
        inventory_store: InventoryStore | None = None,
    ):
        super().__init__(payload_t=RefreshPayload, result_t=RefreshResult)
        self._repo = repo
        self._media_extensions = frozenset(media_extensions) if media_extensions else MEDIA_EXTENSIONS
        self._inventory_store = inventory_store

    async def handle(self, payload: RefreshPayload) -> TaskResult[RefreshResult]:
        scan_dir = Path(payload.path)
        if not await path_is_dir(scan_dir):
            return TaskResult(success=False, error=f"不是目录: {payload.path}")

        library = await self._repo.get_library(payload.library_id)
        scan = LibraryScan(
            patterns=payload.patterns,
            trailer_pattern=library.trailer_pattern if library is not None else None,
            blacklist_patterns=library.blacklist_patterns if library is not None else None,
            min_file_size=library.min_file_size if library is not None else 0,
            media_extensions=self._media_extensions,
            subtitle_extensions=library.subtitle_extensions if library is not None else None,
        )

        added = removed = scrape = subtitles = 0
        adopted_count = 0

        existing = await self._repo.list_media_files(library_id=payload.library_id, limit=None)
        existing_by_path = {nfc_path(f.path): f for f in existing}

        if payload.scan:
            want_add = ScanMode.add in payload.scan
            want_remove = ScanMode.remove in payload.scan
            # add 与 remove 同时启用: 一次遍历收集 seen, 再做集合差.
            # 仅 remove: 不对整树 glob, 对库内记录逐条 exists (O(索引) 而非 O(磁盘树)).
            seen: set[str] | None = set() if want_add and want_remove else None

            # 扫描磁盘并注册未见过的媒体.
            if want_add:
                logger.info("scan walking started", path=payload.path)
                walked = 0
                library_root = Path(library.path) if library is not None else scan_dir
                # 与清理清单共用一趟遍历: 媒体命中用于增删, 无效文件与空目录顺手进清单.
                inventory = await scan_inventory(
                    scan_dir,
                    library_id=payload.library_id,
                    library_root=library_root,
                    recursive=payload.recursive if payload.recursive is not None else True,
                    patterns=payload.patterns or [],
                    scan=scan,
                    collect_media=True,
                    orphan_scan=(
                        OrphanScan.from_library(
                            library_root=library_root,
                            scope_dir=scan_dir,
                            subtitle_extensions=library.subtitle_extensions,
                            trailer_pattern=library.trailer_pattern,
                            patterns=payload.patterns or [],
                            media_extensions=self._media_extensions,
                        )
                        if library is not None
                        else None
                    ),
                )
                if library is not None and self._inventory_store is not None and inventory.scope_path is None:
                    # 子目录范围的巡检不代表整库, 因此不覆盖面板用的清单.
                    self._inventory_store.put(inventory)
                for hit in inventory.media_hits:
                    file_path = hit.path
                    path_key = nfc_path(str(file_path))
                    walked += 1
                    if seen is not None:
                        seen.add(path_key)
                    if walked % _WALK_LOG_EVERY == 0:
                        logger.info("scan walking", path=payload.path, seen=walked, added=added)
                    if path_key not in existing_by_path:
                        media = await register_media_file(self._repo, payload.library_id, file_path, library=library)
                        adopted = await self._adopt_existing_metadata(media)
                        existing_by_path[path_key] = media
                        added += 1
                        if adopted:
                            adopted_count += 1
                    else:
                        subtitles += await self._sync_external_subtitle(existing_by_path[path_key], hit)
                if added:
                    logger.info("new files discovered", path=payload.path, count=added)
                if adopted_count:
                    # 元数据还在就直接挂上: 删库重加 / 换目录之后不该把已有条目重刮一遍.
                    logger.info("existing metadata adopted", path=payload.path, count=adopted_count)

            # 删除索引中已不存在的记录.
            if want_remove:
                if seen is not None:
                    missing = [f for f in existing if nfc_path(f.path) not in seen]
                else:
                    missing = []
                    for f in existing:
                        still_there = await path_exists(Path(f.path))
                        if not still_there:
                            missing.append(f)
                if missing:
                    logger.info("remove invalid db entries", count=len(missing))
                    for media in missing:
                        assert media.id is not None
                        await self._repo.delete_media_file(media.id)
                    removed += len(missing)

        media_files = await self._repo.list_media_files(
            library_id=payload.library_id, status=payload.scrape, limit=None
        )

        # 扇出 SCRAPE; 只描述后继, 由完成事务创建.
        followups = []
        for f in media_files:
            parsed = parse_file_info(f.path)
            assert f.id is not None
            assert parsed.number is not None
            followups.append(
                FollowupTask(
                    key=f"scrape:{f.id}",
                    task_type=TaskType.SCRAPE,
                    payload=ScrapePayload(
                        media_file_id=f.id,
                        number=parsed.number,
                        content_type=parsed.content_type,
                        use_cache=payload.use_cache,
                    ).model_dump(mode="json"),
                )
            )
            scrape += 1

        # 已有元数据的文件只整理: 路径模板变了或补充过字段时, 扫描一次就该落到该去的位置,
        # 不必逐个点整理, 也不该因此重新刮一遍.
        organized = 0
        if payload.organize:
            scraped = await self._repo.list_media_files(
                library_id=payload.library_id, status={MediaFileStatus.SCRAPED}, limit=None
            )
            if scraped:
                organize_ids = [f.id for f in scraped if f.id is not None]
                followups.append(
                    FollowupTask(
                        key=f"organize:{payload.library_id}",
                        task_type=TaskType.ORGANIZE,
                        payload=OrganizePayload(
                            library_id=payload.library_id,
                            media_file_ids=organize_ids,
                        ).model_dump(mode="json"),
                    )
                )
                organized = len(organize_ids)

        logger.info(
            "scan completed",
            path=payload.path,
            added=added,
            removed=removed,
            scrape=scrape,
            organize=organized,
            subtitles=subtitles,
        )

        return TaskResult(True, result=RefreshResult(added=added, removed=removed, scrape=scrape), followups=followups)

    async def _adopt_existing_metadata(self, media: MediaFile) -> bool:
        """新登记的行若已有同番号元数据, 直接挂上并标成已刮削.

        删库重加、换监控目录之后, 番号条目本身还在库里; 让这些行重新进 PENDING 等于把
        整库再刮一遍 —— 既慢又白打站点. 返回是否真的挂上了.
        """
        if media.id is None or not media.number:
            return False
        meta = await self._repo.get_metadata_by_number(
            media.number, content_type=parse_file_info(media.path).content_type
        )
        if meta is None:
            return False
        await self._repo.update_media_file(media.id, status=MediaFileStatus.SCRAPED, metadata_id=meta.id)
        media.status = MediaFileStatus.SCRAPED
        media.metadata_id = meta.id
        return True

    async def _sync_external_subtitle(self, media: MediaFile, hit: LibraryHit) -> int:
        """按本次遍历所见回写已登记行的外挂字幕标记; 未变化返回 0.

        标记的其它写入点是视频登记与换 path, 二者都不覆盖存量行, 因此库扫描是存量行唯一的补齐途径.
        """
        if media.has_external_subtitle == hit.has_companion_subtitle:
            return 0
        assert media.id is not None
        await self._repo.update_media_file(media.id, has_external_subtitle=hit.has_companion_subtitle)
        return 1


def _scrape_targets(files: Sequence[MediaFile]) -> list[MediaFile]:
    """挑出该发 SCRAPE 的文件: 多盘片只留 CD1.

    同一番号的 CD1/CD2/CD3 共用一份元数据 (刮削结果按番号找), 每张各发一次只是把同一批
    站点重打若干遍. 整组没有 CD1 时不刮 — 缺首盘通常是抓取不完整, 让用户自己判断比按 CD2
    的数据落库更安全.

    无 CD 标记的文件原样保留.
    """
    singles: list[MediaFile] = []
    # 番号 → 该组里 CD 编号与文件的对应
    grouped: dict[str, dict[int, MediaFile]] = {}
    for f in files:
        parsed = parse_file_info(f.path)
        if parsed.number is None:
            continue
        if parsed.cd is None:
            singles.append(f)
            continue
        grouped.setdefault(parsed.number, {})[parsed.cd] = f

    picked = list(singles)
    for number, by_cd in grouped.items():
        first = by_cd.get(1)
        if first is not None:
            picked.append(first)
        else:
            logger.info("multi-disc first disc missing, skip scraping", number=number, cds=sorted(by_cd))
    return picked
