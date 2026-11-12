from collections.abc import Iterable, Sequence
from typing import NamedTuple, Unpack

from sqlalchemy import delete as sqla_delete
from sqlalchemy import func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.functions import count
from sqlmodel import col, select
from sqlmodel.sql.expression import SelectOfScalar

from ...parsing import ContentType, FilePhase, FilePhaseSummary, Mosaic, file_phase_from_path, summarize_file_phases
from ...utils.collections import unique_ids
from ...utils.path import nfc_path
from ..models import MediaFile, MediaFileStatus, MediaSortField, Metadata, ScrapedFilename, SortOrder
from ..repo_types import _MEDIA_SORT_COLUMNS, MediaFileUpdates, _apply_media_phase_filters, _order_clause, _utcnow
from .base import RepositoryMixinBase, chunks


def _apply_path_phase(media: MediaFile) -> None:
    """path 是真值, 相位列是投影; 创建与改 path 时必须回填.

    只回填 path 能推出的相位. 同目录字幕不是 path 的投影, 由调用方另经
    ``handlers._common.refresh_external_subtitle`` 写入.
    """
    phase = file_phase_from_path(media.path)
    # 文件名是 path 的投影, 按名查重靠它走索引; 与相位列一同回填.
    media.filename = media.path.replace("\\", "/").rsplit("/", 1)[-1]
    media.mosaic = phase["mosaic"]
    media.has_subtitle_in_name = phase["has_subtitle"]
    media.definition = phase["definition"]
    media.ltx = phase["ltx"]


def file_phase_of(media: MediaFile) -> FilePhase:
    """从已落库列组装, 不再解析 path. 中字取文件名标记与同目录字幕的并集."""
    return FilePhase(
        mosaic=media.mosaic,
        has_subtitle=media.has_subtitle,
        definition=media.definition,
        ltx=media.ltx,
    )


class MetadataFilesSummary(NamedTuple):
    file_count: int
    phase: FilePhaseSummary


def _filename_stem(filename: str) -> str:
    """取文件主名, 去掉最后一段扩展名.

    「同一个文件」的判断不该受容器格式影响: 同一份片子转成 .mp4 / .strm 时,
    主名一模一样, 用户当然不希望被当成新文件再刮一遍.
    """
    name = filename.strip()
    if not name:
        return ""
    dot = name.rfind(".")
    # 没有点, 或点在开头 (隐藏文件) 时整段当主名.
    return name[:dot] if dot > 0 else name


