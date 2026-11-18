from __future__ import annotations

import json
from pathlib import Path

import pytest

from amane.mappings.store import NumberMappingStore


def test_initializes_three_empty_external_maps(tmp_path: Path) -> None:
    store = NumberMappingStore(tmp_path)

    assert store.list("caribbeancompr") == {}
    assert store.list("d2pass") == {}
    assert store.list("caribbeancom") == {}
    assert sorted(path.name for path in (tmp_path / "mappings").glob("*.json")) == [
        "caribbeancom_index.json",
        "caribbeancompr_heyzo_index.json",
        "d2pass_heyzo_index.json",
    ]


def test_merge_conflicts_only_when_id_differs(tmp_path: Path) -> None:
    store = NumberMappingStore(tmp_path)
    store.save("caribbeancompr", "0123", {"id": "120120_001", "override_title": "原标题"})

    same_id = store.import_merge("caribbeancompr", {"0123": {"id": "120120_001", "override_title": "新标题"}})
    assert same_id == {"added": [], "unchanged": ["0123"], "conflicts": []}
    assert store.list("caribbeancompr")["0123"]["override_title"] == "原标题"

    different_id = store.import_merge("caribbeancompr", {"0123": {"id": "120120_002"}})
    assert len(different_id["conflicts"]) == 1
    assert different_id["conflicts"][0]["old"]["id"] == "120120_001"
    assert different_id["conflicts"][0]["new"]["id"] == "120120_002"


def test_resolve_conflict_does_not_resurrect_missing_key(tmp_path: Path) -> None:
    store = NumberMappingStore(tmp_path)
    assert (
        store.resolve(
            "caribbeancompr",
            [{"key": "missing", "use_import": True, "value": {"id": "120120_002"}}],
        )
        == 0
    )
    assert store.list("caribbeancompr") == {}


def test_resolve_conflict_does_not_overwrite_a_newer_mapping(tmp_path: Path) -> None:
    store = NumberMappingStore(tmp_path)
    store.save("caribbeancompr", "0123", {"id": "120120_001"})
    store.save("caribbeancompr", "0123", {"id": "120120_003"})

    changed = store.resolve(
        "caribbeancompr",
        [
            {
                "key": "0123",
                "use_import": True,
                "expected_old_id": "120120_001",
                "value": {"id": "120120_004"},
            }
        ],
    )

    assert changed == 0
    assert store.list("caribbeancompr")["0123"]["id"] == "120120_003"


def test_resolve_conflict_replaces_expected_old_mapping(tmp_path: Path) -> None:
    store = NumberMappingStore(tmp_path)
    store.save("caribbeancompr", "0123", {"id": "120120_001"})

    changed = store.resolve(
        "caribbeancompr",
        [
            {
                "key": "0123",
                "use_import": True,
                "expected_old_id": "120120_001",
                "value": {"id": "120120_002"},
            }
        ],
    )

    assert changed == 1
    assert store.list("caribbeancompr")["0123"]["id"] == "120120_002"


def test_source_specific_key_and_id_validation(tmp_path: Path) -> None:
    store = NumberMappingStore(tmp_path)
    store.save("d2pass", "0001", {"id": 123})
    store.save("caribbeancom", "RED-123.mp4", {"id": "source-id"})

    with pytest.raises(ValueError):
        store.save("d2pass", "0002", {"id": "123"})
    with pytest.raises(ValueError):
        store.save("caribbeancompr", "HEYZO1234", {"id": "source-id"})
    with pytest.raises(ValueError):
        store.save("caribbeancompr", "123", {"id": "source-id"})

    assert store.list("caribbeancom", "RED") == {"RED-123.mp4": {"id": "source-id"}}


def test_caribbeancompr_id_must_match_expected_format(tmp_path: Path) -> None:
    store = NumberMappingStore(tmp_path)

    store.save("caribbeancompr", "0001", {"id": "091026_001"})

    with pytest.raises(ValueError, match="Caribbeancompr"):
        store.save("caribbeancompr", "0002", {"id": "091026_01"})

    with pytest.raises(ValueError, match="Caribbeancompr"):
        store.save("caribbeancompr", "0003", {"id": "ABCDEF_001"})

    with pytest.raises(ValueError, match="Caribbeancompr"):
        store.save("caribbeancompr", "0004", {"id": "091026_001_extra"})

    assert store.list("caribbeancompr") == {"0001": {"id": "091026_001"}}


def test_caribbeancom_id_must_match_crawler_format(tmp_path: Path) -> None:
    store = NumberMappingStore(tmp_path)

    store.save("caribbeancom", "ABC-123", {"id": "movie_001-A"})

    with pytest.raises(ValueError, match="Caribbeancom Index"):
        store.save("caribbeancom", "ABC-124", {"id": "movie/001"})

    with pytest.raises(ValueError, match="Caribbeancom Index"):
        store.save("caribbeancom", "ABC-125", {"id": "movie 001"})

    with pytest.raises(ValueError, match="Caribbeancom Index"):
        store.save("caribbeancom", "ABC-126", {"id": "电影001"})

    assert store.list("caribbeancom") == {"ABC-123": {"id": "movie_001-A"}}


