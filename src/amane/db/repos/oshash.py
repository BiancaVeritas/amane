from sqlmodel import col, delete, select, update

from ..models import OshashSubmission, OshashSubmissionStatus
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
                        theporndb_uuid=theporndb_uuid,
                        oshash=oshash,
                        duration=duration,
                        title=title,
                        path=path,
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

    async def claim_oshash_submission(self, submission_id: int) -> bool:
        """Atomically claim a pending row for manual submission."""
        async with self._session() as session:
            result = await session.exec(
                update(OshashSubmission)
                .where(
                    col(OshashSubmission.id) == submission_id,
                    col(OshashSubmission.status) == OshashSubmissionStatus.PENDING,
                )
                .values(
                    status=OshashSubmissionStatus.SUBMITTING,
                    last_error=None,
                )
            )
            await session.commit()
            return result.rowcount == 1

    async def finish_oshash_submission(
        self,
        submission_id: int,
        *,
        error: str | None,
        uncertain: bool = False,
    ) -> bool:
        """Finish only a row currently claimed by this submission attempt."""
        async with self._session() as session:
            if error is None:
                result = await session.exec(
                    delete(OshashSubmission).where(
                        col(OshashSubmission.id) == submission_id,
                        col(OshashSubmission.status) == OshashSubmissionStatus.SUBMITTING,
                    )
                )
            else:
                result = await session.exec(
                    update(OshashSubmission)
                    .where(
                        col(OshashSubmission.id) == submission_id,
                        col(OshashSubmission.status) == OshashSubmissionStatus.SUBMITTING,
                    )
                    .values(
                        status=(OshashSubmissionStatus.UNCERTAIN if uncertain else OshashSubmissionStatus.PENDING),
                        last_error=error[:2000],
                    )
                )
            await session.commit()
            return result.rowcount == 1

    async def recover_oshash_submissions(self) -> int:
        """Mark interrupted submissions uncertain after an application restart."""
        async with self._session() as session:
            result = await session.exec(
                update(OshashSubmission)
                .where(col(OshashSubmission.status) == OshashSubmissionStatus.SUBMITTING)
                .values(
                    status=OshashSubmissionStatus.UNCERTAIN,
                    last_error="应用程序重启时提交请求尚未完成，结果待确认",
                )
            )
            await session.commit()
            return result.rowcount

    async def confirm_oshash_submission_accepted(self, submission_id: int) -> bool:
        """Remove an uncertain row after the user confirms it was accepted."""
        async with self._session() as session:
            result = await session.exec(
                delete(OshashSubmission).where(
                    col(OshashSubmission.id) == submission_id,
                    col(OshashSubmission.status) == OshashSubmissionStatus.UNCERTAIN,
                )
            )
            await session.commit()
            return result.rowcount == 1

    async def retry_uncertain_oshash_submission(self, submission_id: int) -> bool:
        """Return an uncertain row to pending after the user confirms it was not accepted."""
        async with self._session() as session:
            result = await session.exec(
                update(OshashSubmission)
                .where(
                    col(OshashSubmission.id) == submission_id,
                    col(OshashSubmission.status) == OshashSubmissionStatus.UNCERTAIN,
                )
                .values(
                    status=OshashSubmissionStatus.PENDING,
                    last_error=None,
                )
            )
            await session.commit()
            return result.rowcount == 1

    async def delete_oshash_submissions(self, submission_ids: list[int]) -> int:
        """Delete only explicitly selected pending submission rows."""
        ids = list(dict.fromkeys(submission_ids))
        if not ids:
            return 0

        async with self._session() as session:
            result = await session.exec(
                delete(OshashSubmission).where(
                    col(OshashSubmission.id).in_(ids),
                    col(OshashSubmission.status) == OshashSubmissionStatus.PENDING,
                )
            )
            await session.commit()
            return result.rowcount
