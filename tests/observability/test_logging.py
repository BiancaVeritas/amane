"""structlog 集成测试 - 验证核心管线行为."""

import json
import logging
from typing import TYPE_CHECKING

import pytest
import structlog

from amane.observability import setup_logging
from amane.observability.recorder import TaskIdFilter, _task_id_ctx

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _reset_logging():
    """每个测试前清理 logging 和 structlog 状态."""
    # 清除 amane logger 的 handlers (setup_logging 幂等检查依赖 handlers)
    root = logging.getLogger("amane")
    root.handlers.clear()
    req = logging.getLogger("amane.request")
    req.handlers.clear()
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()
    yield
    root.handlers.clear()
    req.handlers.clear()
    structlog.contextvars.clear_contextvars()


class TestSetupLogging:
    """setup_logging 管线配置."""

    def test_contextvars_injected_into_json_output(self, tmp_path: Path):
        """stdlib logger 的输出中包含 contextvars 绑定的字段."""
        setup_logging(level="DEBUG", log_dir=tmp_path)

        structlog.contextvars.bind_contextvars(task_id=99, number="TEST-001")
        logger = logging.getLogger("amane.test")
        logger.info("hello from test")

        log_file = tmp_path / "app.log"
        assert log_file.exists()

        lines = log_file.read_text().strip().splitlines()
        assert len(lines) >= 1

        record = json.loads(lines[-1])
        assert record["event"] == "hello from test"
        assert record["task_id"] == 99
        assert record["number"] == "TEST-001"
        assert record["logger"] == "amane.test"
        assert record["level"] == "info"
        assert "timestamp" in record

    def test_idempotent(self, tmp_path: Path):
        """多次调用 setup_logging 不会重复添加 handler."""
        setup_logging(level="INFO", log_dir=tmp_path)
        setup_logging(level="INFO", log_dir=tmp_path)
        setup_logging(level="INFO", log_dir=tmp_path)

        root = logging.getLogger("amane")
        # console + file + (no event_bus)
        assert len(root.handlers) == 2


class TestTaskIdFilter:
    """TaskIdFilter 实现 per-task 日志隔离."""

    def test_filter_matches_current_context(self):
        """当 ContextVar 匹配时允许通过."""
        _task_id_ctx.set(42)
        f = TaskIdFilter(42)
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        assert f.filter(record) is True

    def test_filter_rejects_other_context(self):
        """当 ContextVar 不匹配时拒绝."""
        _task_id_ctx.set(99)
        f = TaskIdFilter(42)
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        assert f.filter(record) is False

    def test_filter_rejects_no_context(self):
        """当 ContextVar 为 None (未设置) 时拒绝."""
        _task_id_ctx.set(None)
        f = TaskIdFilter(42)
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        assert f.filter(record) is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_concurrent_isolation(self, tmp_path: Path):
        """
        并发任务各自只写入自己的日志文件 - 验证 bug fix.

        模拟 3 个并发 task, 每个绑定不同 task_id, 各写入独立文件.
        """
        import asyncio

        setup_logging(level="DEBUG", log_dir=tmp_path)
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        root_logger = logging.getLogger("amane")

        # 安装 3 个带 TaskIdFilter 的 handler (与生产一致: task-{id}/task.log)
        handlers = {}
        for tid in (1, 2, 3):
            task_dir = tasks_dir / f"task-{tid}"
            task_dir.mkdir()
            h = logging.FileHandler(task_dir / "task.log", encoding="utf-8")
            h.setFormatter(
                structlog.stdlib.ProcessorFormatter(
                    processors=[
                        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                        structlog.processors.JSONRenderer(),
                    ],
                    foreign_pre_chain=[
                        structlog.contextvars.merge_contextvars,
                        structlog.stdlib.add_log_level,
                        structlog.processors.TimeStamper(fmt="iso", utc=True),
                    ],
                )
            )
            h.addFilter(TaskIdFilter(tid))
            root_logger.addHandler(h)
            handlers[tid] = h

        async def simulate_task(task_id: int):
            _task_id_ctx.set(task_id)
            structlog.contextvars.clear_contextvars()
            structlog.contextvars.bind_contextvars(task_id=task_id)
            logger = logging.getLogger("amane.worker")
            for i in range(5):
                logger.info(f"step {i}")
                await asyncio.sleep(0)

        # 并发执行
        await asyncio.gather(simulate_task(1), simulate_task(2), simulate_task(3))

        # 清理
        for h in handlers.values():
            root_logger.removeHandler(h)
            h.close()

        # 验证: 每个文件恰好 5 条, 且 task_id 一致
        for tid in (1, 2, 3):
            lines = (tasks_dir / f"task-{tid}" / "task.log").read_text().strip().splitlines()
            assert len(lines) == 5, f"task-{tid}/task.log has {len(lines)} lines, expected 5"
            for line in lines:
                record = json.loads(line)
                assert record["task_id"] == tid


class TestPathSeparators:
    """日志里的路径统一用 ``/``.

    库根存 ``/``, 而扫描与索引产物在 Windows 上带 ``\\``; 同一份日志里两种写法并存
    看着像出错, 读者也无从判断它们是不是同一个目录.
    """

    def test_path_fields_use_forward_slash(self, tmp_path: Path):
        setup_logging(level="DEBUG", log_dir=tmp_path)
        logger = structlog.get_logger("amane.test")

        logger.info("scan walking started", path="\\\\192.168.31.100\\NAS\\Downloads\\AV")
        logger.info("inventory scanned", scope="C:\\Users\\a\\lib")
        logger.info("watching library", path="//192.168.31.100/NAS/x", watch_path="C:\\a\\b")

        log_file = tmp_path / "app.log"
        records = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
        by_event = {r["event"]: r for r in records}

        assert by_event["scan walking started"]["path"] == "//192.168.31.100/NAS/Downloads/AV"
        assert by_event["inventory scanned"]["scope"] == "C:/Users/a/lib"
        assert by_event["watching library"]["path"] == "//192.168.31.100/NAS/x"
        assert by_event["watching library"]["watch_path"] == "C:/a/b"

    def test_non_path_fields_untouched(self, tmp_path: Path):
        """字段名不在路径清单里就不动: 正则与摘要里的反斜杠是内容的一部分."""
        setup_logging(level="DEBUG", log_dir=tmp_path)
        structlog.get_logger("amane.test").info("regex failed", pattern="\\\\d+\\w")

        records = [
            json.loads(line)
            for line in (tmp_path / "app.log").read_text(encoding="utf-8").splitlines()
        ]

        assert records[0]["pattern"] == "\\\\d+\\w"
