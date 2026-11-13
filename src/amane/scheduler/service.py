from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Coroutine, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from ..db.models import Library, TaskType
from ..enums import LibraryAutomation, LibraryIngest
from ..events import EventBus, EventType
from ..handlers._common import refresh_external_subtitle, register_media_file, scan_library
from ..handlers.models import ScrapePayload
from ..library import LibraryFileKind, LibraryScan
from ..parsing import parse_file_info
from ..utils.path import is_descendant, path_is_under
from ..utils.threads import existing_disk_path
from .clouddrive import CloudDriveChange, CloudDriveRoute, local_for, match_route
from .watcher import FileWatcher

if TYPE_CHECKING:
    from ..db.models import MediaFile
    from ..db.repository import Repository
    from ..parsing import FilePhase

logger = structlog.get_logger()

_CHECK_INTERVAL = 1.0


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(os.path.normpath(right))


def _file_in_scope(path: Path, route: CloudDriveRoute) -> bool:
    root = Path(route.local_path)
    if not is_descendant(path, root):
        return False
    if route.recursive:
        return True
    return _same_path(path.parent, root)


def _dir_in_scope(path: Path, route: CloudDriveRoute) -> bool:
    root = Path(route.local_path)
    if not is_descendant(path, root):
        return False
    if route.recursive:
        return True
    return _same_path(path, root)


