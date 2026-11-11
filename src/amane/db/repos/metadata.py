from collections.abc import Collection, Mapping, Sequence
from datetime import datetime
from typing import Unpack, cast

import structlog
from sqlalchemy import ColumnElement, distinct, func, or_, text, update
from sqlalchemy import delete as sqla_delete
from sqlalchemy import update as sqla_update
from sqlalchemy.orm import Mapped
from sqlalchemy.sql.functions import count
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ...enums import ActorGender, MetadataField
from ...parsing import ContentType, Mosaic, infer_content_type
from ...utils.collections import unique_ids
from ...utils.text import normalize_long_text
from ..models import (
    Comment,
    MediaFile,
    MediaFileStatus,
    MediaIdentity,
    MediaIdentityType,
    Metadata,
    MetadataActor,
    MetadataDirector,
    MetadataSortField,
    MetadataTag,
    MetadataUserTag,
    Publisher,
    Series,
    SortOrder,
    Studio,
    has_subtitle_predicate,
    metadata_number_predicate,
    metadata_vr_predicate,
)
from ..repo_types import (
    MetadataFields,
    WriteMode,
    _metadata_has_files_clause,
    _metadata_linked_file_exists,
    _metadata_primary_order,
    _metadata_uncensored_predicate,
    _utcnow,
)
from .base import RepositoryMixinBase, acquire_write_lock, chunks
from .facet_helpers import (
    apply_facet_rules_to_metadata,
    cascade_delete_metadata,
    clean_actor_names,
    load_rules_by_kind,
    resolve_scalar_facet_names,
    sync_metadata_facets,
)

logger = structlog.get_logger()


def _normalize_text_fields(fields: MetadataFields) -> MetadataFields:
    """长文本列在落库前归一 (与聚合输出同一函数, 幂等).

    覆盖面是全部写库路径: 首刮 / 补刮 / merge / REST PATCH / Agent 工具都经这两个写方法.
    """
    updates: MetadataFields = {**fields}
    for key in ("plot", "original_plot"):
        value = updates.get(key)
        if isinstance(value, str):
            updates[key] = normalize_long_text(value)
    return updates


def _image_signature(metadata: Metadata) -> tuple[list[str], list[str], dict[str, list[str]]]:
    """封面与剧照的来源字段; 变化即向量失效 (缩略图作为封面回退, 一并纳入)."""
    return (metadata.poster_urls, metadata.thumb_urls, metadata.extrafanart_urls)


def _merge_raw_snapshots(
    target: Mapping[str, Mapping[str, object]],
    sources: Sequence[Mapping[str, Mapping[str, object]]],
) -> dict[str, dict[str, object]]:
    """按站点补入来源快照: 目标已有的站点保持不变, 来源之间按传入顺序先到先得."""
    merged = {site: dict(snapshot) for site, snapshot in target.items()}
    for source in sources:
        for site, snapshot in source.items():
            merged.setdefault(site, dict(snapshot))
    return merged


def _facet_link_condition(
    link_metadata_id: Mapped[int],
    facet_column: Mapped[int],
    facet_ids: Sequence[int] | None,
) -> ColumnElement[bool] | None:
    """同一 kind 的多个 id 聚合成一条子查询: 条目必须挂满全部去重后的 id (AND).

    逐 id 各生成一条子查询会线性增加表达式深度, 约 1000 个 id 时 SQLite 报表达式过大;
    聚合写法的语义不变: 未知 id 使计数不足, 结果为空; 空入参不生成条件.
    """
    id_list = list(unique_ids(facet_ids))
    if not id_list:
        return None
    matched = (
        select(link_metadata_id)
        .where(facet_column.in_(id_list))
        .group_by(link_metadata_id)
        .having(count(distinct(facet_column)) == len(id_list))
    )
    return col(Metadata.id).in_(matched)


# 锁字段 (MetadataField) ↔ Metadata 列名; 仅 extrafanart / score 两名不同.
_LOCK_FIELD_COLUMN: dict[MetadataField, str] = {
    MetadataField.TITLE: "title",
    MetadataField.ORIGINAL_TITLE: "original_title",
    MetadataField.PLOT: "plot",
    MetadataField.ORIGINAL_PLOT: "original_plot",
    MetadataField.ACTORS: "actors",
    MetadataField.DIRECTORS: "directors",
    MetadataField.TAGS: "tags",
    MetadataField.SERIES: "series",
    MetadataField.RELEASE: "release",
    MetadataField.RUNTIME: "runtime",
    MetadataField.PUBLISHER: "publisher",
    MetadataField.STUDIO: "studio",
    MetadataField.POSTER_URLS: "poster_urls",
    MetadataField.THUMB_URLS: "thumb_urls",
    MetadataField.TRAILER_URLS: "trailer_urls",
    MetadataField.EXTRAFANART: "extrafanart_urls",
    MetadataField.SCORE: "scores",
}
_LOCK_COLUMN_FIELD: dict[str, MetadataField] = {column: field for field, column in _LOCK_FIELD_COLUMN.items()}
_LOCK_COLUMN_FIELD["series_names"] = MetadataField.SERIES


