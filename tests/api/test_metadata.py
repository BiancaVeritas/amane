"""/metadata HTTP 接线: 状态码、JSON 形状、422. 列表筛选/级联删除见 tests/db; 合并规则见 tests/aggregate."""

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from PIL import Image

from amane.db.models import MediaFileStatus, Metadata
from amane.enums import ActorGender
from amane.media.resource_store import RESOURCE_URL_PREFIX, ResourceStore

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI
    from httpx2 import AsyncClient

    from amane.db.repository import Repository


@pytest_asyncio.fixture(autouse=True)
async def _seed_library(repo: Repository) -> None:
    """FK 约束要求归属库存在; 这些测试以 library_id=1 创建 MediaFile."""
    if await repo.get_library(1) is None:
        await repo.create_library(name="default", path="/")


_FILTER_LIMIT_CASES = [
    ({"actor_id": list(range(101))}, 422, "actor_id"),
    ({"actor_id": list(range(100)), "tag_id": list(range(100)), "director_id": [1]}, 422, "合计"),
    ({"actor_id": list(range(100))}, 200, None),
]


class TestMetadataHttp:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_filter_id_limits(self, client: AsyncClient):
        """每组 ≤100、八组合计 ≤200; 超限 422 并指出是哪一项."""
        for params, status, detail_part in _FILTER_LIMIT_CASES:
            response = await client.get("metadata", params=params)
            assert response.status_code == status, (params, response.json())
            if detail_part is not None:
                assert detail_part in response.json()["detail"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_item_omits_raw_and_field_sources(self, client: AsyncClient, repo: Repository):
        """列表项是精简模型, raw / field_sources 只在详情返回; 聚合字段仍在."""
        meta = await repo.upsert_metadata(
            number="SLIM-001",
            title="Slim",
            raw={"javdb": {"title": "Slim"}},
            field_sources={"title": "javdb"},
        )
        assert meta.id is not None

        listed = (await client.get("metadata?search=SLIM-001")).json()["items"][0]
        assert "raw" not in listed
        assert "field_sources" not in listed
        assert listed["file_count"] == 0
        assert "file_phase" in listed
        assert listed["locked_fields"] == []

        detail = (await client.get(f"metadata/{meta.id}")).json()["metadata"]
        assert detail["raw"] == {"javdb": {"title": "Slim"}}
        assert detail["field_sources"] == {"title": "javdb"}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_phase_carries_tags_for_tag_driven_uncensored(self, client: AsyncClient, repo: Repository):
        """相位里要带条目标签, 前端才能按「無碼」点亮无码.

        FC2 这类条目的类型本身不带无码标记, 只能靠标签区分; 少了 tags 就永远不亮.
        """
        meta = await repo.upsert_metadata(number="FC2-1229755", tags=["無碼", "素人"])
        assert meta.id is not None

        listed = (await client.get("metadata?search=FC2-1229755")).json()["items"][0]
        assert "無碼" in listed["file_phase"]["tags"]

        detail = (await client.get(f"metadata/{meta.id}")).json()["metadata"]
        assert "無碼" in detail["file_phase"]["tags"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_phase_comes_from_number_without_files(self, client: AsyncClient, repo: Repository):
        """有码 / 无码取番号类别: 没有挂载文件也亮, 无码再并上文件的无码标记."""
        uncensored = await repo.upsert_metadata(number="HEYZO-1234")
        marked = await repo.upsert_metadata(number="MIDV-002")
        assert uncensored.id is not None and marked.id is not None
        media = await repo.create_media_file(library_id=1, path="/video/MIDV-002-无码.mp4")
        assert media.id is not None
        await repo.update_media_file(media.id, metadata_id=marked.id)

        listed = await client.get("metadata?sort_by=number&order=asc")
        items = {item["number"]: item for item in listed.json()["items"]}
        assert items["HEYZO-1234"]["file_count"] == 0
        assert items["HEYZO-1234"]["file_phase"] == {
            "has_subtitle": False,
            "censored": False,
            "uncensored": True,
            "mosaics": [],
            "definition": None,
            "ltx": False,
            "tags": [],
        }
        assert items["MIDV-002"]["file_phase"]["censored"] is True
        assert items["MIDV-002"]["file_phase"]["uncensored"] is True
        assert items["MIDV-002"]["file_phase"]["mosaics"] == ["uncensored"]

        detail = await client.get(f"metadata/{uncensored.id}")
        assert detail.json()["metadata"]["file_phase"]["uncensored"] is True
        assert detail.json()["files"] == []

        uncensored_filtered = await client.get("metadata?uncensored=true")
        assert {item["number"] for item in uncensored_filtered.json()["items"]} == {"HEYZO-1234", "MIDV-002"}
        by_type = await client.get("metadata?content_type=uncensored")
        assert [item["number"] for item in by_type.json()["items"]] == ["HEYZO-1234"]
        by_mosaic = await client.get("metadata?mosaic=uncensored")
        assert [item["number"] for item in by_mosaic.json()["items"]] == ["MIDV-002"]
        assert (await client.get("metadata?mosaic=censored")).status_code == 422

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_detail_update_delete(self, client: AsyncClient, repo: Repository):
        empty = await client.get("metadata")
        assert empty.status_code == 200
        assert empty.json()["items"] == []
        assert empty.json()["total"] == 0
        assert (await client.get("metadata?definition=bogus")).status_code == 422

        meta = await repo.upsert_metadata(
            number="ABC-001",
            title="Test",
            actors=["Mei", "MaleA"],
            actor_genders={"Mei": ActorGender.FEMALE},
        )
        assert meta.id is not None
        media = await repo.create_media_file(library_id=1, path="/video/MIDV-001-C.mp4", number="ABC-001")
        assert media.id is not None
        await repo.update_media_file(media.id, status=MediaFileStatus.SCRAPED, metadata_id=meta.id)

        listed = await client.get("metadata?search=ABC")
        assert listed.status_code == 200
        item = listed.json()["items"][0]
        assert item["file_count"] == 1
        assert item["file_phase"]["has_subtitle"] is True

        detail = await client.get(f"metadata/{meta.id}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["metadata"]["title"] == "Test"
        assert len(body["files"]) == 1
        assert body["actor_genders"] == {"Mei": "female", "MaleA": "unknown"}
        assert set(body["actor_ids"]) == {"Mei", "MaleA"}
        assert (await client.get("metadata/9999")).status_code == 404

        ok = await client.patch(f"metadata/{meta.id}", json={"release": "2020-01-15T00:00:00Z"})
        assert ok.status_code == 200
        assert ok.json()["release"] == "2020-01-15"
        assert (await client.patch(f"metadata/{meta.id}", json={"release": "not-a-date"})).status_code == 422

        urls = await client.patch(
            f"metadata/{meta.id}",
            json={
                "poster_urls": ["https://a/p.jpg", "https://b/p.jpg"],
                "scores": {"javdb": 4.5},
                "external_ids": {"javdb": "abc"},
            },
        )
        assert urls.status_code == 200
        data = urls.json()
        assert data["poster_urls"] == ["https://a/p.jpg", "https://b/p.jpg"]
        assert data["poster_url"] == "https://a/p.jpg"
        assert data["score"] == 4.5
        assert (await client.patch("metadata/9999", json={"title": "X"})).status_code == 404
        assert (await client.patch(f"metadata/{meta.id}", json={})).status_code == 422
        extra = await client.patch(f"metadata/{meta.id}", json={"title": "New", "unknown_field": "ignored"})
        assert extra.status_code == 200
        assert extra.json()["title"] == "New"
        for bad_payload in ({"runtime": "not_an_int"}, {"actors": "not_a_list"}):
            assert (await client.patch(f"metadata/{meta.id}", json=bad_payload)).status_code == 422

        deleted = await client.delete(f"metadata/{meta.id}")
        assert deleted.status_code == 204
        assert (await client.delete("metadata/9999")).status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_western_external_id_editor_roundtrips_theporndb_uuid(self, client: AsyncClient, repo: Repository):
        metadata = await repo.upsert_western_metadata(
            theporndb_uuid="5ad1967c-5cef-4020-8256-50227acf985b",
            number="LANCELOT-001",
            external_ids={"javdb": "javdb-123"},
        )
        assert metadata.id is not None

        detail = await client.get(f"metadata/{metadata.id}")
        assert detail.status_code == 200
        assert detail.json()["metadata"]["theporndb_uuid"] == "5ad1967c-5cef-4020-8256-50227acf985b"

        updated = await client.patch(
            f"metadata/{metadata.id}",
            json={
                "external_ids": {
                    "theporndb": "5ad1967c-5cef-4020-8256-50227acf985b",
                    "javdb": "javdb-456",
                }
            },
        )
        assert updated.status_code == 200
        assert updated.json()["theporndb_uuid"] == "5ad1967c-5cef-4020-8256-50227acf985b"
        assert updated.json()["external_ids"] == {"javdb": "javdb-456"}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_detail_file_rows_carry_derived_phase(self, client: AsyncClient, repo: Repository):
        """详情里的文件行与条目相位同源: 有码条目的文件行带 censored, 带无码标记的再带 uncensored."""
        meta = await repo.upsert_metadata(number="MIDV-123")
        assert meta.id is not None
        plain = await repo.create_media_file(library_id=1, path="/video/MIDV-123.mp4", metadata_id=meta.id)
        marked = await repo.create_media_file(library_id=1, path="/video/MIDV-124-无码.mp4", metadata_id=meta.id)
        assert plain.id is not None and marked.id is not None

        detail = await client.get(f"metadata/{meta.id}")
        rows = {row["id"]: row for row in detail.json()["files"]}
        assert (rows[plain.id]["censored"], rows[plain.id]["uncensored"]) == (True, False)
        assert (rows[marked.id]["censored"], rows[marked.id]["uncensored"]) == (False, True)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_sort_by_score(self, client: AsyncClient, repo: Repository):
        """HTTP 接线: 接受 score 排序, 响应 score 是 scores 首值而非最大值; 未知排序字段 422."""
        await repo.upsert_metadata(number="S-1", title="One", scores={"a": 1.0, "b": 4.5})

        listed = await client.get("metadata?sort_by=score&order=desc")
        assert listed.status_code == 200
        assert [i["score"] for i in listed.json()["items"]] == [1.0]
        assert (await client.get("metadata?sort_by=bogus")).status_code == 422

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_vr_filter_and_field(self, client: AsyncClient, repo: Repository):
        """HTTP 接线: vr 三态筛选与响应字段同源; 非法取值 422, 且不成为可写字段."""
        await repo.upsert_metadata(number="VR-001", tags=["VR"])
        await repo.upsert_metadata(number="MIDV-123", tags=["単体作品"])

        listed = await client.get("metadata?vr=true&sort_by=number&order=asc")
        assert listed.status_code == 200
        assert [(i["number"], i["vr"]) for i in listed.json()["items"]] == [("VR-001", True)]
        only_2d = await client.get("metadata?vr=false&sort_by=number&order=asc")
        assert [(i["number"], i["vr"]) for i in only_2d.json()["items"]] == [("MIDV-123", False)]
        both = await client.get("metadata?sort_by=number&order=asc")
        assert [i["vr"] for i in both.json()["items"]] == [False, True]

        assert (await client.get("metadata?vr=bogus")).status_code == 422
        assert "vr" not in (await client.get("metadata/schema")).json()["properties"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_schema_includes_editable_fields(self, client: AsyncClient):
        resp = await client.get("metadata/schema")
        assert resp.status_code == 200
        props = resp.json()["properties"]
        for key in ("title", "actors", "poster_urls", "scores", "external_ids", "source_urls"):
            assert key in props
        # 锁列由 PUT /locks 管理, 不经 PATCH 写入.
        assert "locked_fields" not in props

    @pytest.mark.asyncio(loop_scope="function")
    async def test_merge_http(self, client: AsyncClient, repo: Repository):
        meta = await repo.upsert_metadata(
            number="ABC-001",
            title="javdb title",
            field_sources={"title": "javdb"},
            raw={"javdb": {"title": "javdb title"}, "dmm": {"title": "dmm title"}},
        )
        assert meta.id is not None
        resp = await client.post(f"metadata/{meta.id}/merge", json={"selections": {"title": "dmm"}})
        assert resp.status_code == 200
        assert resp.json()["title"] == "dmm title"
        assert resp.json()["field_sources"]["title"] == "dmm"
        assert resp.json()["locked_fields"] == ["title", "original_title"]

        none_meta = await repo.upsert_metadata(
            number="ABC-004", field_sources={"title": "javdb"}, raw={"javdb": {"title": None}}
        )
        assert none_meta.id is not None
        assert (
            await client.post(f"metadata/{none_meta.id}/merge", json={"selections": {"title": "javdb"}})
        ).status_code == 400
        assert (await client.post(f"metadata/{meta.id}/merge", json={"selections": {}})).status_code == 400
        assert (await client.post("metadata/9999/merge", json={"selections": {"title": "javdb"}})).status_code == 404
        bad = await client.post(f"metadata/{meta.id}/merge", json={"selections": {"title": "nonexistent"}})
        assert bad.status_code == 422
        assert "nonexistent" in bad.json()["detail"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_crop_poster_http(self, client: AsyncClient, repo: Repository, app: FastAPI):
        meta = await repo.upsert_metadata(
            number="CROP-001", thumb_urls=["https://example.com/t.jpg"], poster_urls=["https://example.com/old-p.jpg"]
        )
        assert meta.id is not None

        async def fake_download(url: str, dest: Path, **kwargs: object) -> bool:
            dest.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (800, 538), "blue").save(dest)
            return True

        async def fake_resolve(url: str, **kwargs: object) -> str:
            return url

        app.state.runtime.web_client.download = fake_download  # type: ignore[method-assign]
        app.state.runtime.web_client.resolve_final_url = fake_resolve  # type: ignore[method-assign]

        resp = await client.post(
            f"metadata/{meta.id}/crop-poster", json={"left": 421, "top": 0, "right": 800, "bottom": 538}
        )
        assert resp.status_code == 200
        data = resp.json()
        # 新裁的海报插到首位, 原有地址保留在后面 (不删数据, 用户可切回).
        assert len(data["poster_urls"]) == 2
        assert data["poster_urls"][0].startswith("/api/resources/")
        assert data["poster_urls"][1] == "https://example.com/old-p.jpg"
        assert data["thumb_urls"] == ["https://example.com/t.jpg"]
        # 裁切写入 poster_urls 并自动锁定.
        assert data["locked_fields"] == ["poster_urls"]

        # 顺时针 90° 后宽高互换: 同一份封面换角度得到另一份派生结果
        rotated = await client.post(
            f"metadata/{meta.id}/crop-poster",
            json={"left": 0, "top": 0, "right": 538, "bottom": 538, "rotation": 90},
        )
        assert rotated.status_code == 200
        assert rotated.json()["poster_urls"] != data["poster_urls"]
        # 按未旋转尺寸的框在旋转后越界
        assert (
            await client.post(
                f"metadata/{meta.id}/crop-poster",
                json={"left": 0, "top": 0, "right": 800, "bottom": 538, "rotation": 90},
            )
        ).status_code == 400
        assert (
            await client.post(
                f"metadata/{meta.id}/crop-poster",
                json={"left": 0, "top": 0, "right": 538, "bottom": 538, "rotation": 45},
            )
        ).status_code == 422

        no_thumb = await repo.upsert_metadata(number="CROP-003", poster_urls=["https://example.com/p.jpg"])
        assert no_thumb.id is not None
        missing_thumb = await client.post(
            f"metadata/{no_thumb.id}/crop-poster", json={"left": 0, "top": 0, "right": 10, "bottom": 10}
        )
        assert missing_thumb.status_code == 400
        assert "封面" in missing_thumb.json()["detail"]

        inverted = await client.post(
            f"metadata/{meta.id}/crop-poster", json={"left": 100, "top": 0, "right": 50, "bottom": 100}
        )
        assert inverted.status_code == 422
        assert (
            await client.post("metadata/99999/crop-poster", json={"left": 0, "top": 0, "right": 10, "bottom": 10})
        ).status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_locks_http(self, client: AsyncClient, repo: Repository):
        meta = await repo.upsert_metadata(number="LOCK-101", title="X")
        assert meta.id is not None

        locked = await client.put(f"metadata/{meta.id}/locks", json={"fields": ["title", "poster_urls"]})
        assert locked.status_code == 200
        assert set(locked.json()["locked_fields"]) == {"title", "poster_urls"}

        # 重复值按集合去重, 顺序稳定.
        dup = await client.put(f"metadata/{meta.id}/locks", json={"fields": ["poster_urls", "title", "title"]})
        assert dup.status_code == 200
        assert dup.json()["locked_fields"] == ["title", "poster_urls"]

        # 手动 PATCH 无视锁, 并把写入字段并入锁.
        patched = await client.patch(f"metadata/{meta.id}", json={"title": "Manual"})
        assert patched.status_code == 200
        assert patched.json()["title"] == "Manual"
        assert set(patched.json()["locked_fields"]) == {"title", "poster_urls"}

        # 自动刮削写入跳过锁定字段, 未锁定字段正常更新.
        updated = await repo.upsert_metadata(number="LOCK-101", title="Scraped", actors=["A"])
        assert updated.title == "Manual"
        body = (await client.get(f"metadata/{meta.id}")).json()["metadata"]
        assert body["actors"] == ["A"]

        cleared = await client.put(f"metadata/{meta.id}/locks", json={"fields": []})
        assert cleared.status_code == 200
        assert cleared.json()["locked_fields"] == []

        bad = await client.put(f"metadata/{meta.id}/locks", json={"fields": ["bogus"]})
        assert bad.status_code == 422
        assert (await client.put("metadata/9999/locks", json={"fields": []})).status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_response_drops_unknown_locked_values(self, client: AsyncClient, repo: Repository):
        """库内非法存量锁值不使读路径 500, 并去重."""
        meta = await repo.upsert_metadata(number="LOCK-102")
        assert meta.id is not None
        async with repo._session() as session:
            row = await session.get(Metadata, meta.id)
            assert row is not None
            row.locked_fields = ["title", "title", "bogus"]
            session.add(row)
            await session.commit()

        detail = await client.get(f"metadata/{meta.id}")
        assert detail.status_code == 200
        assert detail.json()["metadata"]["locked_fields"] == ["title"]

        listed = await client.get("metadata?search=LOCK-102")
        assert listed.status_code == 200
        assert listed.json()["items"][0]["locked_fields"] == ["title"]


@pytest.mark.asyncio
class TestImageSelection:
    """候选池只增不重排, 换图只改选中项; thumb_urls 仍只存当前那张."""

    @staticmethod
    def _install_fake_web_client(app) -> None:
        async def fake_download(url: str, dest: Path, **kwargs: object) -> bool:
            dest.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (800, 538), "blue").save(dest)
            return True

        async def fake_resolve(url: str, **kwargs: object) -> str:
            return url

        app.state.runtime.web_client.download = fake_download  # type: ignore[method-assign]
        app.state.runtime.web_client.resolve_final_url = fake_resolve  # type: ignore[method-assign]

    async def test_select_keeps_previous_cover_in_pool(self, client, repo, app):
        """选第二张当封面后, 原封面仍留在候选池原位 —— 换一次图不该把编号全打乱."""
        first = "https://example.com/a.jpg"
        second = "https://example.com/b.jpg"
        self._install_fake_web_client(app)

        meta = await repo.upsert_metadata(number="SEL-001", thumb_urls=[first])

        resp = await client.post(f"metadata/{meta.id}/adopt-thumb", params={"source_url": second})

        assert resp.status_code == 200
        data = resp.json()
        assert data["thumb_urls"] == [second]  # 下游只看到当前那一张
        assert data["thumb_candidates"] == [first, second]  # 原位保留, 新的追加在后

    async def test_reselect_existing_candidate_does_not_duplicate(self, client, repo, app):
        """把它再选回来, 池里不应出现第二条同样的地址."""
        first = "https://example.com/a.jpg"
        second = "https://example.com/b.jpg"
        self._install_fake_web_client(app)

        meta = await repo.upsert_metadata(number="SEL-002", thumb_urls=[first])
        await client.post(f"metadata/{meta.id}/adopt-thumb", params={"source_url": second})
        resp = await client.post(f"metadata/{meta.id}/adopt-thumb", params={"source_url": first})

        assert resp.status_code == 200
        data = resp.json()
        assert data["thumb_candidates"] == [first, second]  # 顺序不变, 没有重复
        assert data["thumb_urls"] == [first]

    async def test_remove_drops_from_pool_and_clears_selection(self, client, repo, app):
        """删掉唯一一张后, 池与选中项一起清空."""
        only = "https://example.com/only.jpg"
        self._install_fake_web_client(app)

        meta = await repo.upsert_metadata(number="SEL-003", thumb_urls=[only])
        store = app.state.runtime.resource_store
        await store.acquire(only, app.state.runtime.web_client)
        internal = f"{RESOURCE_URL_PREFIX}/{ResourceStore.url_hash(only)}"
        await repo.update_metadata(meta.id, thumb_urls=[internal], thumb_candidates=[internal])

        resp = await client.request("DELETE", f"metadata/{meta.id}/images", params={"url": internal, "target": "thumb"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["thumb_urls"] == []
        assert data["thumb_candidates"] == []

    async def test_remove_middle_item_falls_back_to_pool_head(self, client, repo, app):
        """删掉的正在用的那张时, 顺延到池里第一张而不是留空."""
        first = "https://example.com/a.jpg"
        second = "https://example.com/b.jpg"
        self._install_fake_web_client(app)

        meta = await repo.upsert_metadata(number="SEL-004", thumb_urls=[first])
        await client.post(f"metadata/{meta.id}/adopt-thumb", params={"source_url": second})

        store = app.state.runtime.resource_store
        await store.acquire(second, app.state.runtime.web_client)
        internal = f"{RESOURCE_URL_PREFIX}/{ResourceStore.url_hash(second)}"
        await repo.update_metadata(meta.id, thumb_urls=[internal], thumb_candidates=[first, internal])

        resp = await client.request("DELETE", f"metadata/{meta.id}/images", params={"url": internal, "target": "thumb"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["thumb_candidates"] == [first]

    async def test_remove_deletes_extra_fanart(self, client, repo, app):
        """剧照在按站点分组的 extrafanart_urls 里, 删掉要落到那一层.

        早期只过滤 flat 的 ``extrafanart`` 属性 (只读首站), 结果剧照删了仍在, 前端看起来
        像「删除没反应」.
        """
        still = "https://example.com/still1.jpg"
        keep = "https://example.com/still2.jpg"
        meta = await repo.upsert_metadata(
            number="SEL-005",
            extrafanart_urls={"javdb": [still, keep]},
        )

        resp = await client.request("DELETE", f"metadata/{meta.id}/images", params={"url": still, "target": "thumb"})

        assert resp.status_code == 200
        updated = await repo.get_metadata(meta.id)
        assert updated is not None
        assert updated.extrafanart_urls == {"javdb": [keep]}

    async def test_remove_last_still_drops_site_group(self, client, repo, app):
        """某站的剧照全删完后该站分组应消失, 不留空数组."""
        only = "https://example.com/only-still.jpg"
        meta = await repo.upsert_metadata(number="SEL-006", extrafanart_urls={"javdb": [only]})

        resp = await client.request("DELETE", f"metadata/{meta.id}/images", params={"url": only, "target": "thumb"})

        assert resp.status_code == 200
        updated = await repo.get_metadata(meta.id)
        assert updated is not None
        assert updated.extrafanart_urls == {}


class TestImageInfo:
    """候选图的来源与分辨率, 供选图框展示."""

    def test_source_label_from_external_url(self) -> None:
        from amane.api.routes.metadata import _source_label

        assert _source_label("https://www.caribbeancompr.com/x.jpg", origin=None, raw_url=None) == "Caribbeancom"
        assert _source_label("https://www.1pondo.tv/x.jpg", origin=None, raw_url=None) == "1Pondo"
        assert _source_label("https://unknown.example/x.jpg", origin=None, raw_url=None) == ""

    def test_source_label_prefers_traced_origin(self) -> None:
        """裁切派生件的内部地址要溯源到原始站点, 而不是标成「自定义」."""
        from amane.api.routes.metadata import _source_label

        assert (
            _source_label(
                "/api/resources/abc123",
                origin="https://www.caribbeancom.com/x.jpg",
                raw_url="derived:abc123",
            )
            == "Caribbeancom"
        )

    def test_source_label_marks_upload_as_custom(self) -> None:
        from amane.api.routes.metadata import _source_label

        assert _source_label("/api/resources/abc123", origin=None, raw_url="upload:abc:thumb") == "自定义"

    @pytest.mark.asyncio
    async def test_image_info_returns_size_and_source(self, client, repo, app):
        first = "https://www.caribbeancom.com/x.jpg"

        async def fake_download(url: str, dest: Path, **kwargs: object) -> bool:
            dest.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (640, 360), "blue").save(dest)
            return True

        async def fake_resolve(url: str, **kwargs: object) -> str:
            return url

        app.state.runtime.web_client.download = fake_download  # type: ignore[method-assign]
        app.state.runtime.web_client.resolve_final_url = fake_resolve  # type: ignore[method-assign]

        # 端点按内部地址查库, 所以候选要用 /api/resources/{hash} 形式.
        store = app.state.runtime.resource_store
        await store.acquire(first, app.state.runtime.web_client)
        internal = f"{RESOURCE_URL_PREFIX}/{ResourceStore.url_hash(first)}"
        meta = await repo.upsert_metadata(number="INFO-001", thumb_urls=[internal])

        resp = await client.post(f"metadata/{meta.id}/image-info", json={"urls": [internal]})

        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        # 记录本身存的是原始站点地址, 溯源后应认出来源.
        assert items[0]["source"] == "Caribbeancom"
        assert (items[0]["width"], items[0]["height"]) == (640, 360)


class TestMetadataPatch:
    """PATCH 只写显式提供的字段: 省略键不改, 可空列给 null 即清空."""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_writes_only_given_fields(self, client: AsyncClient, repo: Repository):
        cases = [
            ({"title": "新标题"}, {"title": "新标题", "studio": "原片商"}),
            ({"title": None}, {"title": None, "studio": "原片商"}),
            ({"studio": None}, {"studio": None, "title": "原标题"}),
            ({"release": None}, {"release": None}),
        ]
        for index, (payload, expected) in enumerate(cases):
            meta = await repo.upsert_metadata(
                number=f"PATCH-W-{index}", title="原标题", studio="原片商", release="2020-01-15"
            )
            assert meta.id is not None

            resp = await client.patch(f"metadata/{meta.id}", json=payload)
            assert resp.status_code == 200
            detail = (await client.get(f"metadata/{meta.id}")).json()["metadata"]
            for field, value in expected.items():
                assert detail[field] == value, (payload, field)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_rejects_empty_body_and_invalid_values(self, client: AsyncClient, repo: Repository):
        """number 不在可写字段集内, 其显式 null 落到「没有需要修改的字段」; actors 等非空列由派生模型拒绝."""
        meta = await repo.upsert_metadata(number="PATCH-R", title="原标题")
        assert meta.id is not None

        for payload in ({}, {"number": None}, {"actors": None}, {"release": 20200101}):
            resp = await client.patch(f"metadata/{meta.id}", json=payload)
            assert resp.status_code == 422, payload
        detail = (await client.get(f"metadata/{meta.id}")).json()["metadata"]
        assert detail["title"] == "原标题"


@pytest.mark.asyncio
async def test_split_merged_tag_ids_merges_by_display_name(tmp_path) -> None:
    """按展示名分组: 没配映射的中文标签也要与译文同名的外文标签并成一组.

    例: 「二穴同入」(本来就是中文) 与「二穴同時挿入」(译作二穴同入) 界面上是同一条.
    """
    from types import SimpleNamespace

    from amane.api.routes.metadata import _split_merged_tag_ids
    from amane.db.repo_types import FacetItem
    from amane.tags.mapping import TagMappingStore

    store = TagMappingStore(tmp_path)
    store.seed([("二穴同入", 3), ("二穴同時挿入", 9), ("中出し", 5)])
    store.upsert("二穴同時挿入", "二穴同入")
    store.upsert("中出し", "中出")

    facets = [
        FacetItem(id=1, name="二穴同入", count=3, is_favorite=False),
        FacetItem(id=2, name="二穴同時挿入", count=9, is_favorite=False),
        FacetItem(id=3, name="中出し", count=5, is_favorite=False),
    ]

    class FakeRepo:
        async def list_facets(self, *args, **kwargs):
            return facets, len(facets)

    repo = FakeRepo()
    runtime = SimpleNamespace(tag_mappings=store)

    # 没有映射的中文标签: 与译文同名的日文标签并成一组, 走 OR.
    and_ids, or_ids = await _split_merged_tag_ids(repo, runtime, [1])
    assert and_ids is None
    assert sorted(or_ids or []) == [1, 2]

    # 有映射的那一方拿到同样的组.
    and_ids, or_ids = await _split_merged_tag_ids(repo, runtime, [2])
    assert and_ids is None
    assert sorted(or_ids or []) == [1, 2]

    # 没有同名兄弟的标签保持 AND.
    and_ids, or_ids = await _split_merged_tag_ids(repo, runtime, [3])
    assert and_ids == [3]
    assert or_ids is None
