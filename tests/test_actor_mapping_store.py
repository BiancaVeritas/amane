from __future__ import annotations

import json
from pathlib import Path

import pytest

from amane.mappings.actor_store import ActorMappingStore


def test_initial_seed_and_exact_name_lookup(tmp_path: Path) -> None:
    seed = tmp_path / "seed.json"
    seed.write_text(
        json.dumps({"725921": {"name": "長谷川杏実", "aliases": ["長谷川みお"]}}, ensure_ascii=False), encoding="utf-8"
    )
    store = ActorMappingStore(tmp_path / "data", seed)
    assert store.find_ids_by_name("長谷川杏実") == ["725921"]
    assert store.find_ids_by_name("長谷川みお") == []
    assert store.find_id("长谷川杏实") is None


def test_manual_save_checks_only_primary_names_and_ids(tmp_path: Path) -> None:
    store = ActorMappingStore(tmp_path)
    store.save("100", {"name": "主名甲", "aliases": ["别名甲"]})
    store.save("101", {"name": "别名甲", "aliases": []})
    store.save("102", {"name": "主名乙", "aliases": ["主名甲"]})
    with pytest.raises(ValueError, match="标准姓名重复"):
        store.save("103", {"name": "主名甲", "aliases": []})
    with pytest.raises(ValueError, match="ID 已存在"):
        store.save("100", {"name": "另一个名字", "aliases": []}, original_id="101")


def test_edit_excludes_self_but_checks_every_other_record(tmp_path: Path) -> None:
    store = ActorMappingStore(tmp_path)
    store.save("100", {"name": "主名甲", "aliases": ["别名甲"]})
    store.save("100", {"name": "主名甲改", "aliases": ["别名甲"]}, original_id="100")
    assert store.list()["100"]["name"] == "主名甲改"
    store.save("200", {"name": "主名乙", "aliases": []})
    store.save("100", {"name": "主名甲改", "aliases": ["主名乙"]}, original_id="100")


def test_import_duplicate_id_is_quarantined(tmp_path: Path) -> None:
    store = ActorMappingStore(tmp_path)
    store.save("725921", {"name": "主名", "aliases": ["别名一", "别名二"]})
    result = store.import_merge({"725921": {"name": "新主名", "aliases": ["新别名"]}})
    assert result["added"] == []
    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["reason"] == "id"
    assert store.list() == {}


def test_import_allows_alias_collisions_but_quarantines_primary_name_collisions(tmp_path: Path) -> None:
    store = ActorMappingStore(tmp_path)
    store.save("100", {"name": "主名甲", "aliases": ["共用别名"]})
    result = store.import_merge(
        {
            "101": {"name": "主名乙", "aliases": ["共用别名"]},
            "102": {"name": "主名甲", "aliases": []},
        }
    )
    assert result["added"] == ["101"]
    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["reason"] == "name"
    assert result["conflicts"][0]["old"]["id"] == "100"
    assert "100" not in store.list() and "102" not in store.list()


def test_replace_rejects_duplicate_primary_names_but_allows_alias_duplicates(tmp_path: Path) -> None:
    store = ActorMappingStore(tmp_path)
    assert (
        store.replace({"1": {"name": "演员甲", "aliases": ["共用"]}, "2": {"name": "演员乙", "aliases": ["共用"]}}) == 2
    )
    with pytest.raises(ValueError, match="标准姓名重复"):
        store.replace({"1": {"name": "同名", "aliases": []}, "2": {"name": "同名", "aliases": []}})


def test_import_repeated_aliases_do_not_conflict(tmp_path: Path) -> None:
    store = ActorMappingStore(tmp_path)
    result = store.import_merge(
        {
            "100": {"name": "演员甲", "aliases": ["共享别名"]},
            "200": {"name": "演员乙", "aliases": ["共享别名"]},
        }
    )
    assert result["added"] == ["100", "200"]
    assert result["conflicts"] == []


def test_import_rows_preserves_repeated_ids_and_reports_name_collisions(tmp_path: Path) -> None:
    store = ActorMappingStore(tmp_path)
    result = store.import_merge_rows(
        [
            ("100", {"name": "演员甲", "aliases": []}),
            ("100", {"name": "演员甲别名", "aliases": []}),
            ("200", {"name": "演员甲", "aliases": []}),
            ("300", {"name": "演员丙", "aliases": []}),
        ]
    )

    assert result["added"] == ["300"]
    assert len(result["conflicts"]) == 2
    assert result["conflicts"][0]["old"]["id"] == "100"
    assert result["conflicts"][0]["new"]["id"] == "100"
    assert result["conflicts"][1]["reason"] == "name"
    assert "100" not in store.list() and "200" not in store.list()


def test_exports_contain_only_normal_actor_mappings(tmp_path: Path) -> None:
    store = ActorMappingStore(tmp_path)
    store.save("100", {"name": "正常演员", "aliases": ["正常别名"]})
    store.add_conflicts(
        [
            {
                "group_id": 1,
                "reason": "id",
                "old": {"id": "200", "name": "异常旧记录", "aliases": []},
                "new": {"id": "300", "name": "异常新记录", "aliases": []},
            }
        ]
    )

    assert store.export_data() == {"100": {"name": "正常演员", "aliases": ["正常别名"]}}
    csv_text = store.export_csv()
    assert "正常演员" in csv_text
    assert "异常旧记录" not in csv_text
    assert "异常新记录" not in csv_text


def test_existing_empty_map_is_seeded_once_without_overwriting_user_data(tmp_path: Path) -> None:
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps({"1": {"name": "内置演员", "aliases": []}}, ensure_ascii=False), encoding="utf-8")
    data_dir = tmp_path / "data"
    mapping_dir = data_dir / "mappings"
    mapping_dir.mkdir(parents=True)
    (mapping_dir / "minnano_actress_map.json").write_text("{}\n", encoding="utf-8")
    store = ActorMappingStore(data_dir, seed)
    assert store.list() == {"1": {"name": "内置演员", "aliases": []}}
    store.save("2", {"name": "用户演员", "aliases": []})
    again = ActorMappingStore(data_dir, seed)
    assert again.list()["2"]["name"] == "用户演员"


def test_missing_seed_can_be_applied_when_it_appears_later(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    seed = tmp_path / "seed.json"

    store = ActorMappingStore(data_dir, seed)
    assert store.list() == {}

    seed.write_text(
        json.dumps({"1": {"name": "内置演员", "aliases": []}}, ensure_ascii=False),
        encoding="utf-8",
    )

    again = ActorMappingStore(data_dir, seed)
    assert again.list() == {"1": {"name": "内置演员", "aliases": []}}


def test_seed_is_not_reapplied_after_mapping_is_cleared(tmp_path: Path) -> None:
    seed = tmp_path / "seed.json"
    seed.write_text(
        json.dumps({"1": {"name": "内置演员", "aliases": []}}, ensure_ascii=False),
        encoding="utf-8",
    )

    data_dir = tmp_path / "data"
    store = ActorMappingStore(data_dir, seed)
    assert store.list() == {"1": {"name": "内置演员", "aliases": []}}

    store.replace({})
    again = ActorMappingStore(data_dir, seed)

    assert again.list() == {}
