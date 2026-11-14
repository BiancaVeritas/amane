"""minnano 的离线演员检索表.

站点的 ``search_result.php`` 处于 Cloudflare 挑战之后 (403), 而演员列表页与详情页不设防,
所以索引靠整表枚举生成. 表里只有「主名与别名」, 生日三围等资料一律现抓网页.

查人可以落到多个人身上: 站点上只留假名或常见姓名的条目很多, 同一个名字会挂在不同的 id 下.
因此查找分两步 —— 先只按主名定位, 主名不中才退到别名; 候选仍不唯一时交给抓取侧比较资料.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from amane.crawlers.actor.sites.minnano import (
    MinnanoActorCrawler,
    _actress_table,
    _profile_completeness,
    lookup_actress_id,
    lookup_actress_ids,
    match_actress,
)

BASE = "https://www.minnano-av.com"
#: 橘めぐみ: 主名之外还有真别名 (不同时期的艺名), 两者都要能查到同一个 id.
ID = "100025"
NAME = "橘めぐみ"
ALIAS = "宮嶋のどか"


def test_index_is_embedded() -> None:
    """索引随包分发; 缺失会让整条检索退回被拦的在线通路."""
    assert len(_actress_table()) > 10_000


def test_index_separates_primary_name_from_aliases() -> None:
    """主名与别名必须分开存.

    扁平表在建表时就把这个区别丢了, 而查人要先只按主名判断 —— 混在一起会让
    「主名精确命中」与「别人的别名命中」权重相同.
    """
    entry = _actress_table()[ID]
    assert entry["name"] == NAME
    assert ALIAS in entry["aliases"]  # type: ignore[operator]
    assert entry["name"] != ALIAS


def test_lookup_actress_id_returns_none_when_ambiguous() -> None:
    """同名挂在多个 id 下时返回 None, 不随便挑一个.

    站点上常见「A 的主名 = B 的别名」这类交叉, 只按第一个命中选会把两个人的资料混成
    一个. 这种情况必须交给候选比较.
    """
    assert lookup_actress_id(NAME) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("完全不存在的名字", None),
        ("", None),
    ],
)
def test_lookup_actress_id_misses(value: str, expected: str | None) -> None:
    assert lookup_actress_id(value) == expected


def test_lookup_ignores_case_and_spaces() -> None:
    """站点名字有全角空格与大小写差异, 宽松匹配仍应唯一命中."""
    assert lookup_actress_id(f" {NAME} ") == lookup_actress_id(NAME)


def test_reading_brackets_are_not_searchable() -> None:
    """读音括号与厂牌标注在建表时剥掉, 不该留下带括号的键."""
    assert lookup_actress_id(f"{NAME} （たちばなめぐみ / Tachibana Megumi）") is None
    for entry in _actress_table().values():
        for key in [str(entry["name"]), *(str(a) for a in entry["aliases"])]:  # type: ignore[union-attr]
            assert "（" not in key and "(" not in key


def test_match_prefers_primary_name_over_alias() -> None:
    """主名唯一命中时直接用, 不再看别名.

    主名精确命中却因为某个别名也命中别人而变得不确定, 会把主名优先的意图抹掉.
    取一个只在主名出现、别名里没有的名字来验证.
    """
    table = _actress_table()
    by_name: dict[str, list[str]] = {}
    for actress_id, entry in table.items():
        name = str(entry["name"]).strip()
        if name:
            by_name.setdefault(name, []).append(actress_id)
    unique_name = next(
        name
        for name, ids in by_name.items()
        if len(ids) == 1 and not any(name in entry["aliases"] for entry in table.values())  # type: ignore[operator]
    )
    expected = by_name[unique_name][0]

    unique, candidates = match_actress([unique_name])

    assert unique == expected
    assert candidates == []


def test_match_falls_back_to_alias_when_primary_misses() -> None:
    """主名查不到时才退到别名."""
    assert lookup_actress_id(ALIAS) == ID

    unique, candidates = match_actress(["完全不存在的名字", ALIAS])

    assert unique == ID
    assert candidates == []


def test_match_returns_candidates_when_ambiguous() -> None:
    """一个名字挂在多个 id 下时不瞎猜, 把候选交出去比资料."""
    table = _actress_table()
    by_name: dict[str, list[str]] = {}
    for actress_id, entry in table.items():
        name = str(entry["name"]).strip()
        if name:
            by_name.setdefault(name, []).append(actress_id)
    duplicated = next(name for name, ids in by_name.items() if len(ids) > 1)

    unique, candidates = match_actress([duplicated])

    assert unique is None
    assert sorted(candidates) == sorted(by_name[duplicated])


def test_lookup_actress_ids_spans_name_and_alias() -> None:
    """不分主名别名的原始查询: 主名与别名都算, 交叉命中会返回多个 id."""
    # ID 的主名是 NAME; 另一个 id 把 NAME 收作别名 —— 两个都要出现.
    assert ID in lookup_actress_ids(NAME)
    assert len(lookup_actress_ids(NAME)) >= 2
    assert ID in lookup_actress_ids(ALIAS)


def test_profile_completeness_counts_filled_fields() -> None:
    """「更全」按实际填了几项来数, 不按字段顺序."""
    from amane.crawlers.actor.sites.minnano import ActorMetadata

    sparse = ActorMetadata(name="A", birthday=None, birthplace=None, height=160)
    rich = ActorMetadata(
        name="B",
        birthday="2000-06-30",
        birthplace="東京都",
        height=165,
        bust=112,
        waist=64,
        hip=99,
        cup="J",
        aliases=["別名1"],
    )

    assert _profile_completeness(rich) > _profile_completeness(sparse)


@pytest.mark.asyncio
async def test_search_uses_local_index_without_touching_search_page() -> None:
    """唯一命中索引时不应请求搜索页 —— 那个入口必然被挑战拦下."""
    table = _actress_table()
    by_name: dict[str, list[str]] = {}
    for actress_id, entry in table.items():
        name = str(entry["name"]).strip()
        if name:
            by_name.setdefault(name, []).append(actress_id)
    unique_name = next(name for name, ids in by_name.items() if len(ids) == 1)
    expected = by_name[unique_name][0]

    crawler = MinnanoActorCrawler(client=AsyncMock(), config=None)
    url = await crawler._search(unique_name)

    assert url == f"{BASE}/actress{expected}.html"
    crawler.client.get_html.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_fetch_names_picks_the_richer_profile() -> None:
    """多候选时抓网页比资料, 取字段更全的那个."""
    sparse_html = """
    <div class="act-profile"><table>
      <tr><td><h2>同名的人</h2></td></tr>
      <tr><td><span>サイズ</span><p>T155 / B80(Aカップ) / W60 / H85</p></td></tr>
    </table></div>
    """
    rich_html = """
    <div class="act-profile"><table>
      <tr><td><h2>同名的人</h2></td></tr>
      <tr><td><span>生年月日</span><p>2000年06月30日</p></td></tr>
      <tr><td><span>出身地</span><p>東京都</p></td></tr>
      <tr><td><span>サイズ</span><p>T165 / B112(Jカップ) / W64 / H99</p></td></tr>
    </table></div>
    """

    client = AsyncMock()
    crawler = MinnanoActorCrawler(client=client, config=None)
    real_scrape = MinnanoActorCrawler._scrape

    async def fake(url: str):
        # 两个候选里第一个资料少, 第二个资料全.
        client.get_html.return_value = sparse_html if url.endswith("111.html") else rich_html
        return await real_scrape(crawler, url)

    crawler._scrape = fake  # type: ignore[method-assign]

    # 直接测多候选比较: 伪造一个「主名撞两个人」的查询结果.
    import amane.crawlers.actor.sites.minnano as minnano_module

    original = minnano_module.match_actress
    minnano_module.match_actress = lambda _names: (None, ["111", "222"])  # type: ignore[assignment]
    try:
        meta = await crawler.fetch_names([NAME, ALIAS])
    finally:
        minnano_module.match_actress = original  # type: ignore[assignment]

    assert meta is not None
    assert meta.height == 165
    assert meta.birthday == "2000-06-30"


@pytest.mark.asyncio
async def test_scrape_always_reads_the_web_page() -> None:
    """资料必须现抓: 本地只留检索表, 不再有可用于兜底的资料快照."""
    html = """
    <div class="act-profile"><table>
      <tr><td><h2>橘めぐみ （たちばなめぐみ / Tachibana Megumi）</h2></td></tr>
      <tr><td><span>別名</span><p>宮嶋のどか</p></td></tr>
      <tr><td><span>生年月日</span><p>2000年06月30日</p></td></tr>
      <tr><td><span>サイズ</span><p>T165 / B112(Jカップ) / W64 / H99</p></td></tr>
    </table></div>
    """
    client = AsyncMock()
    client.get_html.return_value = html
    crawler = MinnanoActorCrawler(client=client, config=None)

    meta = await crawler._scrape(f"{BASE}/actress{ID}.html")

    assert meta is not None
    assert meta.name == NAME
    assert ALIAS in meta.aliases
    assert (meta.height, meta.bust, meta.waist, meta.hip, meta.cup) == (165, 112, 64, 99, "J")
    client.get_html.assert_awaited_once()


def test_mutual_links_rejects_one_way_alias() -> None:
    """单向不算: A 的主名只是 B 的别名时, 是两个人共用了一个名字, 不能当同一人."""
    from amane.crawlers.actor.sites.minnano import _mutual_links

    # 100025 主名=橘めぐみ; 325744 把橘めぐみ收作别名但主名是别人 -> 单向.
    assert _mutual_links({"100025", "325744"}) == set()


def test_mutual_links_ignores_same_primary_without_aliases() -> None:
    """只有主名相同、别名为空时不能互认.

    空集恒相等, 若把「别名集相同」当成证据, 所有只留主名的同名者都会被并成一个人.
    沙耶 的三条记录就是这样, 必须判为不同人.
    """
    from amane.crawlers.actor.sites.minnano import _mutual_links

    assert _mutual_links({"101402", "14055", "924634"}) == set()


def test_mutual_links_accepts_identical_records() -> None:
    """主名与别名都相同的两条记录是同一个人.

    站点会为同一个人存两条, 两条没有交叉引用可看, 只能靠「名字集完全一致」认出来.
    1180 / 386206 就是这种情况, 差别只在谁填的资料更全.
    """
    from amane.crawlers.actor.sites.minnano import _mutual_links

    assert _mutual_links({"1180", "386206"}) == {"1180", "386206"}


def test_match_keeps_candidates_for_identical_records() -> None:
    """同名同别名的两条都留下, 由抓取侧比资料完整度, 取更全的那份.

    从索引里取真实主名, 不手写字面量 —— 站点名字混用假名, 抄错一个字符就测不到.
    """
    name = str(_actress_table()["1180"]["name"])

    unique, candidates = match_actress([name])

    assert unique is None
    assert sorted(candidates) == ["1180", "386206"]


def test_match_keeps_candidates_for_homonyms() -> None:
    """同名不同人时把候选全部交出去, 由抓取侧比资料, 不随便挑一个."""
    unique, candidates = match_actress(["沙耶"])

    assert unique is None
    assert sorted(candidates) == ["101402", "14055", "924634"]


def test_match_uses_primary_when_one_way_alias_exists() -> None:
    """主名唯一命中时, 别人的单向别名不影响结果."""
    unique, candidates = match_actress(["橘めぐみ"])

    assert unique == "100025"
    assert candidates == []
