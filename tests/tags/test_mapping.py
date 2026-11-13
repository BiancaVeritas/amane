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


def test_originals_matching_finds_entries_by_translation(tmp_path) -> None:
    """按展示的译文能找到原名.

    列表里显示译文、库里存原名; 只按原名搜索会让用户照着屏幕上的字输入却搜不到.
    """
    store = TagMappingStore(tmp_path)
    store.seed([("完全無修正", 5), ("無碼", 3), ("中出し", 9)])
    store.upsert("完全無修正", "无修正")
    store.upsert("無碼", "无修正")
    store.upsert("中出し", "内射")

    assert sorted(store.originals_matching("无修正")) == ["完全無修正", "無碼"]
    assert store.originals_matching("内射") == ["中出し"]
    # 没有译文的词条不该被这个方法命中, 原名匹配交给数据库.
    assert store.originals_matching("中出し") == []
    assert store.originals_matching("") == []