class WatcherService:
    """新文件注册 MediaFile; automation=scrape 时解析番号并提交 SCRAPE.
    删除移除 MediaFile; 移动更新路径.

    ingest=native 的库挂 FileWatcher; ingest=clouddrive 的库只进入 webhook 路由表.
    """

    def __init__(
        self,
        repo: Repository,
        event_bus: EventBus,
        use_polling: bool = False,
        media_extensions: list[str] | None = None,
        debounce_seconds: float = 3.0,
        check_interval: float = _CHECK_INTERVAL,
        observer_timeout: float = 1.0,
    ):
        self._repo = repo
        self._event_bus = event_bus
        self._use_polling = use_polling
        self._media_extensions = media_extensions
        self._debounce_seconds = debounce_seconds
        self._check_interval = check_interval
        self._observer_timeout = observer_timeout
        self._watcher: FileWatcher | None = None
        self._debounce_task: asyncio.Task | None = None
        self._cloud_routes: dict[int, CloudDriveRoute] = {}
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def _new_watcher(self) -> FileWatcher:
        return FileWatcher(
            on_file_found=self._on_file_found_sync,
            on_file_deleted=self._on_file_deleted_sync,
            on_dir_deleted=self._on_dir_deleted_sync,
            on_file_moved=self._on_file_moved_sync,
            use_polling=self._use_polling,
            media_extensions=self._media_extensions,
            debounce_seconds=self._debounce_seconds,
            observer_timeout=self._observer_timeout,
        )

    def _scan_for(self, lib: Library) -> LibraryScan:
        return LibraryScan(
            patterns=lib.patterns,
            trailer_pattern=lib.trailer_pattern,
            blacklist_patterns=lib.blacklist_patterns,
            min_file_size=lib.min_file_size,
            media_extensions=frozenset(self._media_extensions) if self._media_extensions else None,
        )

    def _register_cloud(self, lib: Library) -> None:
        assert lib.id is not None
        if lib.cloud_path is None:
            logger.warning("clouddrive library missing cloud_path", library_id=lib.id)
            return
        self._cloud_routes[lib.id] = CloudDriveRoute(
            library_id=lib.id,
            cloud_path=lib.cloud_path,
            local_path=lib.path,
            recursive=lib.recursive,
            scan=self._scan_for(lib),
        )
        self._running = True
        logger.info(
            "clouddrive watch registered",
            library_id=lib.id,
            path=lib.path,
            cloud_path=lib.cloud_path,
            recursive=lib.recursive,
        )

    def _log_observer_start_error(self, exc: OSError) -> None:
        if "inotify" in str(exc).lower() or "watch" in str(exc).lower():
            logger.error(
                "Failed to start file watcher (inotify watch limit may be exceeded). "
                "Try increasing /proc/sys/fs/inotify/max_user_watches or set "
                "watcher.use_polling = true in config."
            )
        else:
            logger.error("Failed to start file watcher", exc_info=True)

    def _abandon_watcher(self) -> None:
        if self._watcher is not None:
            with contextlib.suppress(Exception):
                self._watcher.stop()
        self._watcher = None

    def _try_start_observer(self) -> bool:
        """启动已 schedule 的 FileWatcher. 失败则清掉实例, 返回 False."""
        if self._watcher is None:
            return False
        try:
            self._watcher.start()
        except OSError as exc:
            self._log_observer_start_error(exc)
            self._abandon_watcher()
            return False
        if self._debounce_task is None:
            self._debounce_task = asyncio.create_task(self._debounce_loop())
        return True

    async def start(self) -> None:
        if self._running:
            return

        libraries = await self._repo.list_libraries(watch_only=True)
        if not libraries:
            logger.info("no watch-enabled libraries configured, watcher not started")
            return

        native = [lib for lib in libraries if lib.ingest is LibraryIngest.NATIVE]
        cloud = [lib for lib in libraries if lib.ingest is LibraryIngest.CLOUDDRIVE]

        for lib in cloud:
            self._register_cloud(lib)

        if native:
            self._watcher = self._new_watcher()
            try:
                for lib in native:
                    assert lib.id is not None
                    logger.info("watching library", library_id=lib.id, path=lib.path, recursive=lib.recursive)
                    self._watcher.watch(
                        lib.path,
                        library_id=lib.id,
                        recursive=lib.recursive,
                        patterns=lib.patterns,
                        skip_patterns=[lib.trailer_pattern, *(lib.blacklist_patterns or [])],
                        min_file_size=lib.min_file_size,
                    )
                    # 独立下载目录: 同一 library_id 再挂一个 watch, 文件照常经
                    # on_file_found 入库; 之后按库的 move_mode 落到 lib.path.
                    if lib.watch_dir_enabled and lib.watch_path:
                        logger.info(
                            "watching download directory",
                            library_id=lib.id,
                            path=lib.watch_path,
                        )
                        self._watcher.watch(
                            lib.watch_path,
                            library_id=lib.id,
                            recursive=lib.recursive,
                            patterns=lib.patterns,
                            skip_patterns=[lib.trailer_pattern, *(lib.blacklist_patterns or [])],
                            min_file_size=lib.min_file_size,
                        )
            except OSError as exc:
                self._log_observer_start_error(exc)
                self._abandon_watcher()
            else:
                self._try_start_observer()

        self._running = True
        logger.info(
            "watcher service started",
            native_count=len(native),
            clouddrive_count=len(cloud),
            native_observer=self._watcher is not None,
        )

    async def stop(self) -> None:
        self._running = False
        self._cloud_routes.clear()
        if self._debounce_task:
            self._debounce_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._debounce_task
            self._debounce_task = None

        if self._watcher:
            self._watcher.stop()
            self._watcher = None

        logger.info("watcher service stopped")

    def sync_library(self, lib: Library) -> None:
        """按库当前 automation / ingest 热更新监控."""
        assert lib.id is not None
        self.remove_library(lib.id)
        if lib.automation is LibraryAutomation.NONE:
            return
        if lib.ingest is LibraryIngest.CLOUDDRIVE:
            self._register_cloud(lib)
            return
        self.add_library(
            lib.path,
            lib.id,
            recursive=lib.recursive,
            patterns=lib.patterns,
            skip_patterns=[lib.trailer_pattern, *(lib.blacklist_patterns or [])],
            min_file_size=lib.min_file_size,
            watch_path=lib.watch_path if lib.watch_dir_enabled else None,
        )

    def add_library(
        self,
        path: str,
        library_id: int,
        recursive: bool = True,
        patterns: list[str] | None = None,
        skip_patterns: Sequence[str | None] | None = None,
        min_file_size: int = 0,
        watch_path: str | None = None,
    ) -> None:
        """运行时热添加 native 监控库. clouddrive 库经 ``sync_library``.

        ``watch_path`` 是独立的下载目录, 与 ``path`` 一起挂: 只挂 ``path`` 会让保存设置之后的
        监控范围悄悄缩回目标目录.
        """
        self._cloud_routes.pop(library_id, None)
        if self._watcher is None:
            self._watcher = self._new_watcher()
            try:
                self._watcher.watch(
                    path,
                    library_id=library_id,
                    recursive=recursive,
                    patterns=patterns,
                    skip_patterns=skip_patterns,
                    min_file_size=min_file_size,
                )
                if watch_path:
                    self._watcher.watch(
                        watch_path,
                        library_id=library_id,
                        recursive=recursive,
                        patterns=patterns,
                        skip_patterns=skip_patterns,
                        min_file_size=min_file_size,
                    )
            except OSError as exc:
                self._log_observer_start_error(exc)
                self._abandon_watcher()
                self._running = True
                logger.error(
                    "native observer not started; library saved without filesystem watch", library_id=library_id
                )
                return
            if not self._try_start_observer():
                self._running = True
                logger.error(
                    "native observer not started; library saved without filesystem watch", library_id=library_id
                )
                return
            self._running = True
            logger.info("watcher service started for new library", library_id=library_id, path=path)
            return
        self._watcher.unwatch(library_id)
        self._watcher.watch(
            path,
            library_id=library_id,
            recursive=recursive,
            patterns=patterns,
            skip_patterns=skip_patterns,
            min_file_size=min_file_size,
        )
        # 与启动路径同一套: 保存设置时也要把下载目录重新挂上, 否则改一次配置就静默失效.
        if watch_path:
            self._watcher.watch(
                watch_path,
                library_id=library_id,
                recursive=recursive,
                patterns=patterns,
                skip_patterns=skip_patterns,
                min_file_size=min_file_size,
            )
        logger.info("library watch added", library_id=library_id, path=path, watch_path=watch_path, recursive=recursive)

    def remove_library(self, library_id: int) -> None:
        """运行时热移除监控库; watcher 未启动或该库未监控则为无操作."""
        self._cloud_routes.pop(library_id, None)
        if self._watcher is None:
            return
        self._watcher.unwatch(library_id)
        logger.info("library watch removed", library_id=library_id)

    def submit_clouddrive(self, changes: Sequence[CloudDriveChange]) -> None:
        """HTTP 线程已在事件循环上; 后台处理以免 FUSE 扫描堵住 webhook."""
        self._schedule_async(self.ingest_clouddrive(changes))

    async def ingest_clouddrive(self, changes: Sequence[CloudDriveChange]) -> None:
        routes = list(self._cloud_routes.values())
        if not routes:
            return
        dir_creates: list[tuple[int, str]] = []
        for change in changes:
            await self._apply_cloud_change(change, routes, dir_creates)
        await self._scan_collected_dirs(dir_creates)

    async def _scan_collected_dirs(self, dir_creates: list[tuple[int, str]]) -> None:
        if not dir_creates:
            return
        await asyncio.sleep(self._debounce_seconds)
        seen: set[tuple[int, str]] = set()
        for library_id, cloud_dir in dir_creates:
            route = self._cloud_routes.get(library_id)
            if route is None:
                continue
            try:
                local = local_for(route, cloud_dir)
            except ValueError:
                continue
            if not _dir_in_scope(local, route):
                continue
            key = (route.library_id, os.path.normcase(os.path.normpath(local)))
            if key in seen:
                continue
            seen.add(key)
            await self._scan_cloud_dir(route, local)

    async def _apply_cloud_change(
        self,
        change: CloudDriveChange,
        routes: list[CloudDriveRoute],
        dir_creates: list[tuple[int, str]],
    ) -> None:
        src_route = match_route(change.source_file, routes)
        dest_route = match_route(change.destination_file, routes) if change.destination_file else None

        if change.action == "create":
            if src_route is None:
                return
            local = local_for(src_route, change.source_file)
            if change.is_dir:
                if _dir_in_scope(local, src_route):
                    dir_creates.append((src_route.library_id, change.source_file))
                return
            if self._accept_cloud_file(src_route, local):
                await self._on_file_found(local, src_route.library_id)
            return

        if change.action == "delete":
            if src_route is None:
                return
            local = local_for(src_route, change.source_file)
            if change.is_dir:
                await self._delete_under(src_route.library_id, local)
            else:
                await self._on_file_deleted(local, verify=False)
            return

        if change.action == "rename":
            await self._apply_cloud_rename(change, src_route, dest_route, dir_creates)

    async def _apply_cloud_rename(
        self,
        change: CloudDriveChange,
        src_route: CloudDriveRoute | None,
        dest_route: CloudDriveRoute | None,
        dir_creates: list[tuple[int, str]],
    ) -> None:
        if change.is_dir:
            if src_route is not None and dest_route is None:
                await self._delete_under(src_route.library_id, local_for(src_route, change.source_file))
                return
            if src_route is None and dest_route is not None:
                local = local_for(dest_route, change.destination_file)
                if _dir_in_scope(local, dest_route):
                    dir_creates.append((dest_route.library_id, change.destination_file))
                return
            if src_route is not None and dest_route is not None:
                await self._rewrite_prefix(src_route, dest_route, change.source_file, change.destination_file)
            return

        if dest_route is not None:
            dest = local_for(dest_route, change.destination_file)
            if not self._accept_cloud_file(dest_route, dest):
                if src_route is not None:
                    await self._on_file_deleted(local_for(src_route, change.source_file), verify=False)
                return
            if src_route is None:
                await self._on_file_found(dest, dest_route.library_id)
                return
            if src_route.library_id != dest_route.library_id:
                await self._on_file_deleted(local_for(src_route, change.source_file), verify=False)
                await self._on_file_found(dest, dest_route.library_id)
                return
            await self._on_file_moved(local_for(src_route, change.source_file), dest, dest_route.library_id)
            return
        if src_route is not None:
            await self._on_file_deleted(local_for(src_route, change.source_file), verify=False)

    async def _scan_cloud_dir(self, route: CloudDriveRoute, local: Path) -> None:
        nested = not _same_path(local, Path(route.local_path))
        hits = await scan_library(local, recursive=route.recursive or nested, scan=route.scan)
        for hit in hits:
            if hit.kind is LibraryFileKind.MEDIA:
                await self._on_file_found(hit.path, route.library_id)

    async def _delete_under(self, library_id: int, root: Path) -> None:
        files = await self._repo.list_media_files(library_id=library_id, limit=None)
        for media in files:
            if media.id is None:
                continue
            if path_is_under(media.path, root):
                await self._on_file_deleted(Path(media.path), verify=False)

    def _accept_cloud_file(self, route: CloudDriveRoute, path: Path) -> bool:
        return _file_in_scope(path, route) and route.scan.classify(path) is LibraryFileKind.MEDIA

    async def _rewrite_prefix(
        self,
        src_route: CloudDriveRoute,
        dest_route: CloudDriveRoute,
        source_file: str,
        destination_file: str,
    ) -> None:
        src_root = local_for(src_route, source_file)
        dest_root = local_for(dest_route, destination_file)
        files = await self._repo.list_media_files(library_id=src_route.library_id, limit=None)
        for media in files:
            if media.id is None or not path_is_under(media.path, src_root):
                continue
            rel = Path(media.path).relative_to(src_root)
            dest = dest_root / rel
            if src_route.library_id == dest_route.library_id:
                if self._accept_cloud_file(dest_route, dest):
                    library = await self._repo.get_library(dest_route.library_id)
                    updated = await self._repo.update_media_file(media.id, path=str(dest))
                    if updated is not None and library is not None:
                        await refresh_external_subtitle(self._repo, updated, library)
                else:
                    await self._on_file_deleted(Path(media.path), verify=False)
                continue
            await self._on_file_deleted(Path(media.path), verify=False)
            if self._accept_cloud_file(dest_route, dest):
                await self._on_file_found(dest, dest_route.library_id)

    async def _seen_same_name(self, filename: str, library_id: int) -> bool:
        """同库内是否已有一份同名文件且已刮削.

        复制 / 硬链接整理后源文件不会消失, 每轮都会被当成新文件重新刮削; 以文件名当键挡住
        这种重复. 删掉记录后名字不再命中, 下次仍会重新处理.
        """
        return await self._repo.has_scraped_with_filename(library_id, filename)

    def _on_file_found_sync(self, path: Path, library_id: int) -> None:
        self._schedule_async(self._on_file_found(path, library_id))

    def _on_file_deleted_sync(self, path: Path, _library_id: int) -> None:
        self._schedule_async(self._on_file_deleted(path))

    def _on_dir_deleted_sync(self, path: Path, library_id: int) -> None:
        self._schedule_async(self._delete_under(library_id, path))

    def _on_file_moved_sync(self, src: Path, dest: Path, library_id: int) -> None:
        self._schedule_async(self._on_file_moved(src, dest, library_id))

    def _schedule_async(self, coro: Coroutine[Any, Any, Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(asyncio.ensure_future, coro)
        except RuntimeError:
            logger.warning("no event loop available")

    async def _on_file_found(self, path: Path, library_id: int) -> None:
        path_str = str(path)

        existing = await self._repo.get_media_file_by_path(path_str)
        if existing is not None:
            logger.debug("file already tracked", path=path_str)
            return

        # 按源文件名查重: 复制 / 硬链接整理后源文件仍留在原处, 换成新路径会被再次当成新文件.
        # 以文件名为键而不是路径, 换目录同名也算同一份, 免得每轮扫描都重刮一遍.
        if await self._seen_same_name(path.name, library_id):
            logger.info("file already processed under same name", path=path_str)
            return

        library = await self._repo.get_library(library_id)
        media = await register_media_file(self._repo, library_id, path, library=library)
        assert media.id is not None
        logger.info("file discovered", path=path_str, media_file_id=media.id, library_id=library_id)

        try:
            parsed = parse_file_info(path_str)
        except Exception:
            logger.debug("cannot parse number", path=path_str)
            parsed = None

        if parsed is not None and parsed.number is not None:
            await self._repo.update_media_file(media.id, number=parsed.number)
            # 多盘片只有首盘值得刮: 同番号的 CD2/CD3 拿到的是同一份元数据, 各自再发一次
            # 只是把同一批站点重打若干遍. 缺 CD1 时先不刮, 等补齐或用整库扫描统一判断.
            is_later_disc = parsed.cd is not None and parsed.cd != 1
            if is_later_disc:
                logger.info("later disc discovered, scrape deferred to its CD1", number=parsed.number, cd=parsed.cd)
            if library is not None and library.automation == LibraryAutomation.SCRAPE and not is_later_disc:
                task = await self._repo.create_task(
                    task_type=TaskType.SCRAPE,
                    payload=ScrapePayload(
                        media_file_id=media.id, number=parsed.number, content_type=parsed.content_type
                    ),
                )
                logger.info("scrape task submitted", task_id=task.id, number=parsed.number, path=path_str)

        await self._event_bus.emit(EventType.FILE_DISCOVERED, {"path": path_str, "media_file_id": media.id})

    async def _on_file_deleted(self, path: Path, *, verify: bool = True) -> None:
        """``verify`` 为真时先确认文件真的不在了.

        观察器重建 (改库配置会重挂) 时 watchdog 可能补发已消失路径的 deleted 事件, 而文件
        其实还在; 照删会让一条正常记录静默消失, 随后按它发的整理任务只能 skipped=1.
        云盘来的变更通知是权威的「这个文件没了」, 且虚拟路径不落到本地盘, 因此传 False.
        """
        path_str = str(path)
        media = await self._repo.get_media_file_by_path(path_str)
        if media is None:
            return

        if verify and await existing_disk_path(Path(media.path), follow_symlinks=False) is not None:
            logger.debug("file still on disk, ignore delete event", path=path_str)
            return

        assert media.id is not None
        await self._repo.delete_media_file(media.id)
        logger.info("file removed from db", path=path_str, media_file_id=media.id)

        await self._event_bus.emit(EventType.FILE_REMOVED, {"path": path_str, "media_file_id": media.id})

    async def _queue_scrape_for_rename(self, media: MediaFile, parsed: FilePhase) -> None:
        """改名后番号变了: 按新番号重发一次刮削.

        旧番号的元数据已经毫无意义, 不重发就会一直挂在那条错记录上.
        """
        assert media.id is not None
        task = await self._repo.create_task(
            task_type=TaskType.SCRAPE,
            payload=ScrapePayload(media_file_id=media.id, number=parsed.number, content_type=parsed.content_type),
        )
        logger.info("scrape task submitted after rename", task_id=task.id, number=parsed.number, path=media.path)

    async def _on_file_moved(self, src: Path, dest: Path, library_id: int) -> None:
        src_str = str(src)
        dest_str = str(dest)

        media = await self._repo.get_media_file_by_path(src_str)
        if media is None:
            await self._on_file_found(dest, library_id)
            return

        assert media.id is not None
        library = await self._repo.get_library(library_id)
        # 改名常是为了改番号: 只换 path 会把旧番号留在行上, 后续按它去找元数据, 永远对不上.
        parsed = parse_file_info(dest_str)
        changed_number = parsed.number is not None and parsed.number != media.number
        updated = await self._repo.update_media_file(
            media.id,
            path=dest_str,
            **({"number": parsed.number} if changed_number else {}),
        )
        if updated is not None and library is not None:
            await refresh_external_subtitle(self._repo, updated, library)
        logger.info(
            "file path updated",
            src=src_str,
            dest=dest_str,
            media_file_id=media.id,
            number=parsed.number,
            number_changed=changed_number,
        )
        # 与首次发现同一口径: 多盘片只有首盘值得刮.
        later_disc = parsed.cd is not None and parsed.cd != 1
        if changed_number and not later_disc and library is not None and library.automation == LibraryAutomation.SCRAPE:
            await self._queue_scrape_for_rename(updated if updated is not None else media, parsed)

    async def _debounce_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self._check_interval)
            if self._watcher:
                self._watcher.check_debounced()