class MediaRepoMixin(RepositoryMixinBase):
    async def create_media_file(self, library_id: int, **updates: Unpack[MediaFileUpdates]) -> MediaFile:
        path = updates.get("path")
        if path is not None:
            updates["path"] = nfc_path(path)
        async with self._session() as session:
            media = MediaFile(library_id=library_id, **updates)
            _apply_path_phase(media)
            session.add(media)
            await session.commit()
            await session.refresh(media)
            return media

    async def get_media_file(self, media_id: int) -> MediaFile | None:
        async with self._session() as session:
            return await session.get(MediaFile, media_id)

    async def get_media_file_by_path(self, path: str) -> MediaFile | None:
        async with self._session() as session:
            stmt = select(MediaFile).where(MediaFile.path == nfc_path(path))
            result = await session.exec(stmt)
            return result.first()

    async def has_scraped_with_filename(self, library_id: int, filename: str) -> bool:
        """同库内这份文件名是否已经刮过 (不比扩展名).

        查的是 ``scraped_filenames`` 登记表, 不是 ``media_files``: 后者会随整理与清理
        增删, 靠它判断会在文件挪动两轮之后失效, 于是同一份文件被重刮.
        """
        stem = _filename_stem(filename)
        if not stem:
            return False
        async with self._session() as session:
            stmt = (
                select(col(ScrapedFilename.id))
                .where(ScrapedFilename.library_id == library_id)
                .where(ScrapedFilename.filename == stem)
                .limit(1)
            )
            return (await session.exec(stmt)).first() is not None

    async def remember_scraped_filename(self, library_id: int, filename: str, *, path: str = "") -> None:
        """登记「这个名字刮过了」; 已存在则忽略. 只记主名, 不含扩展名."""
        stem = _filename_stem(filename)
        if not stem:
            return
        async with self._session() as session:
            existing = (
                await session.exec(
                    select(ScrapedFilename)
                    .where(ScrapedFilename.library_id == library_id)
                    .where(ScrapedFilename.filename == stem)
                    .limit(1)
                )
            ).first()
            if existing is not None:
                if path and not existing.source_path:
                    existing.source_path = path
                    session.add(existing)
                    await session.commit()
                return
            session.add(ScrapedFilename(library_id=library_id, filename=stem, source_path=path))
            await session.commit()

    async def forget_scraped_filename(self, filename: str, *, library_id: int | None = None) -> int:
        """删掉单条登记; 返回删掉的条数.

        ``library_id`` 缺省时按文件名跨库删: 管理界面只列名字, 不带库归属.
        """
        stem = _filename_stem(filename)
        if not stem:
            return 0
        async with self._session() as session:
            stmt = sqla_delete(ScrapedFilename).where(ScrapedFilename.filename == stem)
            if library_id is not None:
                stmt = stmt.where(ScrapedFilename.library_id == library_id)
            result = await session.exec(stmt)
            await session.commit()
            return result.rowcount or 0

    async def forget_scraped_filenames(self, *, library_id: int | None = None) -> int:
        """清掉登记, 让同名文件重新可刮. 返回清掉的条数."""
        async with self._session() as session:
            stmt = sqla_delete(ScrapedFilename)
            if library_id is not None:
                stmt = stmt.where(col(ScrapedFilename.library_id) == library_id)
            result = await session.exec(stmt)
            await session.commit()
            return result.rowcount or 0

    async def list_scraped_filenames(
        self, *, library_id: int | None = None, limit: int = 200
    ) -> list[tuple[str, str, str, str]]:
        """看登记了哪些名字; 每项是 (原始主名, 整理后名字, 源路径, 目标路径).

        后两者为空表示还没整理 (或整理时冲突跳过).
        """
        async with self._session() as session:
            stmt = select(
                ScrapedFilename.filename,
                ScrapedFilename.renamed,
                ScrapedFilename.source_path,
                ScrapedFilename.target_path,
            ).order_by(col(ScrapedFilename.created_at).desc())
            if library_id is not None:
                stmt = stmt.where(col(ScrapedFilename.library_id) == library_id)
            stmt = stmt.limit(limit)
            return [
                (str(name), str(renamed), str(source), str(target))
                for name, renamed, source, target in (await session.exec(stmt)).all()
            ]

    async def remember_scraped_renamed(
        self,
        library_id: int,
        filename: str,
        renamed: str,
        *,
        target_path: str = "",
        source_path: str = "",
    ) -> None:
        """记下整理后的文件名与目标路径; 登记行不存在时补一条 (改名先于登记的老数据)."""
        stem = _filename_stem(filename)
        if not stem:
            return
        async with self._session() as session:
            row = (await session.exec(select(ScrapedFilename).where(
                ScrapedFilename.library_id == library_id,
                ScrapedFilename.filename == stem,
            ).limit(1))).first()
            if row is None:
                session.add(
                    ScrapedFilename(
                        library_id=library_id,
                        filename=stem,
                        renamed=renamed,
                        target_path=target_path,
                        source_path=source_path,
                    )
                )
            else:
                changed = False
                if row.renamed != renamed:
                    row.renamed = renamed
                    changed = True
                if row.target_path != target_path:
                    row.target_path = target_path
                    changed = True
                # 存量行可能只有 renamed 没有 source: 补上, 否则界面第一行是空的.
                if source_path and row.source_path != source_path:
                    row.source_path = source_path
                    changed = True
                if not changed:
                    return
                session.add(row)
            await session.commit()

    async def get_valid(self, disk_paths: Iterable[str]) -> Sequence[MediaFile]:
        """路径按 ``chunks`` 分批 IN, 避开绑定变量上限. 比较前统一 NFC."""
        paths = [nfc_path(p) for p in disk_paths]
        if not paths:
            return []
        found: list[MediaFile] = []
        async with self._session() as session:
            for chunk in chunks(paths):
                stmt = select(MediaFile).where(col(MediaFile.path).in_(chunk))
                result = await session.exec(stmt)
                found.extend(result.all())
        return found

    async def get_invalid(self, disk_paths: Iterable[str], library_id: int | None = None) -> Sequence[MediaFile]:
        """library_id 须收窄到单库, 否则扫描 A 会误删 B.
        在 Python 做集合差, 禁止 SQL NOT IN: 分批 NOT IN 会把其它批里真实存在的路径误判为失效.
        """
        paths = [nfc_path(p) for p in disk_paths]
        if library_id is None and not paths:
            return []
        files = await self.list_media_files(library_id=library_id, limit=None)
        disk = frozenset(paths)
        if not disk:
            return files
        return [f for f in files if nfc_path(f.path) not in disk]

    async def list_media_files(
        self,
        status: Iterable[MediaFileStatus] | None = None,
        limit: int | None = 50,
        offset: int = 0,
        search: str | None = None,
        library_id: int | None = None,
        sort_by: MediaSortField = MediaSortField.UPDATED_AT,
        order: SortOrder = SortOrder.DESC,
        metadata_ids: Sequence[int] | None = None,
        ids: Sequence[int] | None = None,
        *,
        has_subtitle: bool | None = None,
        mosaic: Mosaic | None = None,
        uncensored: bool | None = None,
        definition: str | None = None,
        content_type: ContentType | None = None,
    ) -> list[MediaFile]:
        """limit None 不分页, ids 分批 IN; 分页时不分批 (offset 与 limit 会按批重新计算). ids 为空列表时直接返回空."""
        id_list = list(ids) if ids is not None else None
        if id_list is not None and not id_list:
            return []
        async with self._session() as session:

            def apply_filters(stmt: SelectOfScalar[MediaFile], chunk: list[int] | None) -> SelectOfScalar[MediaFile]:
                if status is not None:
                    stmt = stmt.where(col(MediaFile.status).in_(status))
                if library_id is not None:
                    stmt = stmt.where(col(MediaFile.library_id) == library_id)
                if chunk is not None:
                    stmt = stmt.where(col(MediaFile.id).in_(chunk))
                if metadata_ids is not None:
                    stmt = stmt.where(col(MediaFile.metadata_id).in_(metadata_ids))
                if search:
                    pattern = f"%{search}%"
                    stmt = stmt.where(or_(col(MediaFile.path).ilike(pattern), col(MediaFile.number).ilike(pattern)))
                return _apply_media_phase_filters(
                    stmt,
                    has_subtitle=has_subtitle,
                    mosaic=mosaic,
                    content_type=content_type,
                    uncensored=uncensored,
                    definition=definition,
                )

            if id_list is not None and limit is None:
                found: list[MediaFile] = []
                for chunk in chunks(id_list):
                    stmt = apply_filters(select(MediaFile), chunk)
                    result = await session.exec(stmt)
                    found.extend(result.all())
                return found

            stmt = apply_filters(select(MediaFile), id_list)
            stmt = (
                stmt.order_by(_order_clause(_MEDIA_SORT_COLUMNS[sort_by], order), col(MediaFile.id).asc())
                .offset(offset)
                .limit(limit)
            )
            result = await session.exec(stmt)
            return list(result.all())

    async def count_media_files(
        self,
        status: Iterable[MediaFileStatus] | None = None,
        search: str | None = None,
        library_id: int | None = None,
        *,
        has_subtitle: bool | None = None,
        mosaic: Mosaic | None = None,
        uncensored: bool | None = None,
        definition: str | None = None,
        content_type: ContentType | None = None,
    ) -> int:
        async with self._session() as session:
            base = select(MediaFile)
            if status is not None:
                base = base.where(col(MediaFile.status).in_(status))
            if library_id is not None:
                base = base.where(col(MediaFile.library_id) == library_id)
            if search:
                pattern = f"%{search}%"
                base = base.where(or_(col(MediaFile.path).ilike(pattern), col(MediaFile.number).ilike(pattern)))
            base = _apply_media_phase_filters(
                base,
                has_subtitle=has_subtitle,
                mosaic=mosaic,
                uncensored=uncensored,
                definition=definition,
                content_type=content_type,
            )
            stmt = select(count()).select_from(base.subquery())
            result = await session.exec(stmt)
            return result.one() or 0

    async def update_media_file_in_session(
        self, session: AsyncSession, media_id: int, **updates: Unpack[MediaFileUpdates]
    ) -> MediaFile | None:
        """在调用方事务中更新 MediaFile; 不提交、不刷新 Session."""
        media = await session.get(MediaFile, media_id)
        if media is None:
            return None
        # 显式赋值, 禁止 setattr; 字段集由 MediaFileUpdates 与 MediaFile 静态对齐.
        if "path" in updates:
            media.path = nfc_path(updates["path"])
            _apply_path_phase(media)
        if "number" in updates:
            media.number = updates["number"]
        if "oshash" in updates:
            media.oshash = updates["oshash"]
        if "size" in updates:
            media.size = updates["size"]
        if "duration" in updates:
            media.duration = updates["duration"]
        if "codec" in updates:
            media.codec = updates["codec"]
        if "status" in updates:
            media.status = updates["status"]
        if "metadata_id" in updates:
            media.metadata_id = updates["metadata_id"]
        if "has_external_subtitle" in updates:
            media.has_external_subtitle = updates["has_external_subtitle"]
        media.updated_at = _utcnow()
        session.add(media)
        await session.flush()
        return media

    async def update_media_file(self, media_id: int, **updates: Unpack[MediaFileUpdates]) -> MediaFile | None:
        async with self._session() as session:
            media = await self.update_media_file_in_session(session, media_id, **updates)
            if media is None:
                return None
            await session.commit()
            await session.refresh(media)
            return media

    async def delete_media_file(self, media_id: int) -> bool:
        async with self._session() as session:
            media = await session.get(MediaFile, media_id)
            if media is None:
                return False
            await session.delete(media)
            await session.commit()
            return True

    async def batch_delete_media_files(self, media_ids: Sequence[int]) -> tuple[int, int]:
        """按 id 批量删除记录, 返回 ``(删除数, 不存在的 id 数)``; 整个批次只提交一次."""
        ids = unique_ids(media_ids)
        if not ids:
            return 0, 0
        deleted = 0
        async with self._session() as session:
            for chunk in chunks(ids):
                result = await session.exec(sqla_delete(MediaFile).where(col(MediaFile.id).in_(chunk)))
                deleted += result.rowcount or 0
            await session.commit()
        return deleted, len(ids) - deleted

    async def count_media_by_metadata_ids(self, metadata_ids: Sequence[int]) -> dict[int, int]:
        """无关联的 id 不出现在结果中."""
        ids = [i for i in metadata_ids if i]
        if not ids:
            return {}
        async with self._session() as session:
            stmt = (
                select(col(MediaFile.metadata_id), func.count())
                .where(col(MediaFile.metadata_id).in_(ids))
                .group_by(col(MediaFile.metadata_id))
            )
            result = await session.exec(stmt)
            return {mid: n for mid, n in result.all() if mid is not None}

    async def summarize_media_by_metadata(self, metadata: Sequence[Metadata]) -> dict[int, MetadataFilesSummary]:
        """每个入参条目都有结果: 相位由条目类型与挂载文件合成, 没有文件的条目 file_count 为 0."""
        entry_types = {m.id: m.content_type for m in metadata if m.id is not None}
        if not entry_types:
            return {}
        async with self._session() as session:
            stmt = select(MediaFile).where(col(MediaFile.metadata_id).in_(list(entry_types)))
            result = await session.exec(stmt)
            grouped: dict[int, list[FilePhase]] = {}
            for media in result.all():
                if media.metadata_id is None:
                    continue
                grouped.setdefault(media.metadata_id, []).append(file_phase_of(media))
        return {
            mid: MetadataFilesSummary(
                file_count=len(grouped.get(mid, [])),
                phase=summarize_file_phases(grouped.get(mid, []), content_type),
            )
            for mid, content_type in entry_types.items()
        }

    async def get_media_by_metadata_id(self, metadata_id: int) -> list[MediaFile]:
        """按入库顺序 (id 升序) 返回条目的文件.

        顺序是契约的一部分: 组装给播放源的快照按这个顺序排列, 来源据此决定列表顺序与默认选中的
        那一条. 不排序时得到的是 SQLite 的扫描顺序, 同一个条目可能换一条默认流.
        """
        async with self._session() as session:
            stmt = select(MediaFile).where(MediaFile.metadata_id == metadata_id).order_by(col(MediaFile.id))
            result = await session.exec(stmt)
            return list(result.all())
