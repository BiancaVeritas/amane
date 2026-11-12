"""LTX 历史文件名重新投影测试."""

from pathlib import Path

from alembic import command
from sqlalchemy import create_engine, text

from tests.helpers import alembic_config


def test_reprojects_only_ltx_files(tmp_path: Path) -> None:
    db_path = tmp_path / "ltx-migration.db"
    cfg = alembic_config(db_path)
    command.upgrade(cfg, "9054ebacea29")
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO libraries (name, path, automation, recursive, patterns, video_template) "
                "VALUES ('A', '/a', 1, 1, '[]', '{number}.{ext}')"
            )
        )
        library_id = conn.execute(text("SELECT id FROM libraries WHERE name='A'")).scalar_one()
        for path in ("/a/ABC-123-LTX.mp4", "/a/ABC-124.mp4"):
            conn.execute(
                text(
                    "INSERT INTO media_files (path, status, library_id, created_at, updated_at, mosaic) "
                    "VALUES (:path, 'PENDING', :library_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'UNCENSORED')"
                ),
                {"path": path, "library_id": library_id},
            )
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT path, mosaic FROM media_files ORDER BY path")).all()
    engine.dispose()
    assert rows == [("/a/ABC-123-LTX.mp4", "CRACKED"), ("/a/ABC-124.mp4", "UNCENSORED")]
