"""/media HTTP 接线: 状态码、JSON 形状、422. 列表筛选见 tests/db/test_repository.py."""

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from amane.db.models import MediaFileStatus

if TYPE_CHECKING:
    from httpx2 import AsyncClient

    from amane.db.repository import Repository


@pytest_asyncio.fixture(autouse=True)
async def _seed_library(repo: Repository) -> None:
    """FK 约束要求归属库存在; 这些测试以 library_id=1 创建 MediaFile."""
    if await repo.get_library(1) is None:
        await repo.create_library(name="default", path="/")


class TestMediaHttp:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_get_update_delete(self, client: AsyncClient, repo: Repository):
        empty = await client.get("media")
        assert empty.status_code == 200
        assert empty.json()["items"] == []
        assert empty.json()["total"] == 0
        assert (await client.get("media?definition=not-a-def")).status_code == 422

        media = await repo.create_media_file(library_id=1, path="/video/MIDV-001-C.mp4", number="XYZ-001")
        listed = await client.get("media?search=MIDV-001")
        assert listed.status_code == 200
        item = listed.json()["items"][0]
        assert item["has_subtitle"] is True
        assert item["mosaic"] is None

        got = await client.get(f"media/{media.id}")
        assert got.status_code == 200
        assert got.json()["number"] == "XYZ-001"
        assert (await client.get("media/9999")).status_code == 404

        patched = await client.patch(f"media/{media.id}", json={"number": "NEW-001", "status": "scraped"})
        assert patched.status_code == 200
        assert patched.json()["number"] == "NEW-001"
        assert patched.json()["status"] == "scraped"
        meta = await repo.upsert_metadata(number="ABC-123")
        moved = await client.patch(f"media/{media.id}", json={"path": "/new/location/x.mp4", "metadata_id": meta.id})
        assert moved.json()["path"] == "/new/location/x.mp4"
        assert moved.json()["metadata_id"] == meta.id
        extra = await client.patch(f"media/{media.id}", json={"number": "NEW", "unknown_field": "should_be_ignored"})
        assert extra.status_code == 200
        assert extra.json()["number"] == "NEW"
        assert (await client.patch("media/9999", json={"number": "X"})).status_code == 404
        assert (await client.patch(f"media/{media.id}", json={})).status_code == 422
        for bad in ({"status": "invalid_status"}, {"number": 12345}):
            assert (await client.patch(f"media/{media.id}", json=bad)).status_code == 422

        deleted = await client.delete(f"media/{media.id}")
        assert deleted.status_code == 204
        assert (await client.delete("media/9999")).status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_phase_derived_from_entry_type(self, client: AsyncClient, repo: Repository):
        """文件行的有码 / 无码按条目类型派生; 未绑定条目的文件只看自身标记."""
        censored = await repo.upsert_metadata(number="MIDV-123")
        uncensored = await repo.upsert_metadata(number="HEYZO-1234")
        assert censored.id is not None and uncensored.id is not None
        plain = await repo.create_media_file(library_id=1, path="/v/MIDV-123.mp4", metadata_id=censored.id)
        marked = await repo.create_media_file(library_id=1, path="/v/MIDV-124-无码.mp4", metadata_id=censored.id)
        inherited = await repo.create_media_file(library_id=1, path="/v/HEYZO-1234.mp4", metadata_id=uncensored.id)
        orphan = await repo.create_media_file(library_id=1, path="/v/PLAIN-1.mp4")
        assert plain.id and marked.id and inherited.id and orphan.id

        listed = await client.get("media?limit=50")
        rows = {row["id"]: row for row in listed.json()["items"]}
        assert (rows[plain.id]["censored"], rows[plain.id]["uncensored"]) == (True, False)
        # 带无码标记的副本: 性质是无码, 不再同时挂有码
        assert (rows[marked.id]["censored"], rows[marked.id]["uncensored"]) == (False, True)
        assert (rows[inherited.id]["censored"], rows[inherited.id]["uncensored"]) == (False, True)
        assert (rows[orphan.id]["censored"], rows[orphan.id]["uncensored"]) == (False, False)
        assert rows[marked.id]["mosaic"] == "uncensored"

        single = await client.get(f"media/{inherited.id}")
        assert (single.json()["censored"], single.json()["uncensored"]) == (False, True)

        # 按所属条目类型筛: 未绑定条目的文件不命中任何类型
        by_type = await client.get("media?content_type=uncensored")
        assert [row["id"] for row in by_type.json()["items"]] == [inherited.id]
        assert by_type.json()["total"] == 1
        assert (await client.get("media?content_type=bogus")).status_code == 422


