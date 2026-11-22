import pytest

from amane.tags.mapping import MappingGroup, TagMappingStore, detect_language

LANGUAGE_CASES = [
    # 假名是日文的硬特征.
    ("中出し", "ja"),
    ("カリビアンコム", "ja"),
    # 纯汉字分不出中日, 一律按中文算.
    ("内射", "zh"),
    ("朝桐光", "zh"),
    ("美乳", "zh"),
    # 汉字优先于拉丁字母: 混着英文的汉字词条仍以中文为主.
    ("3P中出し", "ja"),
    ("Mカップ美乳", "ja"),
    ("VR美乳", "zh"),
    # 有拉丁字母但带数字的是技术标记, 没有可译语义: 判成英文只会污染那一档.
    ("Creampie", "en"),
    ("Beautiful", "en"),
    ("1080p", "other"),
    ("60fps", "other"),
    ("3P", "other"),
    ("4K", "other"),
    ("FC2", "other"),
    # 什么都没有的也算其他.
    ("123", "other"),
    ("", "other"),
]


@pytest.mark.parametrize(("value", "expected"), LANGUAGE_CASES)
def test_language_detection(value, expected) -> None:
    """语言只用于分组展示; 汉字一律算中文, 不让纯汉字词条落进未知."""
    assert detect_language(value) == expected


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


def test_tag_pairs_keep_raw_and_shown_side_by_side(tmp_path) -> None:
    """成对返回时, 同一译文下的两个原名都能保住各自的那一条.

    这正是只返回 ``{译文: 原名}`` 做不到的: ``中出し`` 与 ``Creampie`` 都译作「内射」,
    反查表只能留下一个, 详情页切回原文时另一条就没了原名.
    """
    store = TagMappingStore(tmp_path)
    store.seed([("中出し", 10), ("Creampie", 8), ("制服", 4)])
    store.upsert("中出し", "内射")
    store.upsert("Creampie", "内射")

    pairs = store.map_tag_pairs(["中出し", "Creampie", "制服"])

    # 去重按展示名: 「内射」只占一行, 保留先出现的 ``中出し``; 「制服」未翻译, 两者相同.
    assert pairs == [("中出し", "内射"), ("制服", "制服")]


def test_tag_pairs_are_ordered_like_input(tmp_path) -> None:
    store = TagMappingStore(tmp_path)
    store.seed([("A", 3), ("B", 2)])
    store.upsert("B", "乙")

    assert store.map_tag_pairs(["B", "A"]) == [("B", "乙"), ("A", "A")]


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


SYNC_CASES: list[tuple[str, list[tuple[str, int]], dict[str, int], set[str]]] = [
    # 分类表里消失的自动条目连同译名一起删.
    ("dropped", [("中出し", 10)], {"中出し": 10}, {"中出し"}),
    # 引用数归零也算不存在: 留一条「0 次」既没用也碍眼.
    ("zero", [("中出し", 10), ("巨乳", 0), ("美脚", 3)], {"中出し": 10, "美脚": 3}, {"中出し", "美脚"}),
    # 计数变化要跟上.
    ("count", [("中出し", 7)], {"中出し": 7}, {"中出し"}),
]


@pytest.mark.parametrize(("case", "values", "counts", "kept"), SYNC_CASES)
def test_sync_drops_automatic_entries(tmp_path, case, values, counts, kept) -> None:
    """对账只保留分类表里还在、且还有人用的自动条目."""
    from amane.tags.mapping import TagMappingStore

    store = TagMappingStore(tmp_path)
    store.seed([("中出し", 1), ("巨乳", 2), ("美脚", 3)])
    store.upsert("巨乳", "巨乳译名")

    store.seed(values)

    survivors = {entry.original: entry.usage_count for entry in store.list(status="all")[0]}
    assert survivors == counts, case
    assert set(survivors) == kept, case
    assert store.map_tags(["巨乳"]) == ["巨乳"], "译名随条目一起删掉, 不再参与映射"


