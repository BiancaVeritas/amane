"""迁移: 独立保存原始海报 URL, 从旧 raw 站点快照恢复."""

from __future__ import annotations

import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from tests.helpers import alembic_config


def test_original_poster_urls_are_restored_from_raw_snapshots(tmp_path: Path) -> None:
    """只从 raw 恢复原始地址, 不把旧 poster_urls 中的内部裁剪资源误认为原始 URL."""
    cfg: Config = alembic_config(tmp_path / "original-poster.db")
    # 先停在新迁移的一个父 revision, 插入没有新列的旧数据, 再升级到 head.
    command.upgrade(cfg, "4b8d2e7c1a90")
    url = cfg.get_main_option("sqlalchemy.url")
    assert url is not None
    engine = create_engine(url)

    raw = {
        "javdb": {"poster_urls": ["https://javdb.example/poster.jpg"]},
        "javbus": {"poster_url": "https://javbus.example/poster.jpg"},
        "dmm": {"poster_urls": ["https://dmm.example/derived-from-cover.jpg"]},
        "avbase": {"poster_urls": ["https://avbase.example/derived-from-cover.jpg"]},
        "explicit_empty": {"original_poster_urls": [], "poster_urls": ["https://example/derived.jpg"]},
    }
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO metadata (number, actors, directors, tags, poster_urls, thumb_urls, "
                "trailer_urls, extrafanart_urls, scores, external_ids, source_urls, field_sources, raw, "
                "created_at, updated_at) VALUES (:number, '[]', '[]', '[]', :poster_urls, '[]', '[]', "
                "'{}', '{}', '{}', '{}', '{}', :raw, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "number": "ORIGINAL-POSTER-MIGRATION",
                "poster_urls": json.dumps(["/api/resources/internal-cropped-poster"]),
                "raw": json.dumps(raw),
            },
        )

    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        saved = conn.execute(
            text("SELECT original_poster_urls FROM metadata WHERE number = 'ORIGINAL-POSTER-MIGRATION'")
        ).scalar_one()
    urls = json.loads(saved) if isinstance(saved, str) else saved
    assert urls == [
        "https://javdb.example/poster.jpg",
        "https://javbus.example/poster.jpg",
    ]
    assert "/api/resources/internal-cropped-poster" not in urls
    assert all("derived-from-cover" not in url and "derived.jpg" not in url for url in urls)
    engine.dispose()
