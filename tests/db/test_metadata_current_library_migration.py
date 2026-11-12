"""Metadata 当前媒体库归属迁移回填测试."""

from pathlib import Path

from alembic import command
from sqlalchemy import create_engine, text

from tests.helpers import alembic_config


def test_backfill_one_current_library_per_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "metadata-library.db"
    cfg = alembic_config(db_path)
    command.upgrade(cfg, "c91f7a2e6d40")
    engine = create_engine(f"sqlite:///{db_path}")

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO libraries (name, path, automation, recursive, patterns, video_template) "
                "VALUES ('A', '/a', 1, 1, '[]', '{number}.{ext}')"
            )
        )
        library_a = conn.execute(text("SELECT id FROM libraries WHERE name='A'")).scalar_one()
        conn.execute(
            text(
                "INSERT INTO libraries (name, path, automation, recursive, patterns, video_template) "
                "VALUES ('B', '/b', 1, 1, '[]', '{number}.{ext}')"
            )
        )
        library_b = conn.execute(text("SELECT id FROM libraries WHERE name='B'")).scalar_one()
        conn.execute(
            text(
                "INSERT INTO metadata (number, content_type, created_at, updated_at) "
                "VALUES ('MIG-001', 'CENSORED', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        metadata_id = conn.execute(text("SELECT id FROM metadata WHERE number='MIG-001'")).scalar_one()
        for library_id, path, created in (
            (library_a, "/a/old.mp4", "2025-01-01 00:00:00"),
            (library_b, "/b/new.mp4", "2026-01-01 00:00:00"),
        ):
            conn.execute(
                text(
                    "INSERT INTO media_files (path, status, library_id, metadata_id, created_at, updated_at, "
                    "mosaic, has_subtitle_in_name, has_external_subtitle) "
                    "VALUES (:path, 'SCRAPED', :library_id, :metadata_id, :created, :created, NULL, 0, 0)"
                ),
                {"path": path, "library_id": library_id, "metadata_id": metadata_id, "created": created},
            )

    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT metadata_id, library_id FROM metadata_library WHERE metadata_id=:id"),
            {"id": metadata_id},
        ).all()
    engine.dispose()
    assert rows == [(metadata_id, library_b)]
