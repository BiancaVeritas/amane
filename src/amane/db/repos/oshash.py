from sqlmodel import col, select

from ..models import OshashSubmission
from .base import RepositoryMixinBase


class OshashRepoMixin(RepositoryMixinBase):
    async def enqueue_oshash_submission(
        self, *, theporndb_uuid: str, oshash: str, duration: int, title: str | None, path: str
    ) -> None:
        async with self._session() as session:
            existing = await session.exec(
                select(OshashSubmission).where(
                    col(OshashSubmission.theporndb_uuid) == theporndb_uuid,
                    col(OshashSubmission.oshash) == oshash,
                )
            )
            if existing.first() is None:
                session.add(
                    OshashSubmission(
                        theporndb_uuid=theporndb_uuid, oshash=oshash, duration=duration, title=title, path=path
                    )
                )
                await session.commit()

    async def list_oshash_submissions(self, *, offset: int, limit: int) -> tuple[list[OshashSubmission], int]:
        from sqlalchemy import func

        async with self._session() as session:
            total = int((await session.exec(select(func.count()).select_from(OshashSubmission))).one())
            rows = await session.exec(
                select(OshashSubmission)
                .order_by(col(OshashSubmission.created_at), col(OshashSubmission.id))
                .offset(offset)
                .limit(limit)
            )
            return list(rows.all()), total

    async def get_oshash_submission(self, submission_id: int) -> OshashSubmission | None:
        async with self._session() as session:
            return await session.get(OshashSubmission, submission_id)

    async def finish_oshash_submission(self, submission_id: int, *, error: str | None) -> None:
        async with self._session() as session:
            row = await session.get(OshashSubmission, submission_id)
            if row is None:
                return
            if error is None:
                await session.delete(row)
            else:
                row.last_error = error[:2000]
                session.add(row)
            await session.commit()

    async def delete_oshash_submissions(self, submission_ids: list[int]) -> int:
        """Delete only the explicitly selected pending submission rows."""
        ids = list(dict.fromkeys(submission_ids))
        if not ids:
            return 0
        async with self._session() as session:
            rows = await session.exec(select(OshashSubmission).where(col(OshashSubmission.id).in_(ids)))
            found = list(rows.all())
            for row in found:
                await session.delete(row)
            await session.commit()
            return len(found)
