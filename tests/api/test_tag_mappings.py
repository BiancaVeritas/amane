"""标签中文映射全局开关 API 测试。"""

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI
    from httpx2 import AsyncClient

    from amane.db.repository import Repository


class TestTagMappingStateHttp:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_state_defaults_to_enabled_and_can_be_toggled(self, client: AsyncClient):
        initial = await client.get("tag-mappings/state")
        assert initial.status_code == 200
        assert initial.json() == {"enabled": True}

        disabled = await client.put("tag-mappings/state", json={"enabled": False})
        assert disabled.status_code == 200
        assert disabled.json() == {"enabled": False}

        current = await client.get("tag-mappings/state")
        assert current.status_code == 200
        assert current.json() == {"enabled": False}

        enabled = await client.put("tag-mappings/state", json={"enabled": True})
        assert enabled.status_code == 200
        assert enabled.json() == {"enabled": True}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_state_update_is_persisted(self, client: AsyncClient, app: FastAPI):
        response = await client.put("tag-mappings/state", json={"enabled": False})
        assert response.status_code == 200

        mapping_path = app.state.runtime.tag_mappings.path
        saved = json.loads(mapping_path.read_text(encoding="utf-8"))
        assert saved["enabled"] is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_state_rejects_non_boolean_value(self, client: AsyncClient):
        response = await client.put("tag-mappings/state", json={"enabled": "off"})
        assert response.status_code == 422


class TestMappedTagFacetSearch:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_search_group_count_and_filter_use_all_original_tags(
        self, client: AsyncClient, app: FastAPI, repo: Repository
    ) -> None:
        both = await repo.upsert_metadata(number="MAP-001", tags=["AAA", "BBB"])
        await repo.upsert_metadata(number="MAP-002", tags=["AAA"])
        await repo.upsert_metadata(number="MAP-003", tags=["BBB"])
        assert both.id is not None

        mappings = app.state.runtime.tag_mappings
        mappings.upsert("AAA", "CCC")
        mappings.upsert("BBB", "CCC")

        facets = await client.get("facets/tag?search=CCC")
        assert facets.status_code == 200
        body = facets.json()
        assert body["total"] == 1
        assert body["items"][0]["name"] == "CCC"
        assert body["items"][0]["count"] == 3

        representative_id = body["items"][0]["id"]
        filtered = await client.get(f"metadata?tag_id={representative_id}")
        assert filtered.status_code == 200
        assert filtered.json()["total"] == 3
        assert {item["number"] for item in filtered.json()["items"]} == {"MAP-001", "MAP-002", "MAP-003"}
