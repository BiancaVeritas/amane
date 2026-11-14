"""minnano 的离线演员检索表.

站点的 ``search_result.php`` 处于 Cloudflare 挑战之后 (403), 而演员列表页与详情页不设防,
所以索引靠整表枚举生成. 这里验证查表本身: 命中时不应该发起任何网络请求.

表里只有「名字与别名 → id」这一件事. 生日三围等资料一律现抓网页, 不在本地留快照 ——
快照会过期, 也会漏收字段. 站点附在名字后面的读音括号与厂牌标注在建表时已剥掉.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from amane.crawlers.actor.sites.minnano import (
    MinnanoActorCrawler,
    _name_index,
    lookup_actress_id,
)

BASE = "https://www.minnano-av.com"
#: 橘めぐみ: 主名之外还有真别名 (不同时期的艺名), 两者都要能查到同一个 id.
ID = "100025"
NAME = "橘めぐみ"
ALIAS = "宮嶋のどか"


def test_index_is_embedded() -> None:
    """索引随包分发; 缺失会让整条检索退回被拦的在线通路."""
    assert len(_name_index()) > 10_000


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # 主名与真别名指向同一个 id.
        (NAME, ID),
        (ALIAS, ID),
        # 查不到就是 None, 不瞎猜.
        ("完全不存在的名字", None),
        ("", None),
    ],
)
def test_lookup_actress_id(value: str, expected: str | None) -> None:
    assert lookup_actress_id(value) == expected


def test_lookup_ignores_case_and_spaces() -> None:
    """站点名字有全角空格与大小写差异, 宽松匹配仍应唯一命中."""
    assert lookup_actress_id(f" {NAME} ") == lookup_actress_id(NAME)


def test_reading_brackets_are_not_searchable() -> None:
    """读音括号与厂牌标注在建表时剥掉, 不该留下带括号的键."""
    assert lookup_actress_id(f"{NAME} （たちばなめぐみ / Tachibana Megumi）") is None
    for key in _name_index():
        assert "（" not in key and "(" not in key


@pytest.mark.asyncio
async def test_search_uses_local_index_without_touching_search_page() -> None:
    """命中索引时不应请求搜索页 —— 那个入口必然被挑战拦下."""
    crawler = MinnanoActorCrawler(client=AsyncMock(), config=None)
    url = await crawler._search(NAME)

    assert url == f"{BASE}/actress{ID}.html"
    crawler.client.get_html.assert_not_called()  # type: ignore[attr-defined]


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
