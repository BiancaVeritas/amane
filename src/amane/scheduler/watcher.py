import contextlib
import itertools
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from watchdog.events import FileSystemEventHandler
from watchdog.observers.polling import PollingObserver

from ..library import MEDIA_EXTENSIONS, LibraryFileKind, LibraryScan
from ..library.rules import is_in_trash
from ..utils.path import path_is_under, path_key

if sys.platform == "win32":
    from .watcher_win import WindowsObserver as _NativeObserver
else:
    from watchdog.observers import Observer as _NativeObserver

if TYPE_CHECKING:
    from watchdog.observers.api import BaseObserver, ObservedWatch

_PendingValue = tuple[float, int] | float
_PendingMove = tuple[str, float, int] | tuple[str, float]
_T = TypeVar("_T")
_R = TypeVar("_R")


def _pending_timestamp(value: object) -> float:
    """读取普通/移动事件的时间戳, 兼容测试与旧缓存格式."""
    if isinstance(value, tuple):
        if len(value) == 3:
            timestamp = value[1]
        elif len(value) == 2:
            timestamp = value[0] if isinstance(value[0], (int, float)) else value[1]
        else:
            raise TypeError(f"unsupported pending event value: {value!r}")
    else:
        timestamp = value
    if not isinstance(timestamp, (int, float)):
        raise TypeError(f"pending event timestamp must be numeric, got {timestamp!r}")
    return float(timestamp)


_DEFAULT_DEBOUNCE_SECONDS = 3.0
_DEFAULT_OBSERVER_TIMEOUT = 1.0  # 与 watchdog Observer/PollingObserver 构造默认一致

# 测试中引用此名称, 保留常量别名.
DEBOUNCE_SECONDS = _DEFAULT_DEBOUNCE_SECONDS