def test_batch_delete_and_atomic_json_content(tmp_path: Path) -> None:
    store = NumberMappingStore(tmp_path)
    store.save("d2pass", "0001", {"id": 1})
    store.save("d2pass", "0002", {"id": 2})

    assert store.delete_many("d2pass", ["0001", "missing", "0002"]) == 2
    persisted = json.loads((tmp_path / "mappings" / "d2pass_heyzo_index.json").read_text())
    assert persisted == {}
    assert not list((tmp_path / "mappings").glob("*.tmp"))


def test_actor_is_preserved_as_mapping_metadata_only(tmp_path: Path) -> None:
    store = NumberMappingStore(tmp_path)
    store.save(
        "caribbeancom",
        "RED-123.mp4",
        {"id": "source-id", "override_title": "标题", "actor": "演员甲、演员乙"},
    )

    assert store.list("caribbeancom")["RED-123.mp4"] == {
        "id": "source-id",
        "override_title": "标题",
        "actor": "演员甲、演员乙",
    }

    # Same ID imports may enrich the actor metadata without creating a conflict.
    result = store.import_merge("caribbeancom", {"RED-123.mp4": {"id": "source-id", "actor": "演员丙"}})
    assert result == {"added": [], "unchanged": ["RED-123.mp4"], "conflicts": []}
    assert store.list("caribbeancom")["RED-123.mp4"]["actor"] == "演员丙"

    with pytest.raises(ValueError, match="actor 必须是字符串"):
        store.save("caribbeancom", "RED-124.mp4", {"id": "120120_007", "actor": ["演员"]})


def test_mapping_title_is_separate_from_legacy_scraper_override_title(tmp_path: Path) -> None:
    store = NumberMappingStore(tmp_path)
    store.save(
        "caribbeancom",
        "RED-123.mp4",
        {
            "id": "source-id",
            "actor": "演员甲",
            "title": "第三方标题",
            "override_title": "旧刮削标题覆盖",
        },
    )

    assert store.list("caribbeancom")["RED-123.mp4"] == {
        "id": "source-id",
        "actor": "演员甲",
        "title": "第三方标题",
        "override_title": "旧刮削标题覆盖",
    }

    # Mapping-only imports update title/actor while leaving scraper override intact.
    store.import_merge(
        "caribbeancom",
        {"RED-123.mp4": {"id": "source-id", "actor": "演员乙", "title": "新第三方标题"}},
    )
    saved = store.list("caribbeancom")["RED-123.mp4"]
    assert saved["actor"] == "演员乙"
    assert saved["title"] == "新第三方标题"
    assert saved["override_title"] == "旧刮削标题覆盖"


def test_replace_preserves_legacy_override_title_for_retained_keys(tmp_path: Path) -> None:
    store = NumberMappingStore(tmp_path)
    store.save(
        "caribbeancom",
        "RED-123.mp4",
        {"id": "source-id", "override_title": "旧刮削标题覆盖"},
    )
    store.replace(
        "caribbeancom",
        {"RED-123.mp4": {"id": "source-id", "actor": "演员甲", "title": "第三方标题"}},
    )
    saved = store.list("caribbeancom")["RED-123.mp4"]
    assert saved["title"] == "第三方标题"
    assert saved["override_title"] == "旧刮削标题覆盖"


