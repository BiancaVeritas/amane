"""与 r18 schema 的唯一契约: 只 SELECT 点名的列.

导入期用同款 SELECT 做 schema 探针; 列被删除或改名则拒绝换名.
"""

import re
from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import R18Named, R18Person, R18Video, R18VideoDetail


def _normalize(number: str) -> tuple[str, str] | None:
    m = re.search(r"([a-zA-Z]+)-?(\d+)", number)
    if not m:
        return None
    return m.group(1).lower(), m.group(2)


def content_id_candidates(number: str) -> list[str]:
    # 优先级: 5 位零填充 > 保留原始零填充 > 去掉前导零.
    parsed = _normalize(number)
    if not parsed:
        return []
    prefix, digits = parsed
    digits_int = digits.lstrip("0") or "0"
    padded = f"{prefix}{digits_int:0>5}"
    preserved = f"{prefix}{digits}"
    plain = f"{prefix}{digits_int}"
    out: list[str] = []
    for c in (padded, preserved, plain):
        if c not in out:
            out.append(c)
    return out


_VIDEO_COLS = (
    "content_id, service_code, dvd_id, title_en, title_ja, comment_en, comment_ja, "
    "runtime_mins, release_date, sample_url, maker_id, label_id, series_id, "
    "jacket_full_url, jacket_cover_url, gallery_full_first, gallery_full_last, "
    "gallery_thumb_first, gallery_thumb_last"
)


class R18Repository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def find_video_row(self, number: str) -> dict | None:
        # dvd_id 精确 (大小写不敏感) 或 content_id 落在零填充变体集合内.
        # 多行命中时取 release_date 最新的一条.
        candidates = content_id_candidates(number)
        stmt = text(
            f"SELECT {_VIDEO_COLS} FROM derived_video "
            "WHERE upper(dvd_id) = upper(:number) OR content_id = ANY(:cids) "
            "ORDER BY release_date DESC NULLS LAST LIMIT 1"
        )
        result = await self.session.execute(stmt, {"number": number, "cids": candidates})
        row = result.mappings().first()
        return dict(row) if row else None

    async def get_detail(self, number: str) -> R18VideoDetail | None:
        row = await self.find_video_row(number)
        if row is None:
            return None

        video = R18Video.model_validate(row)
        cid = video.content_id

        maker = await self._get_named("derived_maker", row.get("maker_id"))
        label = await self._get_named("derived_label", row.get("label_id"))
        series = await self._get_named("derived_series", row.get("series_id"))
        actresses = await self._actresses(cid)
        actors = await self._actors(cid)
        directors = await self._directors(cid)
        categories = await self._categories(cid)
        trailer = await self._trailer(cid)

        return R18VideoDetail(
            video=video,
            maker=maker,
            label=label,
            series=series,
            actresses=actresses,
            actors=actors,
            directors=directors,
            categories=categories,
            trailer_url=trailer or video.sample_url,
        )

    async def _get_named(self, table: str, entity_id: int | None) -> R18Named | None:
        if entity_id is None:
            return None
        # table 来自硬编码常量, 非用户输入.
        stmt = text(f"SELECT id, name_en, name_ja FROM {table} WHERE id = :id")
        row = (await self.session.execute(stmt, {"id": entity_id})).mappings().first()
        return R18Named.model_validate(dict(row)) if row else None

    async def _actresses(self, content_id: str) -> list[R18Person]:
        stmt = text(
            "SELECT a.id, a.name_romaji, a.name_kanji, a.name_kana "
            "FROM derived_video_actress va JOIN derived_actress a ON a.id = va.actress_id "
            "WHERE va.content_id = :cid ORDER BY va.ordinality NULLS LAST"
        )
        rows = (await self.session.execute(stmt, {"cid": content_id})).mappings().all()
        return [R18Person.model_validate(dict(r)) for r in rows]

    async def _actors(self, content_id: str) -> list[R18Person]:
        stmt = text(
            "SELECT a.id, NULL AS name_romaji, a.name_kanji, a.name_kana "
            "FROM derived_video_actor va JOIN derived_actor a ON a.id = va.actor_id "
            "WHERE va.content_id = :cid ORDER BY va.ordinality NULLS LAST"
        )
        rows = (await self.session.execute(stmt, {"cid": content_id})).mappings().all()
        return [R18Person.model_validate(dict(r)) for r in rows]

    async def _directors(self, content_id: str) -> list[R18Person]:
        stmt = text(
            "SELECT d.id, d.name_romaji, d.name_kanji, d.name_kana "
            "FROM derived_video_director vd JOIN derived_director d ON d.id = vd.director_id "
            "WHERE vd.content_id = :cid"
        )
        rows = (await self.session.execute(stmt, {"cid": content_id})).mappings().all()
        return [R18Person.model_validate(dict(r)) for r in rows]

    async def _categories(self, content_id: str) -> list[R18Named]:
        stmt = text(
            "SELECT c.id, c.name_en, c.name_ja "
            "FROM derived_video_category vc JOIN derived_category c ON c.id = vc.category_id "
            "WHERE vc.content_id = :cid"
        )
        rows = (await self.session.execute(stmt, {"cid": content_id})).mappings().all()
        return [R18Named.model_validate(dict(r)) for r in rows]

    async def _trailer(self, content_id: str) -> str | None:
        stmt = text("SELECT url FROM source_dmm_trailer WHERE content_id = :cid LIMIT 1")
        row = (await self.session.execute(stmt, {"cid": content_id})).first()
        return row[0] if row else None

    @staticmethod
    def schema_probes() -> Sequence[str]:
        # 覆盖运行时实际依赖的表与列; 任一条失败则拒绝换名. 修改查询时必须同步修改此处.
        return (
            f"SELECT {_VIDEO_COLS} FROM derived_video LIMIT 1",
            "SELECT id, name_en, name_ja FROM derived_maker LIMIT 1",
            "SELECT id, name_en, name_ja FROM derived_label LIMIT 1",
            "SELECT id, name_en, name_ja FROM derived_series LIMIT 1",
            "SELECT id, name_en, name_ja FROM derived_category LIMIT 1",
            "SELECT id, name_romaji, name_kanji, name_kana FROM derived_actress LIMIT 1",
            "SELECT id, name_kanji, name_kana FROM derived_actor LIMIT 1",
            "SELECT id, name_romaji, name_kanji, name_kana FROM derived_director LIMIT 1",
            "SELECT content_id, actress_id, ordinality FROM derived_video_actress LIMIT 1",
            "SELECT content_id, actor_id, ordinality FROM derived_video_actor LIMIT 1",
            "SELECT content_id, category_id FROM derived_video_category LIMIT 1",
            "SELECT content_id, director_id FROM derived_video_director LIMIT 1",
            "SELECT content_id, url FROM source_dmm_trailer LIMIT 1",
        )
