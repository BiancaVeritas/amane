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


def test_delete_archives_instead_of_removing(tmp_path) -> None:
    """删除做成归档: 真删会让它在下次 seed 时按标签表重建出来."""
    from amane.tags.mapping import TagMappingStore

    store = TagMappingStore(tmp_path)
    store.seed([("中出し", 10), ("巨乳", 5)])

    assert store.delete("中出し") is True
    # 归档后仍在映射里生效, 计数也照常.
    assert store.map_tags(["中出し"]) == ["中出し"]
    active, total = store.list()
    assert total == 1 and active[0].original == "巨乳"
    archived, archived_total = store.list(archived=True)
    assert archived_total == 1 and archived[0].original == "中出し"

    # 再 seed 一次: 归档项不会被丢回来, 计数也正常更新.
    store.seed([("中出し", 12), ("巨乳", 5)])
    _, total_after = store.list()
    assert total_after == 1, "归档项不该被 seed 重新拉回主列表"
    entry = store.list(archived=True)[0][0]
    assert entry.usage_count == 12
    assert store.counts()["archived"] == 1


def test_restore_puts_entry_back(tmp_path) -> None:
    """还原把词条放回主列表, 计数一直没丢.

    已映射的不能归档 (归档区表示「不需要译名」), 所以先清空译名再归档;
    还原后重新填回译名即可.
    """
    from amane.tags.mapping import TagMappingStore

    store = TagMappingStore(tmp_path)
    store.seed([("中出し", 10)])
    store.delete("中出し")

    assert store.restore("中出し") is True
    active, total = store.list()
    assert total == 1
    assert active[0].usage_count == 10, "还原后计数要还在"
    assert store.counts()["archived"] == 0


def test_delete_twice_and_restore_unknown(tmp_path) -> None:
    """重复归档/还原不存在的词条都返回 False, 不抛异常."""
    from amane.tags.mapping import TagMappingStore

    store = TagMappingStore(tmp_path)
    store.seed([("中出し", 1)])
    assert store.delete("中出し") is True
    assert store.delete("中出し") is False
    assert store.restore("不存在") is False


def test_all_status_includes_archived(tmp_path) -> None:
    """「全部词条」要把归档区也算进来, 否则会让人以为词条凭空消失了."""
    from amane.tags.mapping import TagMappingStore

    store = TagMappingStore(tmp_path)
    store.seed([("中出し", 10), ("巨乳", 5), ("美脚", 3)])
    store.upsert("中出し", "中出")
    store.delete("巨乳")

    # 默认档 (未归档) 看不到归档项.
    _, total = store.list()
    assert total == 2
    # 「全部词条」能搜到归档项.
    items, total_all = store.list(include_archived=True)
    assert total_all == 3
    assert "巨乳" in {item.original for item in items}
    # 搜索归档词条也能命中.
    hits, hit_total = store.list(include_archived=True, search="巨乳")
    assert hit_total == 1 and hits[0].original == "巨乳"


def test_counts_all_includes_archived(tmp_path) -> None:
    """``all`` 与「全部词条」那一档一致, 同时 archived 单独给出."""
    from amane.tags.mapping import TagMappingStore

    store = TagMappingStore(tmp_path)
    store.seed([("中出し", 10), ("巨乳", 5)])
    store.upsert("中出し", "中出")
    store.delete("巨乳")

    counts = store.counts()
    assert counts["all"] == 2  # 含归档
    assert counts["mapped"] == 1
    assert counts["pending"] == 0
    assert counts["archived"] == 1


def test_export_only_keeps_touched_entries(tmp_path) -> None:
    """导出只带已映射与已归档的; 待映射的原始词条不导出."""
    from amane.tags.mapping import TagMappingStore

    store = TagMappingStore(tmp_path)
    store.seed([("中出し", 10), ("巨乳", 5), ("美脚", 3)])
    store.upsert("中出し", "中出")
    store.delete("巨乳")

    exported = store.export()["entries"]
    assert set(exported) == {"中出し", "巨乳"}
    assert exported["中出し"]["translation"] == "中出"
    assert exported["巨乳"]["archived"] is True
    assert "美脚" not in exported


def test_export_import_roundtrip_keeps_archived(tmp_path) -> None:
    """导出再导入要完整往返: 译名、归档状态、计数、来源都不能丢.

    归档状态丢了等于把「不显示但仍在用」这个决定扔了 —— 词条会回到主列表.
    """
    from amane.tags.mapping import TagMappingStore

    source = TagMappingStore(tmp_path / "a")
    source.seed([("中出し", 10), ("巨乳", 5), ("美脚", 3)])
    source.upsert("中出し", "中出")
    source.delete("巨乳")
    payload = source.export()

    target = TagMappingStore(tmp_path / "b")
    imported = target.import_data(payload)
    assert imported == 2

    # 主列表只剩已映射那条.
    active, total = target.list()
    assert total == 1 and active[0].original == "中出し"
    assert active[0].translation == "中出"
    # 归档那条带回了归档状态.
    archived, archived_total = target.list(archived=True)
    assert archived_total == 1
    assert archived[0].original == "巨乳"
    assert target.counts()["archived"] == 1


def test_export_omits_local_statistics(tmp_path) -> None:
    """导出只带决定 (译文/语言/归档), 不带本机的计数与来源."""
    from amane.tags.mapping import TagMappingStore

    store = TagMappingStore(tmp_path)
    store.seed([("中出し", 10)])
    store.upsert("中出し", "中出")

    entry = store.export()["entries"]["中出し"]
    assert entry["translation"] == "中出"
    assert entry["archived"] is False
    assert "usage_count" not in entry, "计数是本机统计, 不该随词典传给别人"
    assert "sources" not in entry, "来源是本机抓取记录, 不该随词典传给别人"


def test_import_keeps_local_statistics(tmp_path) -> None:
    """导入别人的词典不改本机的计数与来源; 旧格式里带着这两项也跳过."""
    from amane.tags.mapping import TagMappingStore

    local = TagMappingStore(tmp_path)
    local.seed([("中出し", 42)])
    local.observe(["中出し"], source="javdb")
    # observe 会把计数 +1, 记下导入前的真实值用于比较.
    before = local.list(status="all")[0][0].usage_count

    # 模拟别人机器上的导出 (旧格式: 带 usage_count 与 sources).
    foreign = {
        "version": 1,
        "entries": {
            "中出し": {
                "original": "中出し",
                "translation": "中出",
                "language": "ja",
                "usage_count": 999,
                "sources": ["别人的站点"],
                "archived": True,
            }
        },
    }
    local.import_data(foreign)

    entry = local.list(archived=True)[0][0]
    assert entry.translation == "中出"  # 决定被采纳
    assert entry.archived is True
    assert entry.usage_count == before, "本机计数不该被对方覆盖"
    assert "javdb" in (entry.sources or []), "本机来源不该被对方覆盖"
    assert "别人的站点" not in (entry.sources or [])


def test_mapped_entry_cannot_be_archived(tmp_path) -> None:
    """已映射的不许归档: 归档区表示「不需要译名」, 带着译名进去自相矛盾."""
    from amane.tags.mapping import TagMappingStore

    store = TagMappingStore(tmp_path)
    store.seed([("中出し", 10)])
    store.upsert("中出し", "中出")

    assert store.delete("中出し") is False
    # 状态没变.
    entry = store.get("中出し")
    assert entry is not None
    assert entry.archived is False
    assert entry.translation == "中出"

    # 清空译名后就能归档.
    store.upsert("中出し", None)
    assert store.delete("中出し") is True
    archived = store.get("中出し")
    assert archived is not None and archived.archived is True