def test_initializes_number_maps_from_project_seed_only_once(tmp_path: Path) -> None:
    seed_dir = tmp_path / "project-resources"
    seed_dir.mkdir()
    (seed_dir / "d2pass_heyzo_index.json").write_text(
        json.dumps({"0001": {"id": 123, "actor": "演员甲", "title": "作品甲"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (seed_dir / "caribbeancompr_heyzo_index.json").write_text(
        json.dumps({"0004": {"id": "120120_003", "actor": "演员乙"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    store = NumberMappingStore(tmp_path / "data", seed_dir)
    assert store.list("d2pass") == {"0001": {"id": 123, "actor": "演员甲", "title": "作品甲"}}
    assert store.list("caribbeancompr") == {"0004": {"id": "120120_003", "actor": "演员乙"}}
    assert store.list("caribbeancom") == {}

    # Later project-resource changes must not overwrite the local managed copy.
    (seed_dir / "d2pass_heyzo_index.json").write_text("{}", encoding="utf-8")
    store_again = NumberMappingStore(tmp_path / "data", seed_dir)
    assert store_again.list("d2pass") == {"0001": {"id": 123, "actor": "演员甲", "title": "作品甲"}}


def test_existing_local_number_map_is_never_overwritten_by_seed(tmp_path: Path) -> None:
    seed_dir = tmp_path / "project-resources"
    seed_dir.mkdir()
    (seed_dir / "d2pass_heyzo_index.json").write_text(
        json.dumps({"0001": {"id": 123}}, ensure_ascii=False), encoding="utf-8"
    )
    local_dir = tmp_path / "data" / "mappings"
    local_dir.mkdir(parents=True)
    (local_dir / "d2pass_heyzo_index.json").write_text(
        json.dumps({"0002": {"id": 456}}, ensure_ascii=False), encoding="utf-8"
    )

    store = NumberMappingStore(tmp_path / "data", seed_dir)
    assert store.list("d2pass") == {"0002": {"id": 456}}


def test_merge_detects_same_id_under_different_numbers_and_within_batch(tmp_path: Path) -> None:
    store = NumberMappingStore(tmp_path)
    store.save("caribbeancompr", "0001", {"id": "120120_005"})

    result = store.import_merge_rows(
        "caribbeancompr",
        [
            ("0002", {"id": "120120_005"}),
            ("0003", {"id": "120120_006"}),
            ("0004", {"id": "120120_006"}),
        ],
    )

    assert result["added"] == ["0003"]
    assert [(item["key"], item["old_key"], item["reason"]) for item in result["conflicts"]] == [
        ("0002", "0001", "id"),
        ("0004", "0003", "id"),
    ]
    assert "0002" not in store.list("caribbeancompr")
    assert "0004" not in store.list("caribbeancompr")


def test_resolve_different_number_same_id_moves_mapping_only_when_selected(tmp_path: Path) -> None:
    store = NumberMappingStore(tmp_path)
    store.save("caribbeancompr", "0001", {"id": "120120_005"})
    store.import_merge("caribbeancompr", {"0002": {"id": "120120_005"}})

    assert (
        store.resolve(
            "caribbeancompr",
            [
                {
                    "key": "0002",
                    "old_key": "0001",
                    "use_import": True,
                    "expected_old_id": "120120_005",
                    "value": {"id": "120120_005"},
                }
            ],
        )
        == 1
    )
    assert store.list("caribbeancompr") == {"0002": {"id": "120120_005"}}


def test_resolve_does_not_resurrect_deleted_old_key(tmp_path: Path) -> None:
    store = NumberMappingStore(tmp_path)
    store.save("caribbeancompr", "0001", {"id": "120120_001"})
    store.delete_many("caribbeancompr", ["0001"])

    changed = store.resolve(
        "caribbeancompr",
        [
            {
                "key": "0002",
                "old_key": "0001",
                "use_import": True,
                "expected_old_id": "120120_001",
                "value": {"id": "120120_002"},
            }
        ],
    )

    assert changed == 0
    assert store.list("caribbeancompr") == {}


def test_resolve_does_not_overwrite_occupied_target_key(tmp_path: Path) -> None:
    store = NumberMappingStore(tmp_path)
    store.save("caribbeancompr", "0001", {"id": "120120_001"})
    store.save("caribbeancompr", "0002", {"id": "120120_007"})

    changed = store.resolve(
        "caribbeancompr",
        [
            {
                "key": "0002",
                "old_key": "0001",
                "use_import": True,
                "expected_old_id": "120120_001",
                "value": {"id": "120120_001"},
            }
        ],
    )

    assert changed == 0
    assert store.list("caribbeancompr") == {
        "0001": {"id": "120120_001"},
        "0002": {"id": "120120_007"},
    }


def test_resolve_does_not_reuse_old_key_across_decisions(tmp_path: Path) -> None:
    store = NumberMappingStore(tmp_path)
    store.save("caribbeancompr", "0001", {"id": "120120_005"})

    changed = store.resolve(
        "caribbeancompr",
        [
            {
                "key": "0002",
                "old_key": "0001",
                "use_import": True,
                "expected_old_id": "120120_005",
                "value": {"id": "120120_005"},
            },
            {
                "key": "0003",
                "old_key": "0001",
                "use_import": True,
                "expected_old_id": "120120_005",
                "value": {"id": "120120_005"},
            },
        ],
    )

    assert changed == 1
    assert store.list("caribbeancompr") == {
        "0002": {"id": "120120_005"},
    }


def test_manual_save_rejects_id_already_used_by_another_number(tmp_path: Path) -> None:
    store = NumberMappingStore(tmp_path)
    store.save("caribbeancompr", "0001", {"id": "120120_005"})
    with pytest.raises(ValueError, match="0001"):
        store.save("caribbeancompr", "0002", {"id": "120120_005"})


def test_export_data_contains_only_normal_mappings(tmp_path: Path) -> None:
    store = NumberMappingStore(tmp_path)
    store.save("caribbeancompr", "0123", {"id": "120120_008"})
    store.add_conflicts(
        "caribbeancompr",
        [
            {
                "group_id": 1,
                "reason": "id",
                "120120_001": {"key": "9999", "id": "exception-old"},
                "120120_002": {"key": "8888", "id": "exception-new"},
            }
        ],
    )

    exported = store.export_data("caribbeancompr")
    assert exported == {"0123": {"id": "120120_008"}}
    assert "9999" not in exported and "8888" not in exported