class _Handler(FileSystemEventHandler):
    """每个 handler 对应一个 library_id, 事件自带归属, 无需按路径前缀反推."""

    def __init__(
        self,
        library_id: int,
        root: str,
        scan: LibraryScan | None = None,
        debounce_seconds: float = _DEFAULT_DEBOUNCE_SECONDS,
    ):
        super().__init__()
        self.library_id = library_id
        self._root_key = path_key(root)
        self._scan = scan if scan is not None else LibraryScan()
        self._debounce_seconds = debounce_seconds
        self._pending: dict[str, _PendingValue] = {}
        self._pending_deletes: dict[str, _PendingValue] = {}
        self._pending_dir_deletes: dict[str, _PendingValue] = {}
        self._pending_moves: dict[str, _PendingMove] = {}  # dest -> (src, timestamp, sequence)
        self._event_sequence = itertools.count()
        self._event_lock = threading.Lock()
        self._accepting_events = True

    def on_created(self, event):
        with self._event_lock:
            if self._accepting_events and not event.is_directory:
                self._handle(str(event.src_path))

    def on_deleted(self, event):
        with self._event_lock:
            if not self._accepting_events:
                return
            self._on_deleted_locked(event)

    def _on_deleted_locked(self, event):
        # 库根自身的删除事件不代表库内文件被移除: watchdog 在读取目录失败或停止过程中会合成一条
        # 路径为监控根的删除事件, 按前缀会删除整库索引; 整库清理走 REFRESH 的 remove 模式.
        if path_key(str(event.src_path)) == self._root_key:
            return
        # 前缀含路径自身: 文件删除只命中这一条; 目录删除命中子树.
        # Windows 删除通知不区分文件与目录, 一律按前缀处理.
        self._record_prefix_delete(str(event.src_path))

    def on_moved(self, event):
        with self._event_lock:
            if self._accepting_events:
                self._on_moved_locked(event)

    def _on_moved_locked(self, event):
        if not event.is_directory:
            src_str = str(event.src_path)
            dest_str = str(event.dest_path)
            dest = Path(dest_str)

            # 源路径上的创建/删除待处理一律丢弃
            self._pending.pop(src_str, None)
            self._pending_deletes.pop(src_str, None)

            if self._matches(dest):
                # 目标匹配: 记录为移动
                self._pending_moves[dest_str] = (src_str, time.time(), next(self._event_sequence))
            else:
                # 目标不匹配: 源若为媒体则记录为删除
                src = Path(src_str)
                if self._matches(src):
                    self._pending_deletes[src_str] = (time.time(), next(self._event_sequence))

    def _record_prefix_delete(self, dir_str: str) -> None:
        """按路径前缀清未提交事件; 防抖后由服务端按索引前缀删除."""
        if is_in_trash(Path(dir_str)):
            return
        # 已有更外层路径待删除时不必再记; 同一路径则刷新时间戳.
        if any(
            path_is_under(dir_str, existing) and not path_is_under(existing, dir_str)
            for existing in self._pending_dir_deletes
        ):
            self._drop_pending_under(dir_str)
            return
        self._drop_pending_under(dir_str)
        self._pending_dir_deletes[dir_str] = (time.time(), next(self._event_sequence))

    def _drop_pending_under(self, root: str) -> None:
        self._pending = {path: ts for path, ts in self._pending.items() if not path_is_under(path, root)}
        self._pending_deletes = {
            path: ts for path, ts in self._pending_deletes.items() if not path_is_under(path, root)
        }
        self._pending_moves = {
            dest: src_ts
            for dest, src_ts in self._pending_moves.items()
            if not path_is_under(dest, root) and not path_is_under(src_ts[0], root)
        }
        self._pending_dir_deletes = {
            path: ts for path, ts in self._pending_dir_deletes.items() if not path_is_under(path, root)
        }

    def _handle(self, path_str: str) -> None:
        path = Path(path_str)
        if self._matches(path):
            self._pending[path_str] = (time.time(), next(self._event_sequence))

    def _matches(self, path: Path) -> bool:
        return self._scan.classify(path) is LibraryFileKind.MEDIA

    def _take_ready(self, pending: dict[str, _T], convert: Callable[[str, _T], _R]) -> list[_R]:
        """在事件锁内提取已过防抖期的缓存，避免与 observer 回调/热更新交接竞争。"""
        now = time.time()
        ready: list[_R] = []
        remaining: dict[str, _T] = {}
        for key, value in pending.items():
            # 普通事件为 (timestamp, sequence)，移动事件为 (src, timestamp, sequence)。
            # 兼容测试/旧调用直接写入 float，以及旧式移动值 (src, timestamp)。
            if now - _pending_timestamp(value) >= self._debounce_seconds:
                ready.append(convert(key, value))
            else:
                remaining[key] = value
        pending.clear()
        pending.update(remaining)
        return ready

    def get_ready_files(self) -> list[Path]:
        with self._event_lock:
            return self._take_ready(self._pending, lambda path, _: Path(path))

    def get_ready_deletes(self) -> list[Path]:
        with self._event_lock:
            return self._take_ready(self._pending_deletes, lambda path, _: Path(path))

    def get_ready_dir_deletes(self) -> list[Path]:
        with self._event_lock:
            return self._take_ready(self._pending_dir_deletes, lambda path, _: Path(path))

    def get_ready_moves(self) -> list[tuple[Path, Path]]:
        with self._event_lock:
            return self._take_ready(
                self._pending_moves,
                lambda dest, value: (Path(value[0]), Path(dest)),
            )

    def stop_and_take_pending(self) -> list[tuple[float, int, str, Path | tuple[Path, Path]]]:
        """原子关闭旧 handler，按时间戳和单调序号取出事件。"""
        with self._event_lock:
            self._accepting_events = False
            events: list[tuple[float, int, str, Path | tuple[Path, Path]]] = []

            def stamp(value: _PendingValue) -> tuple[float, int]:
                # 兼容测试或旧调用直接向缓存写入 float 的情况。
                return value if isinstance(value, tuple) else (value, -1)

            for path, value in self._pending.items():
                timestamp, sequence = stamp(value)
                events.append((timestamp, sequence, "created", Path(path)))
            for path, value in self._pending_deletes.items():
                timestamp, sequence = stamp(value)
                events.append((timestamp, sequence, "deleted", Path(path)))
            for path, value in self._pending_dir_deletes.items():
                timestamp, sequence = stamp(value)
                events.append((timestamp, sequence, "dir_deleted", Path(path)))
            for dest, move in self._pending_moves.items():
                src, timestamp = move[0], move[1]
                last_value = move[-1]
                sequence = last_value if isinstance(last_value, int) else -1
                events.append((timestamp, sequence, "moved", (Path(src), Path(dest))))
            self._pending.clear()
            self._pending_deletes.clear()
            self._pending_dir_deletes.clear()
            self._pending_moves.clear()
            events.sort(key=lambda item: (item[0], item[1]))
            return events


