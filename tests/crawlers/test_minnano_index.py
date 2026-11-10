"""minnano 的离线演员检索表.

站点的 ``search_result.php`` 处于 Cloudflare 挑战之后 (403), 而演员列表页与详情页不设防,
所以索引靠整表枚举生成. 这里验证查表本身: 命中时不应该发起任何网络请求.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from amane.crawlers.actor.sites.minnano import (
    MinnanoActorCrawler,
    _detail_index,
    _name_index,
    lookup_actress_id,
    metadata_from_index,
)

BASE = "https://www.minnano-av.com"


def test_index_is_embedded() -> None:
    """索引随包分发; 缺失会让整条检索退回被拦的在线通路."""
    assert len(_name_index()) > 10_000


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # 主名 / 假名 / 罗马字都指向同一个 id.
        ("竹内咲奈", "551014"),
        ("たけうちさな", "551014"),
        ("Takeuchi Sana", "551014"),
        ("綾野カヲル", "471698"),
        # 查不到就是 None, 不瞎猜.
        ("完全不存在的名字", None),
        ("", None),
    ],
)
def test_lookup_actress_id(name: str, expected: str | None) -> None:
    assert lookup_actress_id(name) == expected


def test_lookup_ignores_case_and_spaces() -> None:
    """站点名字有全角空格与大小写差异, 宽松匹配仍应唯一命中."""
    assert lookup_actress_id("takeuchisana") == lookup_actress_id("Takeuchi Sana")


@pytest.mark.asyncio
async def test_search_uses_local_index_without_touching_search_page() -> None:
    """命中索引时不应请求搜索页 —— 那个入口必然被挑战拦下."""
    crawler = MinnanoActorCrawler(client=AsyncMock(), config=None)
    url = await crawler._search("竹内咲奈")

    assert url == "https://www.minnano-av.com/actress551014.html"
    crawler.client.get_html.assert_not_called()  # type: ignore[attr-defined]


def test_detail_index_is_embedded() -> None:
    assert len(_detail_index()) > 10_000


def test_metadata_from_index_keeps_aliases_and_body() -> None:
    """别名 (假名/罗马字) 与三围都要带出: 演员自动合并依赖别名, 三围是展示字段."""
    meta = metadata_from_index("551014", base_url=BASE)
    assert meta is not None
    assert meta.name == "竹内咲奈"
    assert "たけうちさな" in meta.aliases
    assert "Takeuchi Sana" in meta.aliases
    assert meta.birthday == "2007-11-30"
    assert (meta.height, meta.bust, meta.waist, meta.hip, meta.cup) == (158, 86, 58, 85, "C")


def test_metadata_from_index_missing_id_returns_none() -> None:
    assert metadata_from_index("999999999", base_url=BASE) is None


@pytest.mark.asyncio
async def test_scrape_falls_back_to_index_when_site_unparseable() -> None:
    """网站解析不出来时退回索引, 不把已有资料丢掉.

    索引是抓取当时的快照, 字段集也未必覆盖网站全部项; 以快照为准会丢掉网站后来更正的
    值和快照漏掉的格子, 所以只做兜底.
    """
    client = AsyncMock()
    client.get_html = AsyncMock(return_value="<html>改版了, 没有 act-profile</html>")
    crawler = MinnanoActorCrawler(client=client, config=None)

    meta = await crawler._scrape(f"{BASE}/actress551014.html")

    assert meta is not None
    assert meta.name == "竹内咲奈"
    client.get_html.assert_called_once()


@pytest.mark.asyncio
async def test_scrape_fetches_when_index_missing_fields() -> None:
    """索引缺生日时应抓一次详情页, 并让网站数据覆盖索引."""
    online_html = """
    <div class="act-profile"><table>
      <tr><td><h2>綾野カヲル （あやのかをる / Ayano Kaworu）</h2></td></tr>
      <tr><td><span>生年月日</span><p>1990年1月2日</p></td></tr>
    </table></div>
    """
    client = AsyncMock()
    client.get_html = AsyncMock(return_value=online_html)
    crawler = MinnanoActorCrawler(client=client, config=None)

    meta = await crawler._scrape(f"{BASE}/actress471698.html")

    assert meta is not None
    assert meta.birthday == "1990-01-02"  # 网站补上了
    assert meta.height == 155  # 索引仍在的字段保留
    assert "あやのかをる" in meta.aliases  # 别名不因网站缺失而丢
    client.get_html.assert_called_once()


@pytest.mark.asyncio
async def test_complete_index_still_asks_site() -> None:
    """索引齐全也照样问一次网站: 网站是当前事实, 索引只是快照.

    索引不参与「是否复用上次刮削结果」的判断, 缓存开关也不该把它伸进这里.
    """
    online_html = """
    <div class="act-profile"><table>
      <tr><td><h2>竹内咲奈 （たけうちさな / Takeuchi Sana）</h2></td></tr>
      <tr><td><span>生年月日</span><p>2007年11月30日</p></td></tr>
      <tr><td><span>出身地</span><p>東京都</p></td></tr>
    </table></div>
    """
    client = AsyncMock()
    client.get_html = AsyncMock(return_value=online_html)
    crawler = MinnanoActorCrawler(client=client, config=None)

    meta = await crawler._scrape(f"{BASE}/actress551014.html")

    assert meta is not None
    assert meta.birthday == "2007-11-30"
    # 网站提供的出生地拿得到; 索引里没有这一项.
    assert meta.birthplace == "東京都"
    client.get_html.assert_called_once()
