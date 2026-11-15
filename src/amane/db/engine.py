from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from .profiling import DbLogPolicy, install_profiling
from .sqlite_migrate import upgrade_sqlite_database

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = structlog.get_logger()

# 写锁等待上限. 驱动默认 5s, 并发 worker 与 API 同进程写单库、机械盘持锁时间长时不足.
BUSY_TIMEOUT_MS = 30_000

# 常驻连接数. SQLite 写是全库串行的, 连接多只会增加锁竞争与建连开销.
POOL_SIZE = 5
POOL_MAX_OVERFLOW = 5
# 等一条空闲连接的时限; 比 busy_timeout 短, 让排队在池层就失败得更快.
POOL_TIMEOUT_S = 30.0
# 定期重建连接: 长时间运行后驱动侧偶有状态残留, 换一条更稳.
POOL_RECYCLE_S = 1800


async def create_async_engine_from_path(db_path: Path | str, *, policy: DbLogPolicy | None = None) -> AsyncEngine:
    """先 ``upgrade_sqlite_database``, 再开业务引擎. 事务性 DDL 仅用于迁移连接.

    ``policy`` 给出时注册慢路径日志.
    """
    path = Path(db_path)
    try:
        upgrade_sqlite_database(path)
    except Exception as e:
        raise RuntimeError(f"Database migration failed: {e}") from e

    url = f"sqlite+aiosqlite:///{path}"
    # 显式给连接池: 驱动默认对 SQLite 用 NullPool, 每个会话都新建连接, 而建连接要跑一遍
    # pragma (含 journal_mode 的排他锁与 -shm 打开), 并发 worker 下既慢又容易撞锁.
    # SQLite 单文件写入本就串行, 少量常驻连接足够, 多余的排队交给 busy_timeout.
    engine = create_async_engine(
        url,
        echo=False,
        pool_size=POOL_SIZE,
        max_overflow=POOL_MAX_OVERFLOW,
        pool_timeout=POOL_TIMEOUT_S,
        pool_recycle=POOL_RECYCLE_S,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        # 这些设置都是连接级的: 每个新连接都必须重设, 漏设即退回驱动默认.
        cursor = dbapi_conn.cursor()
        cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        # journal_mode 是库级持久设置, 一旦切到 WAL 就记在库文件头里, 后续连接无需再设.
        # 而这条 PRAGMA 要求短暂排他锁并会打开 -shm 共享内存文件: 多个 worker 并发新建
        # 连接时它会与正在写的事务抢锁, 底层抛 SQLITE_IOERR, 表现为建连接那一刻的
        # "disk I/O error". 因此切换失败时不能让连接建立失败 —— 库停在原 journal 模式
        # 仍可用, 只是少了 WAL 的并发读.
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
        except Exception:
            logger.warning("switch to WAL journal mode failed", exc_info=True)
        # WAL 下掉电最多丢最近若干已提交事务且不损坏库, 换来提交不再逐笔 fsync.
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    if policy is not None:
        install_profiling(engine, policy)

    logger.info("database ready", path=str(path))
    return engine


@asynccontextmanager
async def get_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    """``expire_on_commit=False``, 提交后仍可读取实例属性."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