class TestScrapedFilenameRegistry:
    """同名不重刮的登记表, 与 media_files 解耦."""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_survives_media_file_deletion(self, repo: Repository) -> None:
        """media_files 记录被删掉之后, 同名仍然不该重刮.

        这是本次的核心诉求: 旧实现查 media_files 的同名 SCRAPED 行, 而那张表会随
        整理与清理增删, 文件挪动两轮之后就又能刮了.
        """
        media = await repo.create_media_file(
            1, path="/lib/ABC-123.mp4", filename="ABC-123.mp4", status=MediaFileStatus.SCRAPED
        )
        await repo.remember_scraped_filename(1, "ABC-123.mp4")
        assert await repo.has_scraped_with_filename(1, "ABC-123.mp4") is True

        assert media.id is not None
        await repo.delete_media_file(media.id)
        # 媒体记录没了, 登记还在.
        assert await repo.has_scraped_with_filename(1, "ABC-123.mp4") is True

    @pytest.mark.asyncio(loop_scope="function")
    async def test_forget_clears_registry(self, repo: Repository) -> None:
        """清掉登记后同名可以重新刮."""
        await repo.remember_scraped_filename(1, "ABC-123.mp4")
        assert await repo.has_scraped_with_filename(1, "ABC-123.mp4") is True

        cleared = await repo.forget_scraped_filenames()
        assert cleared == 1
        assert await repo.has_scraped_with_filename(1, "ABC-123.mp4") is False


    @pytest.mark.asyncio(loop_scope="function")
    async def test_extension_is_ignored(self, repo: Repository) -> None:
        """同一份片子换个容器格式不该被当成新文件.

        判重只看主名: 用户把 .strm 转成 .mp4 时, 主名一模一样.
        """
        await repo.remember_scraped_filename(1, "ABC-123.mp4")
        assert await repo.has_scraped_with_filename(1, "ABC-123.strm") is True
        assert await repo.has_scraped_with_filename(1, "ABC-123.mp4") is True
        assert await repo.has_scraped_with_filename(1, "ABC-124.strm") is False


    @pytest.mark.asyncio(loop_scope="function")
    async def test_remove_single_record(self, repo: Repository) -> None:
        """单条删除只影响那一个名字, 其余记录保留."""
        await repo.remember_scraped_filename(1, "ABC-123.mp4")
        await repo.remember_scraped_filename(1, "ABC-124.mp4")

        removed = await repo.forget_scraped_filename("ABC-123.mp4")
        assert removed == 1
        assert await repo.has_scraped_with_filename(1, "ABC-123.mp4") is False
        assert await repo.has_scraped_with_filename(1, "ABC-124.mp4") is True

    @pytest.mark.asyncio(loop_scope="function")
    async def test_remove_missing_record_returns_zero(self, repo: Repository) -> None:
        """删不存在的记录不报错, 返回 0."""
        assert await repo.forget_scraped_filename("NOPE-999.mp4") == 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_registry_is_per_library(self, repo: Repository) -> None:
        """登记按库隔离: 另一个库里的同名文件不受影响."""
        other = await repo.create_library(name="other", path="/other")
        assert other.id is not None
        await repo.remember_scraped_filename(1, "ABC-123.mp4")
        assert await repo.has_scraped_with_filename(1, "ABC-123.mp4") is True
        assert await repo.has_scraped_with_filename(other.id, "ABC-123.mp4") is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_remember_is_idempotent(self, repo: Repository) -> None:
        """重复登记不报错也不重复计数."""
        await repo.remember_scraped_filename(1, "ABC-123.mp4")
        await repo.remember_scraped_filename(1, "ABC-123.mp4")
        # 登记的是主名 (不含扩展名); 未整理时 renamed 与两个路径都为空.
        assert await repo.list_scraped_filenames(library_id=1) == [("ABC-123", "", "", "")]