class FileWatcher:
    """媒体目录监控, 带防抖. 回调额外接收 library_id.

    use_polling=False 使用原生 OS 事件: Linux inotify 不支持 NFS/CIFS 等网络挂载.
    use_polling=True 适用于 NAS/NFS、Docker Desktop for macOS (VirtioFS inotify
    不可靠)、WSL2, 以及 inotify watch 数量超限.
    """

    def __init__(
        self,
        on_file_found: Callable[[Path, int], None],
        on_file_deleted: Callable[[Path, int], None] | None = None,
        on_dir_deleted: Callable[[Path, int], None] | None = None,
        on_file_moved: Callable[[Path, Path, int], None] | None = None,
        use_polling: bool = False,
        media_extensions: list[str] | None = None,
        debounce_seconds: float = _DEFAULT_DEBOUNCE_SECONDS,
        observer_timeout: float = _DEFAULT_OBSERVER_TIMEOUT,
    ):
        self._on_file_found = on_file_found
        self._on_file_deleted = on_file_deleted
        self._on_dir_deleted = on_dir_deleted
        self._on_file_moved = on_file_moved
        self._use_polling = use_polling
        self._media_extensions = frozenset(media_extensions) if media_extensions else MEDIA_EXTENSIONS
        self._debounce_seconds = debounce_seconds
        self._observer_timeout = observer_timeout
        self._observer: BaseObserver | None = None
        self._handlers: list[_Handler] = []
        self._watching: list[tuple[str, bool, list[str] | None, int]] = []
        self._watches: dict[int, list[ObservedWatch]] = {}
        # Watchdog 对相同 (path, recursive) 返回共享的 ObservedWatch。
        # 多个 library 监听同一路径时，只有最后一个引用释放后才能 unschedule。
        self._watch_refcounts: dict[ObservedWatch, int] = {}

    @property
    def is_running(self) -> bool:
        return self._observer is not None and self._observer.is_alive()

    def _retain_watch(self, watch) -> None:
        self._watch_refcounts[watch] = self._watch_refcounts.get(watch, 0) + 1

    def _release_watch(self, watch) -> None:
        """释放一个 library 对共享 watch 的引用，最后一个引用才真正撤销。"""
        count = self._watch_refcounts.get(watch, 0)
        if count > 1:
            self._watch_refcounts[watch] = count - 1
            return
        self._watch_refcounts.pop(watch, None)
        if self._observer is not None:
            # 防御 observer 已停止/重置或外部清理造成的过期引用。
            emitters = getattr(self._observer, "_emitter_for_watch", None)
            if emitters is None or watch in emitters:
                self._observer.unschedule(watch)

    def watch(
        self,
        path: str,
        library_id: int,
        recursive: bool = True,
        patterns: list[str] | None = None,
        skip_patterns: Sequence[str | None] | None = None,
        min_file_size: int = 0,
    ) -> None:
        """登记监控目录.

        skip_patterns 命中则不登记; `.amane_trash` 内路径恒忽略.
        min_file_size 只对 media_extensions 判定, `.strm` 指针不参与.
        """
        handler = _Handler(
            library_id,
            path,
            scan=LibraryScan(
                patterns=patterns,
                blacklist_patterns=[p for p in skip_patterns if p] if skip_patterns else None,
                min_file_size=min_file_size,
                media_extensions=self._media_extensions,
            ),
            debounce_seconds=self._debounce_seconds,
        )
        self._handlers.append(handler)
        self._watching.append((path, recursive, patterns, library_id))
        if self._observer is None:
            observer_class = PollingObserver if self._use_polling else _NativeObserver
            self._observer = observer_class(timeout=self._observer_timeout)
        watch = self._observer.schedule(handler, path, recursive=recursive)
        self._retain_watch(watch)
        self._watches.setdefault(library_id, []).append(watch)

    def unwatch(self, library_id: int) -> None:
        """取消该 library_id 的监控; 未在监控中则为无操作."""
        watches = self._watches.pop(library_id, [])
        for watch in watches:
            self._release_watch(watch)
        self._handlers = [h for h in self._handlers if h.library_id != library_id]
        self._watching = [item for item in self._watching if item[3] != library_id]

    def replace_library(
        self,
        library_id: int,
        paths: Sequence[tuple[str, bool, list[str] | None, Sequence[str | None] | None, int]],
    ) -> None:
        """先注册该库的新目录，全部成功后再撤销旧目录；失败时保留旧监听。"""
        if self._observer is None:
            observer_class = PollingObserver if self._use_polling else _NativeObserver
            self._observer = observer_class(timeout=self._observer_timeout)

        new_handlers: list[_Handler] = []
        new_watches: list[ObservedWatch] = []
        try:
            for path, recursive, patterns, skip_patterns, min_file_size in paths:
                handler = _Handler(
                    library_id,
                    path,
                    scan=LibraryScan(
                        patterns=patterns,
                        blacklist_patterns=[p for p in skip_patterns if p] if skip_patterns else None,
                        min_file_size=min_file_size,
                        media_extensions=self._media_extensions,
                    ),
                    debounce_seconds=self._debounce_seconds,
                )
                watch = self._observer.schedule(handler, path, recursive=recursive)
                self._retain_watch(watch)
                new_handlers.append(handler)
                new_watches.append(watch)
        except Exception:
            for watch in new_watches:
                with contextlib.suppress(Exception):
                    self._release_watch(watch)
            raise

        # 新监听全部注册成功后，先撤销旧 watch，再原子关闭旧 handler 并提取缓存。
        # 已进入回调的事件会先完成；迟到回调因 accepting=False 被拒绝。
        old_watches = self._watches.get(library_id, [])
        for watch in old_watches:
            self._release_watch(watch)
        old_handlers = [handler for handler in self._handlers if handler.library_id == library_id]
        for handler in old_handlers:
            for _, _, event_type, payload in handler.stop_and_take_pending():
                if event_type == "dir_deleted" and isinstance(payload, Path) and self._on_dir_deleted:
                    self._on_dir_deleted(payload, library_id)
                elif event_type == "deleted" and isinstance(payload, Path) and self._on_file_deleted:
                    self._on_file_deleted(payload, library_id)
                elif event_type == "moved" and isinstance(payload, tuple) and self._on_file_moved:
                    src, dest = payload
                    self._on_file_moved(src, dest, library_id)
                elif event_type == "created" and isinstance(payload, Path):
                    self._on_file_found(payload, library_id)

        self._handlers = [handler for handler in self._handlers if handler.library_id != library_id]
        self._handlers.extend(new_handlers)
        self._watches[library_id] = new_watches
        self._watching = [item for item in self._watching if item[3] != library_id]
        self._watching.extend((path, recursive, patterns, library_id) for path, recursive, patterns, _, _ in paths)

    def start(self) -> None:
        if self._observer:
            self._observer.start()

    def stop(self) -> None:
        if self._observer:
            observer = self._observer
            observer.stop()
            # 不带超时等待线程退出：调用方随后会排空事件循环中已投递的回调，
            # 若此处提前返回，仍存活的 observer 可能在服务重启后投递旧事件。
            if observer.is_alive():
                observer.join()
            self._observer = None
            self._watch_refcounts.clear()
            self._watches.clear()

    def check_debounced(self) -> list[Path]:
        """刷新已过防抖窗口的事件并调用对应回调."""
        ready_files = []
        for handler in self._handlers:
            # 目录前缀删除须先于创建: 同窗口内重建的文件应在清索引之后重新登记.
            if self._on_dir_deleted:
                for path in handler.get_ready_dir_deletes():
                    self._on_dir_deleted(path, handler.library_id)
            if self._on_file_deleted:
                for path in handler.get_ready_deletes():
                    self._on_file_deleted(path, handler.library_id)
            if self._on_file_moved:
                for src, dest in handler.get_ready_moves():
                    self._on_file_moved(src, dest, handler.library_id)
            for path in handler.get_ready_files():
                self._on_file_found(path, handler.library_id)
                ready_files.append(path)
        return ready_files
