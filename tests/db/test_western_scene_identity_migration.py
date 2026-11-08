"""Migration: WESTERN Metadata 以 Scene ID 唯一, 其它类型继续按番号唯一."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.helpers import alembic_config


@pytest.fixture
def alembic_cfg(tmp_path: Path) -> Config:
    return alembic_config(tmp_path / "western-identity.db")


def test_western_theporndb_uuidentity_indexes(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "2a7c9e1d4b60")
    url = alembic_cfg.get_main_option("sqlalchemy.url")
    assert url is not None
    engine = create_engine(url)
    with engine.begin() as conn:
        # 旧 WESTERN 记录保留, 暂时没有 Scene ID, 等用户重新刮削补齐.
        conn.execute(
            text(
                "INSERT INTO metadata "
                "(number, content_type, created_at, updated_at) "
                "VALUES "
                "('LEGACY-001', 'WESTERN', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    command.upgrade(alembic_cfg, "head")

    with engine.begin() as conn:
        columns = {column["name"] for column in inspect(conn).get_columns("metadata")}
        assert "theporndb_uuid" in columns
        indexes = {index["name"] for index in inspect(conn).get_indexes("media_files")}
        assert "ix_media_files_oshash" in indexes
        legacy = conn.execute(
            text("SELECT theporndb_uuid FROM metadata WHERE number = 'LEGACY-001'")
        ).scalar_one()
        assert legacy is None

        conn.execute(
            text("INSERT INTO metadata (number, content_type, theporndb_uuid, created_at, updated_at) VALUES ('SAME-001', 'WESTERN', 'scene-a', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
        )
        conn.execute(
            text("INSERT INTO metadata (number, content_type, theporndb_uuid, created_at, updated_at) VALUES ('SAME-001', 'WESTERN', 'scene-b', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
        )
        with pytest.raises(IntegrityError):
            conn.execute(
                text("INSERT INTO metadata (number, content_type, theporndb_uuid, created_at, updated_at) VALUES ('OTHER-001', 'WESTERN', 'SCENE-A', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
            )

        conn.execute(
            text("INSERT INTO metadata (number, content_type, created_at, updated_at) VALUES ('JAV-001', 'CENSORED', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
        )
        with pytest.raises(IntegrityError):
            conn.execute(
                text("INSERT INTO metadata (number, content_type, created_at, updated_at) VALUES ('jav-001', 'CENSORED', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
            )

    engine.dispose()
