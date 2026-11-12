from __future__ import annotations

from typing import TYPE_CHECKING

from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Iterator

    from ..embeddings import EmbeddingStore
    from ..repository import MediaChangeHook

# SQLite 单条语句的绑定变量上限 (3.32+ 为 32766). 500 给语句里其它占位留余量.
SQL_IN_CHUNK_SIZE = 500


def chunks[T](items: list[T], *, size: int = SQL_IN_CHUNK_SIZE) -> Iterator[list[T]]:
    """按 ``size`` 分批绑定数量随数据增长的 id 列表.

    超出 SQLite 绑定变量上限会报 ``too many SQL variables``; 而语句只是「重」但没超限时,
    Windows 上还会报一个更误导的 ``unable to open database file``. 删除这类多列 OR
    条件尤其容易撞上, 所以允许调用方给更小的批.
    """
    for start in range(0, len(items), size):
        yield items[start : start + size]


class RepositoryMixinBase:
    """子类须提供 ``_session()``. 任务 mixin 还依赖 ``_task_insert_lock`` 串行化入队互斥."""

    _task_insert_lock: asyncio.Lock
    # 向量 sidecar 与图片变化通知的钩子; 仅条目写路径与级联删除使用.
    _embeddings: EmbeddingStore | None
    _media_hook: MediaChangeHook | None

    def _session(self) -> AsyncSession:
        raise NotImplementedError

    async def notify_media_changed(self, metadata_id: int) -> None:
        raise NotImplementedError
