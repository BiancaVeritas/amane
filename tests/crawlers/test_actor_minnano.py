"""minnano 演员名的拆分规则, 以及 サイズ 行在缺项时的取数.

真实页面在 ``cases/minnano`` (amane-testdata): 读音被填进站点名、带注记的别名与空读音各一例.
这里只覆盖无法用整页用例表达的边界, 以及没有名字时返回 ``None`` 的守卫.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest
from parsel import Selector

from amane.crawlers.actor.sites.minnano import (
    MinnanoActorCrawler,
    _parse_figure,
    _parse_name_line,
    parse_minnano_detail,
)
from amane.net.errors import SourceError

# (id, 原始名字行, 同页标签, 期望展示名, 期望读音)
_NAME_CASES: list[tuple[str, str, frozenset[str], str, list[str]]] = [
    ("无括号", "三上悠亜", frozenset(), "三上悠亜", []),
    ("首尾空白", "  三上悠亜  ", frozenset(), "三上悠亜", []),
    ("空串", "", frozenset(), "", []),
    # 括号不在尾部时不拆分
    ("中段括号", "A (B) C", frozenset(), "A (B) C", []),
    # 整串只有括号组: 没有名字
    ("只有括号组", "（AV女優）", frozenset(), "", []),
    # 注记在前、读音在后: 只有最右边一组是读音
    (
        "注记加读音",
        "山岡志織(天然むすめ) （やまおかしおり / Yamaoka Shiori）",
        frozenset({"天然むすめ"}),
        "山岡志織",
        ["やまおかしおり", "Yamaoka Shiori"],
    ),
    # 括号里只有一个假名: 当读音收下
    ("括号内单段假名", "莉愛（りあ）", frozenset(), "莉愛", ["りあ"]),
    # 注记括号不并入读音
    ("注记括号", "莉愛（ガチん娘）", frozenset({"ガチん娘"}), "莉愛", []),
    # 读音的位置被填进站点名: 不像读音的按字形丢弃, 纯假名的只能靠同页标签判定
    ("读音位置汉字站点名", "わかな （天然むすめ / Wakana）", frozenset(), "わかな", ["Wakana"]),
    ("读音位置假名站点名", "わかな （しろハメ / Wakana）", frozenset({"しろハメ"}), "わかな", ["Wakana"]),
    ("读音位置无标签对照", "わかな （しろハメ / Wakana）", frozenset(), "わかな", ["しろハメ", "Wakana"]),
    # 读音的位置带斜杠的注记按段拆开
    ("读音位置多段", "きみか （きみか/ガチん娘 / Kimika）", frozenset({"ガチん娘"}), "きみか", ["きみか", "Kimika"]),
    # 读音缺失
    ("空读音", "久美(FC2) （ / ）", frozenset({"FC2"}), "久美", []),
]


@pytest.mark.parametrize(
    ("raw", "tags", "expected_name", "expected_readings"),
    [case[1:] for case in _NAME_CASES],
    ids=[case[0] for case in _NAME_CASES],
)
def test_parse_name_line(
    raw: str,
    tags: frozenset[str],
    expected_name: str,
    expected_readings: list[str],
) -> None:
    assert _parse_name_line(raw, tags) == (expected_name, expected_readings)


# (id, サイズ原文, 期望 (身高, 胸围, 腰围, 臀围, 罩杯)); 缺项的标号仍留在行内.
_FIGURE_CASES: list[tuple[str, str, tuple[int | None, int | None, int | None, int | None, str | None]]] = [
    ("完整", "T164 / B94(Fカップ) / W58 / H89 / S", (164, 94, 58, 89, "F")),
    ("鞋码尾段", "T149 / B79(Hカップ) / W52 / H78 / S21", (149, 79, 52, 78, "H")),
    ("身高空", "T / B83 / W61 / H89 / S", (None, 83, 61, 89, None)),
    ("三围空", "T150 / B(Dカップ) / W / H / S", (150, None, None, None, "D")),
    ("腰围空", "T160 / B86 / W / H88 / S", (160, 86, None, 88, None)),
    ("全空", "T / B / W / H / S", (None, None, None, None, None)),
    ("空串", "", (None, None, None, None, None)),
    ("全角", "Ｔ１６４ ／ Ｂ９４（Ｆカップ）／ Ｗ５８ ／ Ｈ８９", (164, 94, 58, 89, "F")),
    ("无标号", "164 / 94 / 58 / 89", (None, None, None, None, None)),
]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [case[1:] for case in _FIGURE_CASES],
    ids=[case[0] for case in _FIGURE_CASES],
)
def test_parse_figure(
    raw: str,
    expected: tuple[int | None, int | None, int | None, int | None, str | None],
) -> None:
    assert _parse_figure(raw) == expected


def test_parse_detail_without_profile() -> None:
    assert (
        parse_minnano_detail("<html><body></body></html>", page_url="u", base_url="https://www.minnano-av.com") is None
    )


def test_parse_detail_without_name() -> None:
    page = '<div class="act-profile"><table><tr><td><h2>（AV女優）</h2></td></tr></table></div>'
    assert parse_minnano_detail(page, page_url="u", base_url="https://www.minnano-av.com") is None


def test_parse_figure_keeps_individual_measurements_and_standalone_cup() -> None:
    assert _parse_figure("B70") == (None, 70, None, None, None)
    assert _parse_figure("Cカップ") == (None, None, None, None, "C")
    assert _parse_figure("B70 / Cカップ / W52") == (None, 70, 52, None, "C")


@pytest.mark.asyncio
async def test_search_uses_only_exact_standard_name_mapping_before_site_search(tmp_path):
    mapping_dir = tmp_path / "mappings"
    mapping_dir.mkdir()
    (mapping_dir / "minnano_actress_map.json").write_text(
        json.dumps({"725921": {"name": "長谷川杏実", "aliases": ["長谷川みお"]}}, ensure_ascii=False),
        encoding="utf-8",
    )

    class NeverCalledClient:
        async def get_html(self, *args, **kwargs):
            raise AssertionError("mapped primary name should bypass website search")

    crawler = MinnanoActorCrawler(NeverCalledClient(), data_dir=tmp_path)
    assert await crawler._search("長谷川杏実") == "https://www.minnano-av.com/actress725921.html"


@pytest.mark.asyncio
async def test_search_alias_does_not_use_mapping(tmp_path):
    mapping_dir = tmp_path / "mappings"
    mapping_dir.mkdir()
    (mapping_dir / "minnano_actress_map.json").write_text(
        json.dumps({"725921": {"name": "長谷川杏実", "aliases": ["長谷川みお"]}}, ensure_ascii=False),
        encoding="utf-8",
    )

    class EmptySearchClient:
        async def get_html(self, url, **kwargs):
            assert parse_qs(urlparse(url).query)["search_word"] == ["長谷川みお"]
            return "<html><body></body></html>"

    crawler = MinnanoActorCrawler(EmptySearchClient(), data_dir=tmp_path)
    with pytest.raises(SourceError, match="网站检索"):
        await crawler._search("長谷川みお")


@pytest.mark.asyncio
async def test_search_rejects_multiple_mapping_ids(tmp_path):
    mapping_dir = tmp_path / "mappings"
    mapping_dir.mkdir()
    (mapping_dir / "minnano_actress_map.json").write_text(
        json.dumps({"1": {"name": "同名", "aliases": []}, "2": {"name": "同名", "aliases": []}}, ensure_ascii=False),
        encoding="utf-8",
    )

    class NeverCalledClient:
        async def get_html(self, *args, **kwargs):
            raise AssertionError("ambiguous mapping must not fall back to website search")

    crawler = MinnanoActorCrawler(NeverCalledClient(), data_dir=tmp_path)
    with pytest.raises(SourceError, match="多个 ID"):
        await crawler._search("同名")


def test_pick_search_hits_requires_unique_exact_result():
    crawler = MinnanoActorCrawler(object())
    html = Selector(
        text="""<table class="tbllist actress">
      <tr><td><h2 class="ttl"><a href="actress1.html">CCC</a></h2></td></tr>
      <tr><td><h2 class="ttl"><a href="actress2.html">AAACCC</a></h2></td></tr>
      <tr><td><h2 class="ttl"><a href="actress3.html">CCC</a></h2></td></tr>
    </table>"""
    )
    assert crawler._pick_search_hits(html, "CCC") == [
        "https://www.minnano-av.com/actress1.html",
        "https://www.minnano-av.com/actress3.html",
    ]
