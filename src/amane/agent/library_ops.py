from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from pydantic_ai import RunContext
from pydantic_ai.capabilities import Capability

from ..api.support.path_validation import check_directory_path
from ..db.models import Library, TaskType
from ..db.repo_types import LibraryUpdates
from ..enums import LibraryAutomation, LibraryIngest
from ..handlers.models import RefreshPayload, ScanMode
from .tools import TOOL_OK, AgentDeps, require_approval, unknown_field_error

_LIBRARY_UPDATE_KEYS = frozenset(
    {
        "name",
        "path",
        "scan_paths",
        "organize_paths",
        "automation",
        "auto_organize",
        "ingest",
        "cloud_path",
        "recursive",
        "patterns",
        "move_mode",
        "video_template",
        "link_template",
        "link_mode",
        "strm_content_template",
        "thumb_template",
        "poster_template",
        "fanart_template",
        "extrafanart_template",
        "nfo_template",
        "trailer_template",
        "subtitle_template",
        "subtitle_extensions",
        "write_nfo",
        "copy_resources",
        "trailer_pattern",
        "blacklist_patterns",
        "min_file_size",
    }
)


def _sync_library(deps: AgentDeps, lib: Library) -> None:
    watcher = deps.bridge.watcher
    if watcher is not None:
        watcher.sync_library(lib)


def build_library_ops_capability() -> Capability[AgentDeps]:
    """删除须批准."""
    cap: Capability[AgentDeps] = Capability(
        id="library-ops",
        instructions=(
            "A library path must exist and lie under safe_dirs; create_library rejects anything else. "
            "After create, prefer enqueue_library_refresh when the user wants an initial scan."
        ),
    )

    @cap.tool
    async def create_library(
        ctx: RunContext[AgentDeps],
        path: str,
        scan_paths: list[str] | None = None,
        organize_paths: list[str] | None = None,
        name: str | None = None,
        automation: LibraryAutomation = LibraryAutomation.SCRAPE,
        auto_organize: bool = False,
        recursive: bool = True,
        patterns: list[str] | None = None,
        scan: bool = True,
    ) -> dict[str, Any]:
        """Create a media library; optional initial REFRESH(scan=add)."""
        patterns = list(patterns or [])
        scan_paths = list(dict.fromkeys(p.strip() for p in (scan_paths or [path]) if p and p.strip()))
        organize_paths = list(dict.fromkeys(p.strip() for p in (organize_paths or []) if p and p.strip()))
        if not scan_paths:
            return {"error": "至少需要设置一个扫描路径"}
        try:
            for scan_path in scan_paths:
                await check_directory_path(scan_path, ctx.deps.bridge.safe_dirs)
            for organize_path in organize_paths:
                await check_directory_path(organize_path, ctx.deps.bridge.safe_dirs)
        except ValueError as exc:
            return {"error": str(exc)}
        display = name or Path(scan_paths[0]).name
        try:
            lib = await ctx.deps.repo.create_library(
                name=display,
                path=scan_paths[0],
                scan_paths=scan_paths,
                organize_paths=organize_paths,
                automation=automation,
                auto_organize=auto_organize,
                recursive=recursive,
                patterns=patterns,
            )
        except ValueError as exc:
            return {"error": str(exc)}
        assert lib.id is not None
        _sync_library(ctx.deps, lib)
        out: dict[str, Any] = {"library_id": lib.id}
        if scan:
            scan_paths = lib.scan_paths or [lib.path]
            task = await ctx.deps.repo.create_task(
                TaskType.REFRESH,
                RefreshPayload(
                    library_id=lib.id,
                    recursive=lib.recursive,
                    patterns=list(lib.patterns or []),
                    path=scan_paths[0],
                    paths=scan_paths,
                    scan={ScanMode.add},
                    scrape=set(),
                ),
            )
            assert task.id is not None
            out["refresh_task_id"] = task.id
            out["refresh_task_ids"] = [task.id]
        return out

    @cap.tool
    async def update_library(
        ctx: RunContext[AgentDeps], library_id: int, patch: dict[str, Any]
    ) -> str | dict[str, Any]:
        """Patch library config fields."""
        if not patch:
            return {"error": "patch 为空"}
        unknown = sorted(set(patch) - _LIBRARY_UPDATE_KEYS)
        if unknown:
            return unknown_field_error(unknown, _LIBRARY_UPDATE_KEYS)
        if "automation" in patch:
            try:
                patch["automation"] = LibraryAutomation(patch["automation"])
            except ValueError:
                return {"error": f"无效的 automation: {patch['automation']}"}
        if "ingest" in patch:
            try:
                patch["ingest"] = LibraryIngest(patch["ingest"])
            except ValueError:
                return {"error": f"无效的 ingest: {patch['ingest']}"}
        try:
            if "path" in patch and patch["path"] is not None:
                await check_directory_path(str(patch["path"]), ctx.deps.bridge.safe_dirs)
            for field in ("scan_paths", "organize_paths"):
                for candidate in patch.get(field, []) or []:
                    await check_directory_path(str(candidate), ctx.deps.bridge.safe_dirs)
        except ValueError as exc:
            return {"error": str(exc)}
        try:
            lib = await ctx.deps.repo.update_library(library_id, **cast(LibraryUpdates, patch))
        except ValueError as exc:
            return {"error": str(exc)}
        if lib is None:
            return {"error": f"library {library_id} 不存在"}
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
        if watch_fields & set(patch):
            _sync_library(ctx.deps, lib)
        return TOOL_OK

    @cap.tool
    async def delete_library(ctx: RunContext[AgentDeps], library_id: int) -> str | dict[str, Any]:
        """Delete a library and its MediaFile index rows.

        Only the index is removed: files on disk stay untouched, and metadata rows survive.
        Not undoable.
        """
        detail = f"删除媒体库 id={library_id} (仅索引, 不动磁盘文件)"
        require_approval(
            ctx,
            sql=detail,
            tool="delete_library",
            extra={"library_id": library_id},
        )
        existing = await ctx.deps.repo.list_libraries()
        if not any(lib.id == library_id for lib in existing):
            return {"error": f"library {library_id} 不存在"}
        if ctx.deps.bridge.watcher is not None:
            ctx.deps.bridge.watcher.remove_library(library_id)
        await ctx.deps.repo.delete_library(library_id)
        return TOOL_OK

    @cap.tool
    async def enqueue_library_refresh(
        ctx: RunContext[AgentDeps],
        library_id: int,
        scan_add: bool = True,
    ) -> dict[str, Any]:
        """Enqueue a REFRESH task to discover files; invalid-record cleanup is a separate operation."""
        lib = await ctx.deps.repo.get_library(library_id)
        if lib is None:
            return {"error": f"library {library_id} 不存在"}
        if not scan_add:
            return {"error": "扫描新文件任务必须启用 scan_add；清理失效记录请使用独立清理操作"}
        scan = {ScanMode.add}
        scan_paths = lib.scan_paths or [lib.path]
        task = await ctx.deps.repo.create_task(
            TaskType.REFRESH,
            RefreshPayload(
                library_id=library_id,
                recursive=lib.recursive,
                patterns=list(lib.patterns or []),
                path=scan_paths[0],
                paths=scan_paths,
                scan=scan,
                scrape=set(),
            ),
        )
        assert task.id is not None
        return {"task_ids": [task.id], "task_id": task.id}

    return cap
