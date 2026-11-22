"""heydouga 条目类型迁移: 无码 → 独立类型."""

from pathlib import Path

from alembic import command
from sqlalchemy import Engine, create_engine, text

from tests.helpers import alembic_config

_PREVIOUS = "b5587b48da56"


def _seed(conn, rows: list[tuple[str, str]]) -> None:
    for number, content_type in rows:
        conn.execute(
            text(
                "INSERT INTO metadata (number, content_type, created_at, updated_at) "
                "VALUES (:number, :content_type, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            ),
            {"number": number, "content_type": content_type},
        )


def _entry_types(engine: Engine) -> dict[str, str]:
    with engine.connect() as conn:
        return {
            row.number: row.content_type
            for row in conn.execute(text("SELECT number, content_type FROM metadata")).all()
        }


def test_heydouga_entries_retyped(tmp_path: Path) -> None:
    """升级把 heydouga 条目从无码改成独立类型; 其它行不动, downgrade 可回滚."""
    db_path = tmp_path / "migrate.db"
    cfg = alembic_config(db_path)

    command.upgrade(cfg, _PREVIOUS)
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        _seed(
            conn,
            [
                # 大小写与两段写法都要命中; 前缀规则会把它们判成无码
                ("Heydouga-4037-1002", "UNCENSORED"),
                ("heydouga-4085-001", "UNCENSORED"),
                # 名字里含 heydouga 但不是该站番号的保持原值
                ("Heydouga-Like-9001", "CENSORED"),
                ("HEYZO-1234", "UNCENSORED"),
                ("MIDV-123", "CENSORED"),
            ],
        )

    command.upgrade(cfg, "head")

    assert _entry_types(engine) == {
        "Heydouga-4037-1002": "HEYDOUGA",
        "heydouga-4085-001": "HEYDOUGA",
        "Heydouga-Like-9001": "CENSORED",
        "HEYZO-1234": "UNCENSORED",
        "MIDV-123": "CENSORED",
    }

    command.downgrade(cfg, _PREVIOUS)
    assert _entry_types(engine) == {
        "Heydouga-4037-1002": "UNCENSORED",
        "heydouga-4085-001": "UNCENSORED",
        "Heydouga-Like-9001": "CENSORED",
        "HEYZO-1234": "UNCENSORED",
        "MIDV-123": "CENSORED",
    }

    engine.dispose()