def test_sync_writes_and_refreshes_sources(tmp_path) -> None:
    """对账顺带把来源对齐: 来源从条目上读出来, 攒进映射表省得每次展示都回查.

    没再出现在新结果里的来源要清掉, 别留着已经改投别处的旧站点.
    """
    from amane.tags.mapping import TagMappingStore

    store = TagMappingStore(tmp_path)
    store.sync([("中出し", 5)], sources={"中出し": ["aventertainments", "tokyo_hot"]})
    entry = store.get("中出し")
    assert entry is not None
    assert entry.sources == ["aventertainments", "tokyo_hot"]

    store.sync([("中出し", 5)], sources={"中出し": ["tokyo_hot"]})
    refreshed = store.get("中出し")
    assert refreshed is not None
    assert refreshed.sources == ["tokyo_hot"]

    # 新条目也带来源; 没给来源时为空, 不沿用别人的.
    store.sync([("中出し", 5), ("巨乳", 2)], sources={"中出し": ["tokyo_hot"]})
    created = store.get("巨乳")
    assert created is not None
    assert created.sources == []


def test_sync_leaves_manual_sources_alone(tmp_path) -> None:
    """手动条目不是对账建出来的, 来源也由用户自己管, 对账不去动它."""
    from amane.tags.mapping import TagMappingStore

    store = TagMappingStore(tmp_path)
    store.upsert("手填标签", "手填译名")
    manual = store.get("手填标签")
    assert manual is not None
    manual.sources = ["手填来源"]

    store.sync([], sources={})

    kept = store.get("手填标签")
    assert kept is not None
    assert kept.sources == ["手填来源"]


def test_sync_keeps_manual_entries(tmp_path) -> None:
    """手动新增的条目不受对账删除: 词典本就可能指向还没入库的名字."""
    from amane.tags.mapping import TagMappingStore

    store = TagMappingStore(tmp_path)
    store.upsert("手填标签", "手填译名")
    entry = store.get("手填标签")
    assert entry is not None and entry.manual is True

    store.sync([])

    kept = store.get("手填标签")
    assert kept is not None and kept.translation == "手填译名"


def test_imported_entries_are_manual(tmp_path) -> None:
    """导入的词典也按手动条目处理, 不被对账删掉."""
    store = TagMappingStore(tmp_path)
    store.import_data({"entries": {"外部词条": {"translation": "外部译名"}}})

    store.sync([])

    entry = store.get("外部词条")
    assert entry is not None
    assert entry.translation == "外部译名"
    assert entry.manual is True


GROUP_CASES = [
    (MappingGroup.ACTOR, "彩月七緒", "彩月七绪"),
    (MappingGroup.VENDOR, "S1", "S1 制作商"),
]


@pytest.mark.parametrize(("group", "original", "translation"), GROUP_CASES)
def test_name_mapping_stores_share_one_behaviour(tmp_path, group, original, translation) -> None:
    """演员与片商共用同一套存储: 写入 / 展示 / 归档 / 对账删除."""
    from amane.tags import MAPPING_FILES, MappingRegistry

    registry = MappingRegistry(tmp_path)
    store = registry.store(group)

    # 自动条目 (对账建出来的) 在分类表里消失时连同译名一起删.
    store.sync([(original, 3)])
    store.upsert(original, translation)
    assert registry.display(group, original) == translation
    # 每组一份文件, 互不覆盖.
    assert store.path == tmp_path / MAPPING_FILES[group]

    store.sync([])
    assert store.get(original) is None, "分类表里没有的自动条目要对账删掉"

    # 手动新增的条目 (分类表里本来就没有) 归档后仍在映射里生效.
    store.upsert(original, translation)
    assert store.get(original) is not None
    store.upsert(original, None)
    assert store.delete(original) is True
    assert registry.display(group, original) == original
    archived = store.get(original)
    assert archived is not None and archived.archived is True


def test_manual_entries_survive_sync_even_after_edit(tmp_path) -> None:
    """手动条目改过译名之后仍然不被对账删掉."""
    from amane.tags import MappingGroup, MappingRegistry

    registry = MappingRegistry(tmp_path)
    store = registry.store(MappingGroup.ACTOR)
    store.upsert("手填演员", "手填译名")
    store.upsert("手填演员", "改过的译名")

    store.sync([])

    entry = store.get("手填演员")
    assert entry is not None
    assert entry.translation == "改过的译名"
    assert entry.manual is True