def _locked_fields_of(meta: Metadata) -> set[MetadataField]:
    """库内锁定集合; 非法存量值忽略."""
    locked: set[MetadataField] = set()
    for name in meta.locked_fields or []:
        try:
            locked.add(MetadataField(name))
        except ValueError:
            continue
    return locked


def _filter_locked(
    fields: MetadataFields, locked: set[MetadataField], existing_sources: Mapping[str, str]
) -> MetadataFields:
    """AUTO 写入: 跳过锁定列, ``field_sources`` 保留锁定字段的既有来源."""
    if not locked:
        return fields
    locked_columns = {column for column, field in _LOCK_COLUMN_FIELD.items() if field in locked}
    filtered = cast("MetadataFields", {key: value for key, value in fields.items() if key not in locked_columns})
    sources = filtered.get("field_sources")
    if sources is not None:
        preserved = {key: value for key, value in existing_sources.items() if key in locked}
        merged = {**preserved, **{key: value for key, value in sources.items() if key not in locked}}
        filtered = cast("MetadataFields", {**filtered, "field_sources": merged})
    return filtered


def _merge_auto_locks(fields: MetadataFields, locked: set[MetadataField]) -> list[str]:
    """MANUAL 写入: 把本次写入的可锁列并入锁定集."""
    written = {_LOCK_COLUMN_FIELD[key] for key in fields if key in _LOCK_COLUMN_FIELD}
    return [str(field) for field in MetadataField if field in locked | written]


