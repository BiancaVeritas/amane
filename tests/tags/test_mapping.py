from amane.tags.mapping import TagMappingStore, detect_language


def test_language_detection_prefers_japanese_kana() -> None:
    assert detect_language("中出し") == "ja"
    assert detect_language("Creampie") == "en"
    assert detect_language("内射") == "zh"


def test_mapping_deduplicates_after_translation_and_persists(tmp_path) -> None:
    store = TagMappingStore(tmp_path)
    store.seed([("中出し", 10), ("Creampie", 8), ("制服", 4)])
    store.upsert("中出し", "内射")
    store.upsert("Creampie", "内射")
    assert store.map_tags(["中出し", "Creampie", "制服"]) == ["内射", "制服"]

    restored = TagMappingStore(tmp_path)
    assert restored.map_tags(["中出し", "Creampie", "制服"]) == ["内射", "制服"]
    pending, total = restored.list(status="pending")
    assert total == 1
    assert pending[0].original == "制服"


def test_import_export_keeps_original_entries(tmp_path) -> None:
    store = TagMappingStore(tmp_path)
    store.import_data({"entries": {"Creampie": {"translation": "内射"}}})
    assert store.export()["entries"]["Creampie"]["translation"] == "内射"


def test_mapped_label_keeps_a_clickable_facet_id(tmp_path) -> None:
    store = TagMappingStore(tmp_path)
    store.seed([("Creampie", 3)])
    store.upsert("Creampie", "内射")
    assert store.map_id_map({"Creampie": 42}) == {"内射": 42}


def test_global_mapping_switch_disables_mapping_and_persists(tmp_path) -> None:
    store = TagMappingStore(tmp_path)
    store.seed([("Creampie", 3), ("制服", 2)])
    store.upsert("Creampie", "内射")
    assert store.map_tags(["Creampie", "制服"]) == ["内射", "制服"]
    assert store.map_id_map({"Creampie": 42}) == {"内射": 42}

    store.set_enabled(False)
    assert store.map_tags(["Creampie", "制服"]) == ["Creampie", "制服"]
    assert store.map_id_map({"Creampie": 42}) == {"Creampie": 42}

    restored = TagMappingStore(tmp_path)
    assert restored.enabled is False
    assert restored.map_tags(["Creampie", "制服"]) == ["Creampie", "制服"]
    assert restored.list(status="mapped")[0][0].translation == "内射"

    restored.set_enabled(True)
    assert restored.map_tags(["Creampie", "制服"]) == ["内射", "制服"]


def test_observe_new_tags_while_global_mapping_is_disabled(tmp_path) -> None:
    """关闭展示映射不应暂停新标签收集或管理列表更新。"""
    store = TagMappingStore(tmp_path)
    store.set_enabled(False)

    store.observe(["新しいタグ", "New Tag"], source="scraper-a")

    items, total = store.list(status="pending")
    assert total == 2
    assert {item.original for item in items} == {"新しいタグ", "New Tag"}
    assert store.counts() == {"all": 2, "mapped": 0, "pending": 2, "archived": 0}
    assert store.enabled is False


def test_multiple_original_tags_share_one_display_name(tmp_path) -> None:
    store = TagMappingStore(tmp_path)
    store.seed([("AAA", 2), ("BBB", 2)])
    store.upsert("AAA", "CCC")
    store.upsert("BBB", "CCC")

    assert store.display_name("AAA") == "CCC"
    assert store.display_name("BBB") == "CCC"
    assert store.map_tags(["AAA", "BBB"]) == ["CCC"]
    # API 对外保留一个代表 ID；筛选接口会在路由层将其扩展为全部原始 ID。
    assert store.map_id_map({"AAA": 10, "BBB": 20}) == {"CCC": 10}


def test_archive_restore_and_translation_state_transitions(tmp_path) -> None:
    store = TagMappingStore(tmp_path)
    store.seed([("不要翻译", 5), ("Creampie", 4)])
    assert store.archive("不要翻译") is True
    assert store.archive("Creampie") is True
    assert store.archive("missing") is False
    assert store.archive("不要翻译") is True
    _archived, total = store.list(status="archived")
    assert total == 2
    assert store.counts() == {"all": 2, "mapped": 0, "pending": 0, "archived": 2}

    # 归档词条可以直接保存译名，自动转为已映射。
    store.upsert("不要翻译", "无需翻译")
    assert store.list(status="mapped")[1] == 1
    assert store.archive("不要翻译") is False

    assert store.restore("Creampie") is True
    assert store.restore("Creampie") is False
    assert store.counts() == {"all": 2, "mapped": 1, "pending": 1, "archived": 0}


def test_batch_archive_skips_entries_with_translations_and_export_filters_pending(tmp_path) -> None:
    store = TagMappingStore(tmp_path)
    store.seed([("mapped", 8), ("pending", 7), ("archive", 6)])
    store.upsert("mapped", "已翻译")
    archived, skipped = store.archive_many(["mapped", "pending", "archive", "missing"])
    assert (archived, skipped) == (2, 2)
    exported = store.export()["entries"]
    assert set(exported) == {"mapped", "pending", "archive"}  # pending was archived by this batch
    store.seed([("new-pending", 1)])
    assert "new-pending" not in store.export()["entries"]
    assert exported["archive"]["archived"] is True


def test_import_restores_archived_state(tmp_path) -> None:
    store = TagMappingStore(tmp_path)
    store.import_data({"entries": {"skip": {"translation": None, "archived": True}}})
    assert store.list(status="archived")[1] == 1
    restored = TagMappingStore(tmp_path)
    assert restored.list(status="archived")[1] == 1