def test_vendor_table_merges_same_name_across_roles(tmp_path) -> None:
    """制作商与发行商同名的只占一行, 用 roles 记住它属于哪几类.

    同一个词本来就同一次翻译; 分开存会让它有两份译名, 迟早对不上.
    """
    from amane.tags import MappingGroup, MappingRegistry, VendorRole

    registry = MappingRegistry(tmp_path)
    store = registry.store(MappingGroup.VENDOR)
    store.sync_roles(
        [
            (str(VendorRole.STUDIO), "S1", 4),
            (str(VendorRole.PUBLISHER), "S1", 4),
            (str(VendorRole.SERIES), "S1 系列", 2),
        ]
    )

    assert len(store.list(status="all")[0]) == 2, "同名条目只占一行"
    merged = store.get("S1")
    assert merged is not None
    assert sorted(merged.roles) == ["publisher", "studio"]

    # 一份译名对两个分类同时生效.
    store.upsert("S1", "S1 译名")
    roots = registry.vendor_roots()
    assert roots[VendorRole.STUDIO]["S1"] == "S1 译名"
    assert roots[VendorRole.PUBLISHER]["S1"] == "S1 译名"
    assert "S1" not in roots[VendorRole.SERIES]


def test_vendor_sync_drops_names_gone_from_every_role(tmp_path) -> None:
    """三分类都不再有这个名字时才删; 还属于其中一类就留着."""
    from amane.tags import MappingGroup, MappingRegistry, VendorRole

    registry = MappingRegistry(tmp_path)
    store = registry.store(MappingGroup.VENDOR)
    store.sync_roles(
        [
            (str(VendorRole.STUDIO), "S1", 4),
            (str(VendorRole.PUBLISHER), "S1", 4),
            (str(VendorRole.SERIES), "系列甲", 2),
        ]
    )

    # 发行商那边没了, 制作商还在: 条目留下, roles 只留 studio.
    store.sync_roles(
        [
            (str(VendorRole.STUDIO), "S1", 4),
            (str(VendorRole.SERIES), "系列甲", 2),
        ]
    )
    kept = store.get("S1")
    assert kept is not None
    assert kept.roles == [str(VendorRole.STUDIO)]

    # 制作商也没了: 连同译名一起删.
    store.sync_roles([(str(VendorRole.SERIES), "系列甲", 2)])
    assert store.get("S1") is None


def test_legacy_vendor_files_are_absorbed(tmp_path) -> None:
    """合表之前的三份文件在首次加载时并进来, 同名合成一条."""
    from amane.tags import LEGACY_VENDOR_FILES, MappingGroup, MappingRegistry, VendorRole

    (tmp_path / LEGACY_VENDOR_FILES[VendorRole.STUDIO]).write_text(
        '{"version": 1, "entries": {"S1": {"translation": "S1 制作商"}}}', encoding="utf-8"
    )
    (tmp_path / LEGACY_VENDOR_FILES[VendorRole.PUBLISHER]).write_text(
        '{"version": 1, "entries": {"S1": {"translation": "S1 制作商"}}}', encoding="utf-8"
    )
    (tmp_path / LEGACY_VENDOR_FILES[VendorRole.SERIES]).write_text(
        '{"version": 1, "entries": {"系列甲": {"translation": "系列甲译名"}}}', encoding="utf-8"
    )

    registry = MappingRegistry(tmp_path)
    store = registry.store(MappingGroup.VENDOR)

    s1 = store.get("S1")
    assert s1 is not None
    assert s1.translation == "S1 制作商"
    assert sorted(s1.roles) == ["publisher", "studio"]
    assert registry.vendor_roots()[VendorRole.SERIES]["系列甲"] == "系列甲译名"


def test_rename_carries_translation(tmp_path) -> None:
    """分类实体改名后译名跟着走, 否则界面退回原文."""
    from amane.tags import MappingGroup, MappingRegistry

    registry = MappingRegistry(tmp_path)
    store = registry.store(MappingGroup.ACTOR)
    store.upsert("旧名", "旧译名")

    registry.rename(MappingGroup.ACTOR, "旧名", "新名")

    assert store.get("旧名") is None
    assert registry.display(MappingGroup.ACTOR, "新名") == "旧译名"

    # 新名已有自己的映射时保留对方: 不能覆盖用户已经做过的决定.
    store.upsert("另一个旧名", "旧译名2")
    store.upsert("另一个新名", "新译名")
    registry.rename(MappingGroup.ACTOR, "另一个旧名", "另一个新名")
    assert registry.display(MappingGroup.ACTOR, "另一个新名") == "新译名"