class MetadataRepoMixin(RepositoryMixinBase):
    async def _metadata_filter_conditions(
        self,
        session: AsyncSession,
        *,
        keyword: str | None = None,
        actor_ids: Sequence[int] | None = None,
        director_ids: Sequence[int] | None = None,
        tag_ids: Sequence[int] | None = None,
        studio_ids: Sequence[int] | None = None,
        publisher_ids: Sequence[int] | None = None,
        series_ids: Sequence[int] | None = None,
        user_tag_ids: Sequence[int] | None = None,
        has_files: bool | None = None,
        has_subtitle: bool | None = None,
        vr: bool | None = None,
        mosaic: Mosaic | None = None,
        uncensored: bool | None = None,
        definition: str | None = None,
        content_type: ContentType | None = None,
        ids: Sequence[int] | None = None,
        id_subquery_sql: str | None = None,
        updated_before: datetime | None = None,
    ) -> list[ColumnElement[bool]] | None:
        """列表与候选集共用的 WHERE 条件; ``None`` 表示条件互斥, 结果必为空."""
        conditions: list[ColumnElement[bool]] = []
        if ids is not None:
            id_list = list(unique_ids(ids))
            if not id_list:
                return None
            conditions.append(col(Metadata.id).in_(id_list))
        if id_subquery_sql is not None:
            conditions.append(col(Metadata.id).in_(text(id_subquery_sql)))
        if keyword:
            search_pattern = f"%{keyword}%"
            conditions.append(
                or_(col(Metadata.number).ilike(search_pattern), col(Metadata.title).ilike(search_pattern))
            )
        for link_metadata_id, facet_column, facet_ids in (
            (col(MetadataActor.metadata_id), col(MetadataActor.actor_id), actor_ids),
            (col(MetadataDirector.metadata_id), col(MetadataDirector.director_id), director_ids),
            (col(MetadataTag.metadata_id), col(MetadataTag.tag_id), tag_ids),
            (col(MetadataUserTag.metadata_id), col(MetadataUserTag.user_tag_id), user_tag_ids),
        ):
            condition = _facet_link_condition(link_metadata_id, facet_column, facet_ids)
            if condition is not None:
                conditions.append(condition)
        studio_names = await resolve_scalar_facet_names(session, Studio, studio_ids)
        if studio_ids and not studio_names:
            return None
        if studio_names:
            conditions.append(col(Metadata.studio).in_(studio_names))
        publisher_names = await resolve_scalar_facet_names(session, Publisher, publisher_ids)
        if publisher_ids and not publisher_names:
            return None
        if publisher_names:
            conditions.append(col(Metadata.publisher).in_(publisher_names))
        series_names = await resolve_scalar_facet_names(session, Series, series_ids)
        if series_ids and not series_names:
            return None
        if series_names:
            from ..models import MetadataSeries

            series_ids_resolved = (
                await session.exec(select(Series.id).where(col(Series.name).in_(series_names)))
            ).all()
            conditions.append(
                col(Metadata.id).in_(
                    select(MetadataSeries.metadata_id).where(col(MetadataSeries.series_id).in_(series_ids_resolved))
                )
            )
        if has_files is not None:
            conditions.append(_metadata_has_files_clause(has_files=has_files))
        if vr is True:
            conditions.append(metadata_vr_predicate())
        elif vr is False:
            conditions.append(~metadata_vr_predicate())
        if has_subtitle is True:
            conditions.append(_metadata_linked_file_exists(has_subtitle_predicate()))
        elif has_subtitle is False:
            conditions.append(~_metadata_linked_file_exists(has_subtitle_predicate()))
        if mosaic is not None:
            conditions.append(_metadata_linked_file_exists(col(MediaFile.mosaic) == mosaic))
        if uncensored is not None:
            flag = _metadata_uncensored_predicate()
            conditions.append(flag if uncensored else ~flag)
        if definition is not None:
            conditions.append(_metadata_linked_file_exists(col(MediaFile.definition) == definition))
        if content_type is not None:
            conditions.append(col(Metadata.content_type) == content_type)
        if updated_before is not None:
            conditions.append(col(Metadata.updated_at) < updated_before)
        return conditions

    async def list_metadata(
        self,
        keyword: str | None = None,
        offset: int = 0,
        limit: int = 20,
        sort_by: MetadataSortField = MetadataSortField.UPDATED_AT,
        order: SortOrder = SortOrder.DESC,
        actor_ids: Sequence[int] | None = None,
        director_ids: Sequence[int] | None = None,
        tag_ids: Sequence[int] | None = None,
        studio_ids: Sequence[int] | None = None,
        publisher_ids: Sequence[int] | None = None,
        series_ids: Sequence[int] | None = None,
        user_tag_ids: Sequence[int] | None = None,
        has_files: bool | None = None,
        has_subtitle: bool | None = None,
        vr: bool | None = None,
        mosaic: Mosaic | None = None,
        uncensored: bool | None = None,
        definition: str | None = None,
        content_type: ContentType | None = None,
        ids: Sequence[int] | None = None,
        id_subquery_sql: str | None = None,
        updated_before: datetime | None = None,
    ) -> tuple[list[Metadata], int]:
        """关联类 facet 同 kind 多 id 为 AND; 标量类同 kind 多 id 为 OR (未知 id 忽略, 全部未知则空).
        跨 kind 始终 AND. 相位筛选项: 布尔 True=至少一份具备, False=没有任何一份具备.
        """
        async with self._session() as session:
            conditions = await self._metadata_filter_conditions(
                session,
                keyword=keyword,
                actor_ids=actor_ids,
                director_ids=director_ids,
                tag_ids=tag_ids,
                studio_ids=studio_ids,
                publisher_ids=publisher_ids,
                series_ids=series_ids,
                user_tag_ids=user_tag_ids,
                has_files=has_files,
                has_subtitle=has_subtitle,
                vr=vr,
                mosaic=mosaic,
                uncensored=uncensored,
                definition=definition,
                content_type=content_type,
                ids=ids,
                id_subquery_sql=id_subquery_sql,
                updated_before=updated_before,
            )
            if conditions is None:
                return [], 0
            base = select(Metadata)
            for condition in conditions:
                base = base.where(condition)

            count_stmt = select(count()).select_from(base.subquery())
            total: int = (await session.exec(count_stmt)).one() or 0
            stmt = (
                base.order_by(_metadata_primary_order(sort_by, order), col(Metadata.id).asc())
                .offset(offset)
                .limit(limit)
            )
            result = await session.exec(stmt)
            return list(result.all()), total

    async def list_metadata_ids(
        self,
        *,
        keyword: str | None = None,
        actor_ids: Sequence[int] | None = None,
        director_ids: Sequence[int] | None = None,
        tag_ids: Sequence[int] | None = None,
        studio_ids: Sequence[int] | None = None,
        publisher_ids: Sequence[int] | None = None,
        series_ids: Sequence[int] | None = None,
        user_tag_ids: Sequence[int] | None = None,
        has_files: bool | None = None,
        has_subtitle: bool | None = None,
        vr: bool | None = None,
        mosaic: Mosaic | None = None,
        uncensored: bool | None = None,
        definition: str | None = None,
        content_type: ContentType | None = None,
        ids: Sequence[int] | None = None,
        id_subquery_sql: str | None = None,
        updated_before: datetime | None = None,
    ) -> list[int]:
        """与 :meth:`list_metadata` 同一套筛选, 只取 id; 供语义检索先收窄候选集."""
        async with self._session() as session:
            conditions = await self._metadata_filter_conditions(
                session,
                keyword=keyword,
                actor_ids=actor_ids,
                director_ids=director_ids,
                tag_ids=tag_ids,
                studio_ids=studio_ids,
                publisher_ids=publisher_ids,
                series_ids=series_ids,
                user_tag_ids=user_tag_ids,
                has_files=has_files,
                has_subtitle=has_subtitle,
                vr=vr,
                mosaic=mosaic,
                uncensored=uncensored,
                definition=definition,
                content_type=content_type,
                ids=ids,
                id_subquery_sql=id_subquery_sql,
                updated_before=updated_before,
            )
            if conditions is None:
                return []
            stmt = select(col(Metadata.id))
            for condition in conditions:
                stmt = stmt.where(condition)
            return [row for row in (await session.exec(stmt)).all() if row is not None]

    async def get_metadata(self, metadata_id: int) -> Metadata | None:
        async with self._session() as session:
            return await session.get(Metadata, metadata_id)

    async def get_metadata_by_ids(self, ids: Sequence[int]) -> list[Metadata]:
        """按 id 批量取回, 顺序不保证; 调用方按需重排. 每批用 ``chunks`` 控制绑定变量数."""
        id_list = list(unique_ids(ids))
        if not id_list:
            return []
        async with self._session() as session:
            result: list[Metadata] = []
            for chunk in chunks(id_list):
                rows = (await session.exec(select(Metadata).where(col(Metadata.id).in_(chunk)))).all()
                result.extend(rows)
            return result

    async def get_metadata_by_number(self, number: str, *, content_type: ContentType | None = None) -> Metadata | None:
        """大小写不敏感查询番号. WESTERN 同番号可能有多条, 未指定类型且结果歧义时返回 None."""
        async with self._session() as session:
            stmt = select(Metadata).where(metadata_number_predicate(number))
            if content_type is not None:
                stmt = stmt.where(Metadata.content_type == content_type)
            stmt = stmt.limit(2)
            result = await session.exec(stmt)
            rows = result.all()
            return rows[0] if len(rows) == 1 else None

    async def get_metadata_by_theporndb_uuid(self, theporndb_uuid: str) -> Metadata | None:
        """按 WESTERN ThePornDB UUID 定位作品; ThePornDB UUID 是外部作品身份."""
        theporndb_uuid = theporndb_uuid.strip().casefold()
        async with self._session() as session:
            stmt = select(Metadata).where(
                Metadata.content_type == ContentType.WESTERN, Metadata.theporndb_uuid == theporndb_uuid
            )
            result = await session.exec(stmt)
            return result.first()

    async def get_metadata_by_identity(self, identity_type: MediaIdentityType, identity_value: str) -> Metadata | None:
        """通过稳定媒体身份定位 WESTERN Metadata。"""
        value = identity_value.strip().casefold()
        if not value:
            return None
        async with self._session() as session:
            stmt = (
                select(Metadata)
                .join(MediaIdentity, col(MediaIdentity.metadata_id) == col(Metadata.id))
                .where(
                    Metadata.content_type == ContentType.WESTERN,
                    MediaIdentity.identity_type == identity_type,
                    func.lower(MediaIdentity.identity_value) == value,
                )
            )
            return (await session.exec(stmt)).first()

    async def register_media_identity_in_session(
        self,
        session: AsyncSession,
        metadata_id: int,
        identity_type: MediaIdentityType,
        identity_value: str,
    ) -> MediaIdentity:
        """在调用方事务中幂等登记身份; 不提交事务."""
        value = identity_value.strip()
        if not value:
            raise ValueError("Media identity cannot be empty")
        metadata = await session.get(Metadata, metadata_id)
        if metadata is None or metadata.content_type != ContentType.WESTERN:
            raise ValueError(f"Metadata {metadata_id} does not exist or is not WESTERN")
        stmt = select(MediaIdentity).where(
            MediaIdentity.identity_type == identity_type,
            func.lower(MediaIdentity.identity_value) == value.casefold(),
        )
        existing = (await session.exec(stmt)).first()
        if existing is not None:
            if existing.metadata_id != metadata_id:
                raise ValueError(
                    f"Identity {identity_type}:{value} is already linked to Metadata {existing.metadata_id}"
                )
            return existing
        identity = MediaIdentity(metadata_id=metadata_id, identity_type=identity_type, identity_value=value)
        session.add(identity)
        await session.flush()
        return identity

    async def register_media_identity(
        self, metadata_id: int, identity_type: MediaIdentityType, identity_value: str
    ) -> MediaIdentity:
        """独立事务登记稳定身份; 已绑定其他作品时拒绝改绑."""
        async with self._session() as session:
            connection = await session.connection()
            await connection.exec_driver_sql("BEGIN IMMEDIATE")
            identity = await self.register_media_identity_in_session(
                session, metadata_id, identity_type, identity_value
            )
            await session.commit()
            await session.refresh(identity)
            return identity

    async def get_metadata_by_oshash_matches(self, oshash: str) -> list[Metadata]:
        """返回 OSHASH 已关联的全部不同 WESTERN Metadata, 供调用方识别冲突."""
        async with self._session() as session:
            identity_stmt = (
                select(Metadata)
                .join(MediaIdentity, col(MediaIdentity.metadata_id) == col(Metadata.id))
                .where(
                    MediaIdentity.identity_type == MediaIdentityType.LOCAL_OSHASH,
                    func.lower(MediaIdentity.identity_value) == oshash.strip().casefold(),
                    Metadata.content_type == ContentType.WESTERN,
                )
            )
            legacy_stmt = (
                select(Metadata)
                .join(MediaFile, col(MediaFile.metadata_id) == col(Metadata.id))
                .where(MediaFile.oshash == oshash, Metadata.content_type == ContentType.WESTERN)
            )
            rows = (await session.exec(identity_stmt)).all() + (await session.exec(legacy_stmt)).all()
            return list({row.id: row for row in rows if row.id is not None}.values())

    async def get_metadata_by_oshash(self, oshash: str) -> Metadata | None:
        """通过已关联 MediaFile 的 OSHASH 定位作品; 多条记录指向不同作品时返回 None."""
        rows = await self.get_metadata_by_oshash_matches(oshash)
        return rows[0] if len(rows) == 1 else None

    async def upsert_western_metadata_in_session(
        self,
        session: AsyncSession,
        theporndb_uuid: str,
        number: str,
        *,
        metadata_id: int | None = None,
        actor_genders: Mapping[str, ActorGender] | None = None,
        **kwargs: Unpack[MetadataFields],
    ) -> tuple[Metadata, bool]:
        """在调用方事务中创建或更新 WESTERN Metadata; 返回 (Metadata, 图片字段是否变化)."""
        theporndb_uuid = theporndb_uuid.strip().casefold()
        if not theporndb_uuid:
            raise ValueError("WESTERN Metadata requires a ThePornDB UUID")
        fields = _normalize_text_fields(kwargs)
        if "series_names" not in fields and "series" in fields:
            fields["series_names"] = [fields["series"]] if fields["series"] else []
        elif "series_names" in fields:
            fields["series_names"] = list(
                dict.fromkeys(n.strip() for n in fields["series_names"] if isinstance(n, str) and n.strip())
            )
            fields["series"] = fields["series_names"][0] if fields["series_names"] else None
        # WESTERN 的 ThePornDB UUID 只存放在专用列；external_ids 仅保留其他来源 ID。
        external_ids = fields.get("external_ids")
        if isinstance(external_ids, dict):
            fields["external_ids"] = {
                key: value for key, value in external_ids.items() if key.casefold() != "theporndb"
            }
        stmt = select(Metadata).where(
            Metadata.content_type == ContentType.WESTERN, Metadata.theporndb_uuid == theporndb_uuid
        )
        existing = (await session.exec(stmt)).first()
        selected = await session.get(Metadata, metadata_id) if metadata_id is not None else None
        if metadata_id is not None and selected is None:
            raise ValueError(f"Metadata {metadata_id} does not exist")
        if selected is not None:
            if selected.content_type != ContentType.WESTERN:
                raise ValueError(f"Metadata {metadata_id} is not WESTERN")
            if existing is not None and existing.id != selected.id:
                raise ValueError(f"ThePornDB UUID {theporndb_uuid} is already linked to Metadata {existing.id}")
            stored_uuid = selected.theporndb_uuid
            if stored_uuid and stored_uuid.casefold() != theporndb_uuid:
                raise ValueError(f"Metadata {metadata_id} already has a different ThePornDB UUID")
            existing = selected
        if existing is not None:
            if existing.number.casefold() != number.casefold():
                raise ValueError(
                    f"ThePornDB UUID {theporndb_uuid} number conflict: stored={existing.number!r}, incoming={number!r}"
                )
            before = _image_signature(existing)
            fields = _filter_locked(fields, _locked_fields_of(existing), existing.field_sources or {})
            existing.theporndb_uuid = theporndb_uuid
            for key, value in fields.items():
                setattr(existing, key, value)
            existing.updated_at = _utcnow()
            session.add(existing)
            await session.flush()
            await clean_actor_names(session, existing, actor_genders)
            await apply_facet_rules_to_metadata(session, existing)
            await sync_metadata_facets(session, existing)
            await session.flush()
            return existing, _image_signature(existing) != before

        meta = Metadata(number=number, content_type=ContentType.WESTERN, theporndb_uuid=theporndb_uuid, **fields)
        session.add(meta)
        await session.flush()
        await clean_actor_names(session, meta, actor_genders)
        await apply_facet_rules_to_metadata(session, meta)
        await sync_metadata_facets(session, meta)
        await session.flush()
        return meta, any(_image_signature(meta))

    async def upsert_western_metadata(
        self,
        theporndb_uuid: str,
        number: str,
        *,
        metadata_id: int | None = None,
        actor_genders: Mapping[str, ActorGender] | None = None,
        **kwargs: Unpack[MetadataFields],
    ) -> Metadata:
        """独立事务入口; 统一收尾流程应调用 upsert_western_metadata_in_session()."""
        async with self._session() as session:
            connection = await session.connection()
            await connection.exec_driver_sql("BEGIN IMMEDIATE")
            metadata, images_changed = await self.upsert_western_metadata_in_session(
                session,
                theporndb_uuid,
                number,
                metadata_id=metadata_id,
                actor_genders=actor_genders,
                **kwargs,
            )
            await session.commit()
            await session.refresh(metadata)
            assert metadata.id is not None
            if images_changed:
                await self.notify_media_changed(metadata.id)
            return metadata

    async def finalize_western_scrape(
        self,
        *,
        theporndb_uuid: str,
        number: str,
        media_file_id: int | None,
        oshash: str | None,
        file_115_id: str | None,
        metadata_id: int | None = None,
        actor_genders: Mapping[str, ActorGender] | None = None,
        **kwargs: Unpack[MetadataFields],
    ) -> Metadata:
        """WESTERN 成功收尾: Metadata、MediaFile 与稳定身份在同一事务提交."""
        metadata: Metadata
        images_changed = False
        async with self._session() as session:
            try:
                connection = await session.connection()
                await connection.exec_driver_sql("BEGIN IMMEDIATE")
                metadata, images_changed = await self.upsert_western_metadata_in_session(
                    session,
                    theporndb_uuid,
                    number,
                    metadata_id=metadata_id,
                    actor_genders=actor_genders,
                    **kwargs,
                )
                if metadata.id is None:
                    raise ValueError("WESTERN Metadata did not receive an ID")
                if media_file_id is not None:
                    media = await self.update_media_file_in_session(
                        session,
                        media_file_id,
                        status=MediaFileStatus.SCRAPED,
                        metadata_id=metadata.id,
                    )
                    if media is None:
                        raise ValueError(f"MediaFile {media_file_id} does not exist")
                if oshash:
                    await self.register_media_identity_in_session(
                        session, metadata.id, MediaIdentityType.LOCAL_OSHASH, oshash
                    )
                if file_115_id:
                    await self.register_media_identity_in_session(
                        session, metadata.id, MediaIdentityType.FILE_115_ID, file_115_id
                    )
                await session.commit()
                await session.refresh(metadata)
            except Exception:
                await session.rollback()
                raise
        assert metadata.id is not None
        if images_changed:
            await self.notify_media_changed(metadata.id)
        return metadata

    async def content_types_by_metadata_ids(self, metadata_ids: Sequence[int]) -> dict[int, ContentType]:
        """文件行派生相位用; 不存在的 id 不在结果里, 调用方按"没有条目"处理."""
        unique = list(dict.fromkeys(metadata_ids))
        if not unique:
            return {}
        async with self._session() as session:
            stmt = select(col(Metadata.id), col(Metadata.content_type)).where(col(Metadata.id).in_(unique))
            rows = (await session.exec(stmt)).all()
            return {row_id: content_type for row_id, content_type in rows if row_id is not None}

    async def upsert_metadata(
        self,
        number: str,
        *,
        content_type: ContentType | None = None,
        actor_genders: Mapping[str, ActorGender] | None = None,
        mode: WriteMode = WriteMode.AUTO,
        **kwargs: Unpack[MetadataFields],
    ) -> Metadata:
        """查重忽略大小写; 已存在时不改写 number 的原始大小写, 也不改写类型.
        自动刮削写入; 锁定行为见 docs/dev/data-model.md.
        判定与写入同处一个写事务, 见 docs/dev/database.md; 临界区内不允许出现等待.
        ``actor_genders`` 只填 ``Actor.gender`` 空位, 不是 Metadata 列.
        ``content_type`` 只在插入时落库, None 按番号推断; 已存在时不改写 (契约见 docs/dev/data-model.md).
        """
        resolved_type = content_type or infer_content_type(number)
        if content_type == ContentType.WESTERN:
            raise ValueError("WESTERN metadata must be upserted by ThePornDB UUID")
        fields = _normalize_text_fields(kwargs)
        if "series_names" not in fields and "series" in fields:
            fields["series_names"] = [fields["series"]] if fields["series"] else []
        elif "series_names" in fields:
            fields["series_names"] = list(
                dict.fromkeys(n.strip() for n in fields["series_names"] if isinstance(n, str) and n.strip())
            )
            fields["series"] = fields["series_names"][0] if fields["series_names"] else None
        async with self._session() as session:
            # pysqlite 只在 DML 之前发出 BEGIN, 查号落在事务之外时并发同号写入会双双判定为「无行」,
            # 后者在插入时违反 ix_metadata_number. 手工取得写锁后判定与写入同处一个事务.
            await acquire_write_lock(session)
            rules_by_kind = await load_rules_by_kind(session)
            stmt = select(Metadata).where(metadata_number_predicate(number))
            if content_type is not None:
                stmt = stmt.where(Metadata.content_type != ContentType.WESTERN)
            stmt = stmt.limit(2)
            result = await session.exec(stmt)
            rows = result.all()
            if len(rows) > 1:
                raise ValueError(f"Metadata number {number!r} is ambiguous; use ThePornDB UUID or Metadata ID")
            existing = rows[0] if rows else None
            if existing:
                before = _image_signature(existing)
                if mode is WriteMode.MANUAL:
                    # 手动搜索: 无视既有锁定, 但也不新增锁 ——
                    # 用户要的是「这次听我的」, 不是把这批字段永久钉住.
                    pass
                else:
                    fields = _filter_locked(fields, _locked_fields_of(existing), existing.field_sources or {})
                for key, value in fields.items():
                    if key == "source_urls":
                        # 显式重建: setattr 走 SQLAlchemy 属性描述符, JSON 列的键序在这里会丢.
                        existing.source_urls = dict(value)
                        continue
                    setattr(existing, key, value)
                existing.updated_at = _utcnow()
                session.add(existing)
                await session.flush()
                await clean_actor_names(session, existing, actor_genders, rules_by_kind)
                await apply_facet_rules_to_metadata(session, existing, rules_by_kind)
                await sync_metadata_facets(session, existing)
                await session.commit()
                await session.refresh(existing)
                assert existing.id is not None
                if _image_signature(existing) != before:
                    await self.notify_media_changed(existing.id)
                return existing
            meta = Metadata(number=number, content_type=resolved_type, **fields)
            session.add(meta)
            await session.flush()
            await clean_actor_names(session, meta, actor_genders, rules_by_kind)
            await apply_facet_rules_to_metadata(session, meta, rules_by_kind)
            await sync_metadata_facets(session, meta)
            await session.commit()
            await session.refresh(meta)
            assert meta.id is not None
            if any(_image_signature(meta)):
                await self.notify_media_changed(meta.id)
            return meta

    async def reorder_source_urls(self, metadata_id: int, site: str) -> bool:
        """把指定站点挪到 ``source_urls`` 第一位, 直接写 JSON 列.

        不走 ORM 属性赋值: identity map 里已有该行时, 赋值后再读会拿回缓存里的旧值,
        键序看着像没变. 这里自己序列化再整列回写, 顺序由字典本身决定.
        """
        async with self._session() as session:
            row = (
                await session.exec(
                    select(col(Metadata.source_urls)).where(col(Metadata.id) == metadata_id)
                )
            ).first()
            urls = dict(row or {})
            if site not in urls:
                return False
            ordered = {site: urls[site]}
            ordered.update({k: v for k, v in urls.items() if k != site})
            await session.exec(
                sqla_update(Metadata)
                .where(col(Metadata.id) == metadata_id)
                .values(source_urls=ordered)
            )
            await session.commit()
            return True

    async def update_metadata(
        self,
        metadata_id: int,
        *,
        mode: WriteMode = WriteMode.AUTO,
        force: bool = False,
        actor_genders: Mapping[str, ActorGender] | None = None,
        **updates: Unpack[MetadataFields],
    ) -> Metadata | None:
        """不存在返回 None; 写入策略见 WriteMode."""
        updates = _normalize_text_fields(updates)
        async with self._session() as session:
            # 读改写须同事务: 锁定集合合并、投影重建都依据本次读到的值, 见 docs/dev/database.md.
            await acquire_write_lock(session)
            rules_by_kind = await load_rules_by_kind(session)
            metadata = await session.get(Metadata, metadata_id)
            if metadata is None:
                return None
            before = _image_signature(metadata)
            locked = _locked_fields_of(metadata)
            if force:
                # 清理动作 (删图等): 锁拦不住显式操作, 也不因此新增锁.
                pass
            elif mode is WriteMode.AUTO:
                updates = _filter_locked(updates, locked, metadata.field_sources or {})
            else:
                metadata.locked_fields = _merge_auto_locks(updates, locked)
            # WESTERN 编辑界面沿用 external_ids 的 `theporndb` 行展示 UUID,
            # 但实际仍写入专用列; 不把 UUID 重复保存到 external_ids.
            external_ids = updates.get("external_ids")
            if metadata.content_type == ContentType.WESTERN and isinstance(external_ids, dict):
                uuid_entry = next(
                    (
                        (key, value)
                        for key, value in external_ids.items()
                        if isinstance(key, str) and key.casefold() == "theporndb"
                    ),
                    None,
                )
                if uuid_entry is not None:
                    raw_uuid = uuid_entry[1]
                    normalized_uuid = raw_uuid.strip().casefold() if isinstance(raw_uuid, str) else ""
                    if normalized_uuid:
                        duplicate = await session.exec(
                            select(Metadata.id).where(
                                Metadata.content_type == ContentType.WESTERN,
                                func.lower(Metadata.theporndb_uuid) == normalized_uuid,
                                Metadata.id != metadata_id,
                            )
                        )
                        if duplicate.first() is not None:
                            raise ValueError(f"ThePornDB UUID {normalized_uuid} is already linked to another Metadata")
                    metadata.theporndb_uuid = normalized_uuid or None
                updates["external_ids"] = {
                    key: value
                    for key, value in external_ids.items()
                    if not isinstance(key, str) or key.casefold() != "theporndb"
                }
            # 显式赋值, 禁止 setattr; 字段集由 MetadataFields 与 Metadata 静态对齐.
            if "title" in updates:
                metadata.title = updates["title"]
            if "original_title" in updates:
                metadata.original_title = updates["original_title"]
            if "actors" in updates:
                metadata.actors = updates["actors"]
            if "studio" in updates:
                metadata.studio = updates["studio"]
            if "publisher" in updates:
                metadata.publisher = updates["publisher"]
            if "release" in updates:
                metadata.release = updates["release"]
            if "runtime" in updates:
                metadata.runtime = updates["runtime"]
            if "tags" in updates:
                metadata.tags = updates["tags"]
            if "series_names" in updates:
                metadata.series_names = list(
                    dict.fromkeys(n.strip() for n in updates["series_names"] if isinstance(n, str) and n.strip())
                )
                metadata.series = metadata.series_names[0] if metadata.series_names else None
            elif "series" in updates:
                metadata.series = updates["series"]
                metadata.series_names = [updates["series"]] if updates["series"] else []
            if "plot" in updates:
                metadata.plot = updates["plot"]
            if "original_plot" in updates:
                metadata.original_plot = updates["original_plot"]
            if "directors" in updates:
                metadata.directors = updates["directors"]
            if "original_poster_urls" in updates:
                metadata.original_poster_urls = updates["original_poster_urls"]
            if "poster_urls" in updates:
                metadata.poster_urls = updates["poster_urls"]
            if "thumb_urls" in updates:
                metadata.thumb_urls = updates["thumb_urls"]
            if "thumb_candidates" in updates:
                metadata.thumb_candidates = updates["thumb_candidates"]
            if "trailer_urls" in updates:
                metadata.trailer_urls = updates["trailer_urls"]
            if "extrafanart_urls" in updates:
                metadata.extrafanart_urls = updates["extrafanart_urls"]
            if "scores" in updates:
                metadata.scores = updates["scores"]
            if "external_ids" in updates:
                metadata.external_ids = updates["external_ids"]
            if "source_urls" in updates:
                metadata.source_urls = updates["source_urls"]
            if "field_sources" in updates:
                metadata.field_sources = updates["field_sources"]
            if "raw" in updates:
                metadata.raw = updates["raw"]
            metadata.updated_at = _utcnow()
            session.add(metadata)
            await session.flush()
            await clean_actor_names(session, metadata, actor_genders, rules_by_kind)
            await apply_facet_rules_to_metadata(session, metadata, rules_by_kind)
            await sync_metadata_facets(session, metadata)
            await session.commit()
            await session.refresh(metadata)
            if _image_signature(metadata) != before:
                await self.notify_media_changed(metadata_id)
            return metadata

    async def merge_metadata_entries(self, target_id: int, source_ids: Sequence[int]) -> Metadata | None:
        """把来源条目的 raw 快照补入目标, 迁移关联记录后删除来源.

        目标字段与锁定集合不变: raw 以目标为准, 来源按传入顺序补入目标缺失的站点, 不重新聚合.
        媒体文件 / 评论 / 用户标签改挂目标 (标签去重), 分类投影随来源删除.
        target 不存在返回 None; 来源为空 / 含 target / 不存在则抛 ``ValueError``.
        """
        source_id_list = list(dict.fromkeys(source_ids))
        if target_id in source_id_list:
            raise ValueError("合并目标不能是来源之一")
        if not source_id_list:
            raise ValueError("未提供要合并的影片")

        async with self._session() as session:
            # 来源判定、raw 合并与关联迁移同处一个写事务: 否则会按旧快照整体写回 raw.
            await acquire_write_lock(session)
            target = await session.get(Metadata, target_id)
            if target is None:
                return None
            sources: list[Metadata] = []
            for source_id in source_id_list:
                source = await session.get(Metadata, source_id)
                if source is None:
                    raise ValueError(f"影片 {source_id} 不存在")
                sources.append(source)

            target.raw = _merge_raw_snapshots(target.raw or {}, [source.raw or {} for source in sources])
            target.updated_at = _utcnow()
            session.add(target)

            target_tag_ids = set(
                (
                    await session.exec(
                        select(MetadataUserTag.user_tag_id).where(col(MetadataUserTag.metadata_id) == target_id)
                    )
                ).all()
            )
            now = _utcnow()
            for source in sources:
                assert source.id is not None
                source_id = source.id
                await session.exec(
                    update(Comment).where(col(Comment.metadata_id) == source_id).values(metadata_id=target_id)
                )
                await session.exec(
                    update(MediaFile)
                    .where(col(MediaFile.metadata_id) == source_id)
                    .values(metadata_id=target_id, updated_at=now)
                )
                source_tag_ids = set(
                    (
                        await session.exec(
                            select(MetadataUserTag.user_tag_id).where(col(MetadataUserTag.metadata_id) == source_id)
                        )
                    ).all()
                )
                for tag_id in source_tag_ids - target_tag_ids:
                    session.add(MetadataUserTag(metadata_id=target_id, user_tag_id=tag_id))
                    target_tag_ids.add(tag_id)
                await session.exec(sqla_delete(MetadataUserTag).where(col(MetadataUserTag.metadata_id) == source_id))
                await cascade_delete_metadata(session, source)

            await session.commit()
            await session.refresh(target)
        await self._drop_embeddings(source_id_list)
        return target

    async def set_metadata_locks(self, metadata_id: int, fields: Collection[MetadataField]) -> Metadata | None:
        """整体替换锁定字段集合, 不修改 ``updated_at``. 不存在返回 None."""
        async with self._session() as session:
            metadata = await session.get(Metadata, metadata_id)
            if metadata is None:
                return None
            wanted = set(fields)
            metadata.locked_fields = [str(field) for field in MetadataField if field in wanted]
            session.add(metadata)
            await session.commit()
            await session.refresh(metadata)
            return metadata

    async def delete_metadata(self, metadata_id: int) -> bool:
        """级联见 ``cascade_delete_metadata``. 不存在返回 False."""
        async with self._session() as session:
            metadata = await session.get(Metadata, metadata_id)
            if metadata is None:
                return False
            await cascade_delete_metadata(session, metadata)
            await session.commit()
        await self._drop_embeddings([metadata_id])
        return True

    async def batch_delete_metadata(self, ids: list[int]) -> tuple[int, int]:
        """级联与 :meth:`delete_metadata` 一致, 单个事务. 不存在的 id 计入 missing."""
        removed: list[int] = []
        async with self._session() as session:
            missing = 0
            for metadata_id in unique_ids(ids):
                metadata = await session.get(Metadata, metadata_id)
                if metadata is None:
                    missing += 1
                    continue
                await cascade_delete_metadata(session, metadata)
                removed.append(metadata_id)
            await session.commit()
        await self._drop_embeddings(removed)
        return len(removed), missing

    async def _drop_embeddings(self, metadata_ids: list[int]) -> None:
        """主库提交之后清除 sidecar: 跨库没有事务, 先删除向量会在主库回滚时丢弃仍有效的数据.
        清理失败只留下孤儿行: 检索侧回查主库时会将其过滤, 下次全量索引再删除, 因此仅记录日志."""
        if self._embeddings is None or not metadata_ids:
            return
        try:
            await self._embeddings.delete_metadata(metadata_ids)
        except Exception:
            logger.warning("embedding cleanup failed", metadata_ids=metadata_ids, exc_info=True)
