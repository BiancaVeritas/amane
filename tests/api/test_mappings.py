"""映射表端点: 三个滑动选项各一组, 组间完全独立.

映射表行为本身 (对账删除、手动条目、归档、片商合表) 见 ``tests/tags/test_mapping.py``;
这里只覆盖端点契约: 路径里的 group 选表、各组可用动作不同、导出文件名.
"""

import pytest
from httpx2 import AsyncClient

from amane.tags import MAPPING_FILES, MappingGroup

GROUP_CASES = [
    (MappingGroup.TAG, "中出し"),
    (MappingGroup.ACTOR, "彩月七緒"),
    (MappingGroup.VENDOR, "S1"),
]


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(("group", "original"), GROUP_CASES)
async def test_mapping_crud_per_group(client: AsyncClient, group: MappingGroup, original: str) -> None:
    """每组都能写入、读回、导出; 写入不要求分类表里已经有这个名字."""
    saved = await client.put(f"mappings/{group}", json={"original": original, "translation": "译名"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["translation"] == "译名"

    listed = await client.get(f"mappings/{group}", params={"status": "mapped"})
    assert listed.status_code == 200
    assert original in {item["original"] for item in listed.json()["items"]}

    exported = await client.get(f"mappings/{group}/export")
    assert exported.status_code == 200
    assert exported.headers["content-disposition"].endswith(f'"{MAPPING_FILES[group]}"')
    assert exported.json()["entries"][original]["translation"] == "译名"


@pytest.mark.asyncio(loop_scope="function")
async def test_groups_endpoint_lists_three_slides(client: AsyncClient) -> None:
    """三组各自声明管的分类; 三组都有归档区, 前端据此渲染, 不硬编码."""
    response = await client.get("mappings/groups")
    assert response.status_code == 200
    items = {item["group"]: item for item in response.json()["items"]}
    assert set(items) == {"tag", "actor", "vendor"}
    assert all(item["archive"] is True for item in items.values())
    # 片商组三个分类合一, 不是三个滑动选项.
    assert items["vendor"]["kinds"] == ["studio", "publisher", "series"]


@pytest.mark.asyncio(loop_scope="function")
async def test_unknown_group_is_rejected(client: AsyncClient) -> None:
    """group 只认三组, 不静默落到标签表."""
    response = await client.get("mappings/director")
    assert response.status_code == 422


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize("group", list(MappingGroup))
async def test_delete_archives_instead_of_removing(client: AsyncClient, group: MappingGroup) -> None:
    """三组的删除都是归档: 词条不真删, 仍参与映射, 只是离开主列表.

    真删会让它在下次对账时按分类表重建出来, 用户看到的就是「删了又回来」.
    """
    await client.put(f"mappings/{group}", json={"original": "要归档的", "translation": None})

    assert (await client.delete(f"mappings/{group}/要归档的")).status_code == 204

    archived = await client.get(f"mappings/{group}", params={"archived": True})
    assert "要归档的" in {item["original"] for item in archived.json()["items"]}
    # 再删一次是 404: 已经在归档区里, 不重复归档.
    assert (await client.delete(f"mappings/{group}/要归档的")).status_code == 404
    # 还原回主列表.
    assert (await client.post(f"mappings/{group}/restore/要归档的")).status_code == 200
    pending = await client.get(f"mappings/{group}", params={"status": "pending"})
    assert "要归档的" in {item["original"] for item in pending.json()["items"]}


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize("group", list(MappingGroup))
async def test_mapped_entry_cannot_be_archived(client: AsyncClient, group: MappingGroup) -> None:
    """带译名的词条不许归档: 归档区表示「不需要译名」, 带着译名进去自相矛盾."""
    await client.put(f"mappings/{group}", json={"original": "有译名的", "translation": "译名"})

    blocked = await client.delete(f"mappings/{group}/有译名的")
    assert blocked.status_code == 409

    # 清空译名后就能归档.
    await client.put(f"mappings/{group}", json={"original": "有译名的", "translation": None})
    assert (await client.delete(f"mappings/{group}/有译名的")).status_code == 204


@pytest.mark.asyncio(loop_scope="function")
async def test_import_and_export_roundtrip(client: AsyncClient) -> None:
    """导出的词典能导回, 手动标记一并往返."""
    payload = {"version": 1, "entries": {"外部词条": {"translation": "外部译名"}}}

    imported = await client.post("mappings/actor/import", json=payload)
    assert imported.status_code == 200
    assert imported.json()["imported"] == 1

    exported = await client.get("mappings/actor/export")
    entry = exported.json()["entries"]["外部词条"]
    assert entry["translation"] == "外部译名"
    assert entry["manual"] is True


@pytest.mark.asyncio(loop_scope="function")
async def test_bad_import_payload(client: AsyncClient) -> None:
    """格式不对的导入文件报 400, 不静默吞掉; 空对象是合法空词典."""
    bad = await client.post("mappings/tag/import", json={"entries": []})
    assert bad.status_code == 400
    assert "导入文件格式不正确" in bad.text

    empty = await client.post("mappings/tag/import", json={"entries": {}})
    assert empty.status_code == 200
    assert empty.json()["imported"] == 0