def test_forget_keeps_manual_entries(tmp_path) -> None:
    """分类被真删后只清自动映射.

    手动条目与导入的词典是用户自己做过的决定, 与分类表里还有没有这个名字无关 ——
    词典本就可能指向还没入库的名字.
    """
    from amane.tags import MappingGroup, MappingRegistry

    registry = MappingRegistry(tmp_path)
    store = registry.store(MappingGroup.VENDOR)
    # 自动条目 (对账建出来的) 跟着分类一起消失.
    store.sync_roles([("series", "被删系列", 3)])
    # 手动条目不受影响.
    store.upsert("手填系列", "手填系列译名")

    registry.forget(MappingGroup.VENDOR, "被删系列")
    registry.forget(MappingGroup.VENDOR, "手填系列")

    assert store.get("被删系列") is None
    kept = store.get("手填系列")
    assert kept is not None
    assert kept.translation == "手填系列译名"


IMPORTED_KEYS = ["导入条目"]


def test_forget_keeps_imported_entries(tmp_path) -> None:
    """导入的词典同样不被分类删除带走."""
    from amane.tags import MappingGroup, MappingRegistry

    registry = MappingRegistry(tmp_path)
    store = registry.store(MappingGroup.ACTOR)
    store.import_data({"entries": {name: {"translation": "导入译名"} for name in IMPORTED_KEYS}})

    registry.forget(MappingGroup.ACTOR, IMPORTED_KEYS[0])

    kept = store.get(IMPORTED_KEYS[0])
    assert kept is not None
    assert kept.translation == "导入译名"


PUBLISHER_CASES = [
    # (制作商, 发行商, 系列, 期望发行商)
    ("S1", None, None, "S1"),
    ("S1", "发行商", None, "发行商"),
    (None, "发行商", None, "发行商"),
    (None, None, None, None),
    # 系列不受「缺发行商按制作商补齐」影响.
    ("S1", None, "我的女友", "S1"),
]


@pytest.mark.parametrize(("studio", "publisher", "series", "expected"), PUBLISHER_CASES)
def test_vendor_names_fall_back_to_studio(studio, publisher, series, expected) -> None:
    """没有发行商时制作商就是发行商; 系列单独一条, 不跟着补."""
    from amane.db.models import Metadata
    from amane.tags import vendor_names

    meta = Metadata(number="X-1", studio=studio, publisher=publisher, series=series)

    names = vendor_names(meta)

    assert names.publisher == expected
    assert names.studio == studio
    assert names.series == ([series] if series else [])


def test_vendor_names_translate_through_mappings(tmp_path) -> None:
    """展示值走映射; 没有译名的保持原名.

    片商三分类共用一个文件, 但一条译名只对它所属的分类生效 (没有 ``roles`` 的手动条目对三类都生效).
    """
    from amane.db.models import Metadata
    from amane.tags import MAPPING_FILES, MappingGroup, VendorRole, vendor_names, vendor_roots

    (tmp_path / MAPPING_FILES[MappingGroup.VENDOR]).write_text(
        '{"version": 1, "entries": {'
        '"カリビアンコム": {"translation": "加勒比", "roles": ["publisher"]},'
        '"未知系列": {"translation": "未知系列译名"}'
        "}}",
        encoding="utf-8",
    )
    meta = Metadata(number="X-2", studio="未知片商", publisher="カリビアンコム", series="未知系列")

    roots = vendor_roots(tmp_path)
    shown = vendor_names(meta).translated(roots)

    assert shown.publisher == "加勒比"
    assert shown.studio == "未知片商"
    assert shown.series == ["未知系列译名"]
    # 带 roles 的条目只在所属分类里生效.
    assert "カリビアンコム" not in roots[VendorRole.STUDIO]
