"""糖心 Vlog 解析: 标题搜索 + 详情页.

站点搜索是纯前端过滤, 真正的数据源是 ``/search-index.json`` (一条记录是
``[id, 标题, 演员, 标签, 时长, 描述]`` 的数组). 这里用假 client 喂固定索引与页面,
不访问网络.
"""

from __future__ import annotations

import pytest

from amane.crawlers import registry
from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.tangxin import (
    TangxinCrawler,
    _looks_like_tangxin,
    _normalize_search_text,
    _number_from_url,
    _parse_runtime,
    clean_search_title,
    spaced_title,
)
from amane.enums import SiteName
from amane.parsing import ContentType, parse_file_info

_INDEX = [
    [
        "6aef1a3558ccd9a2e6045645",
        "【饥渴的巨乳人妻】 背着老公偷情 肉穴塞满抽插不停",
        "由奈",
        ["人妻", "巨乳"],
        "24:45",
        "描述甲",
    ],
    ["6ae2fb37a98e8842b20c5172", "【新人由奈】姐弟淫乱 强上巨乳姐姐 后入深插", "由奈", ["巨乳"], "31:20", "描述乙"],
    ["29570", "【美乳OL】傲慢油亮黑丝女上司", "Yuzukitty柚子猫", ["OL"], "18:03", "描述丙"],
]

_DETAIL_HTML = """<html><head>
<meta property="og:title" content="【饥渴的巨乳人妻】 背着老公偷情 肉穴塞满抽插不停 - @由奈 - 糖心Vlog在线观看">
<meta property="og:image" content="https://t.5gcdn.xyz/videos/6aef1a3558ccd9a2e6045645/cover.jpg">
</head><body>
<h1>【饥渴的巨乳人妻】 背着老公偷情 肉穴塞满抽插不停</h1>
<p><a href="/a/%E7%94%B1%E5%A5%88/">@由奈</a> · 24:45 · 喜欢 35.0w · 播放 89.5w · 2026-11-12</p>
<ul class="tags">
<li><a href="/tag/1/">口爆</a></li>
<li><a href="/tag/2/">人妻</a></li>
<li><a href="/tag/3/">巨乳</a></li>
</ul>
</body></html>"""


class _FakeClient:
    def __init__(self, index: list[list[object]] | None = None, html: str | None = None) -> None:
        self._index = index if index is not None else _INDEX
        self._html = html if html is not None else _DETAIL_HTML
        self.urls: list[str] = []

    async def get_json(self, url: str, **kwargs: object) -> object:
        del kwargs
        self.urls.append(url)
        return self._index

    async def get_html(self, url: str, **kwargs: object) -> str:
        del kwargs
        self.urls.append(url)
        return self._html


def _crawler(client: _FakeClient) -> TangxinCrawler:
    return TangxinCrawler(client=client)  # type: ignore[arg-type]


def test_registered() -> None:
    assert registry.get("tangxin") is TangxinCrawler


def test_profile() -> None:
    profile = TangxinCrawler.profile()
    assert profile.name is SiteName.TANGXIN
    assert "tangxinvlog" in profile.base_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 标点折成空格, 开头残留符号一并去掉.
        ("【饥渴的巨乳人妻】 背着老公偷情", "饥渴的巨乳人妻 背着老公偷情"),
        ("「贱母狗求欢」：当了好久的 S 女王", "贱母狗求欢 当了好久的 S 女王"),
        ("  多余   空格  ", "多余 空格"),
        ("", ""),
    ],
)
def test_normalize_search_text(raw: str, expected: str) -> None:
    assert _normalize_search_text(raw) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://tangxinvlog.app/v/6aef1a3558ccd9a2e6045645/", "6aef1a3558ccd9a2e6045645"),
        ("https://tangxinvlog.app/v/29570/", "29570"),
        ("https://tangxinvlog.app/a/x/", None),
    ],
)
def test_number_from_url(url: str, expected: str | None) -> None:
    assert _number_from_url(url) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("24:45", 25),
        ("01:05:00", 65),
        ("18:03", 18),
        ("坏值", None),
    ],
)
def test_parse_runtime(raw: str, expected: int | None) -> None:
    assert _parse_runtime(raw) == expected


@pytest.mark.asyncio(loop_scope="function")
async def test_search_picks_title_match() -> None:
    """按标题找: 词元全覆盖, 命中更短标题的那条."""
    client = _FakeClient()
    crawler = _crawler(client)
    query = SearchQuery("【饥渴的巨乳人妻】 背着老公偷情", file_path="/lib/糖心/某片.mp4", content_type="chinese")

    url = await crawler._search(query)

    assert url == "https://tangxinvlog.app/v/6aef1a3558ccd9a2e6045645/"
    # 索引只拉一次: 第二次搜索复用同一份.
    assert sum(1 for u in client.urls if "search-index" in u) == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_search_miss_returns_none() -> None:
    crawler = _crawler(_FakeClient())
    query = SearchQuery("完全不存在的标题词", None, None, "chinese")
    assert await crawler._search(query) is None


@pytest.mark.asyncio(loop_scope="function")
async def test_scrape_detail_fields() -> None:
    crawler = _crawler(_FakeClient())
    meta = await crawler._scrape("https://tangxinvlog.app/v/6aef1a3558ccd9a2e6045645/")

    assert meta is not None
    # 本站没有番号体系: 标题就是 number.
    assert meta.number == "【饥渴的巨乳人妻】 背着老公偷情 肉穴塞满抽插不停"
    # og:title 的站点后缀与演员前缀都剥掉.
    assert meta.title == "【饥渴的巨乳人妻】 背着老公偷情 肉穴塞满抽插不停"
    assert [actor.name for actor in meta.actors] == ["由奈"]
    assert meta.tags == ["口爆", "人妻", "巨乳"]
    assert meta.runtime == 25
    assert meta.release == "2026-11-12"
    assert meta.poster_urls == ["https://t.5gcdn.xyz/videos/6aef1a3558ccd9a2e6045645/cover.jpg"]
    assert meta.source_url == "https://tangxinvlog.app/v/6aef1a3558ccd9a2e6045645/"


@pytest.mark.asyncio(loop_scope="function")
async def test_scrape_cover_falls_back_to_convention() -> None:
    """没有 og:image 时按站点的目录约定拼封面."""
    html = _DETAIL_HTML.replace(
        '<meta property="og:image" content="https://t.5gcdn.xyz/videos/6aef1a3558ccd9a2e6045645/cover.jpg">',
        "",
    )
    crawler = _crawler(_FakeClient(html=html))
    meta = await crawler._scrape("https://tangxinvlog.app/v/6aef1a3558ccd9a2e6045645/")

    assert meta is not None
    assert meta.poster_urls == ["https://t.5gcdn.xyz/videos/6aef1a3558ccd9a2e6045645/cover.jpg"]


@pytest.mark.asyncio(loop_scope="function")
async def test_scrape_rejects_foreign_url() -> None:
    crawler = _crawler(_FakeClient())
    assert await crawler._scrape("https://example.com/nope") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 文件名式标题: 域名与 Up主 标签剥掉, 分辨率与扩展名去掉.
        (
            "489155.com@糖心VlogUp主【小痛仰】黑丝淫穴上门家政女仆黑丝淫穴诱惑骚穴被业主肉棒塞满打桩内射.mp4",
            "小痛仰 黑丝淫穴上门家政女仆黑丝淫穴诱惑骚穴被业主肉棒塞满打桩内射",
        ),
        # 没有前缀的普通标题: 标点折空格.
        ("【饥渴的巨乳人妻】 背着老公偷情", "饥渴的巨乳人妻 背着老公偷情"),
        # 分辨率标记去掉.
        ("某标题 [720p] 后半", "某标题 后半"),
        ("某标题-1080p", "某标题"),
        # 扩展名去掉.
        ("标题.mp4", "标题"),
    ],
)
def test_clean_search_title(raw: str, expected: str) -> None:
    assert clean_search_title(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 中文标题逐字加空格: 站内搜索按空格切词元, 不切分就匹配不上.
        ("淫荡的家庭护士", "淫 荡 的 家 庭 护 士"),
        ("温温兔-淫荡的家庭护士", "温 温 兔 淫 荡 的 家 庭 护 士"),
        # 英文与数字同样拆开.
        ("abc123", "a b c 1 2 3"),
        ("", ""),
    ],
)
def test_spaced_title(raw: str, expected: str) -> None:
    assert spaced_title(raw) == expected


@pytest.mark.parametrize(
    ("file_path", "number", "expected"),
    [
        # 库路径带「糖心」: 是本站内容.
        ("/lib/糖心/某片.mp4", "某标题", True),
        # 文件名带「糖心」也算.
        ("/lib/其他/糖心Vlog-某片.mp4", "某标题", True),
        # 没有路径时一律放行: 手动搜索是用户主动要查, 拦下来等于让人搜不到.
        (None, "489155.com@糖心VlogUp主【小痛仰】某标题.mp4", True),
        (None, "【饥渴的巨乳人妻】背着老公偷情", True),
        # 有路径但路径不带糖心: 不该走本站 (避免认领库里别的中文片).
        ("/lib/国产/某片.mp4", "某标题", False),
    ],
)
def test_looks_like_tangxin(file_path: str | None, number: str, expected: bool) -> None:
    assert _looks_like_tangxin(file_path, number) is expected


@pytest.mark.asyncio(loop_scope="function")
async def test_search_skips_unrelated_file() -> None:
    """路径与标题都不带糖心字样时直接跳过, 不去问站点."""
    client = _FakeClient()
    crawler = _crawler(client)
    query = SearchQuery("某个普通国产标题", file_path="/lib/国产/x.mp4", content_type="chinese")

    assert await crawler._search(query) is None
    # 一个请求都没发出去.
    assert client.urls == []


@pytest.mark.asyncio(loop_scope="function")
async def test_search_falls_back_to_site_search() -> None:
    """索引里没有时走站内搜索页."""
    index = [["x", "完全不相干的一条", "演员", [], "10:00", "描述"]]
    html = '<a href="/v/abc123/">某标题</a>'
    client = _FakeClient(index=index, html=html)
    crawler = _crawler(client)

    url = await crawler._search(SearchQuery("糖心 某标题", file_path="/lib/糖心/x.mp4", content_type="chinese"))

    assert url == "https://tangxinvlog.app/v/abc123/"
    # 索引拉过一次之后又请求了搜索页.
    assert any("/search/" in u for u in client.urls)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        # 「糖心」字样出现在目录或文件名里都判为国产.
        (r"Z:\AV\糖心\某标题.mp4", ContentType.CHINESE),
        (r"Z:\AV\糖心Vlog-某标题.mp4", ContentType.CHINESE),
        (r"Z:\AV\tangxin\某标题.mp4", ContentType.CHINESE),
        # 其它目录不受影响.
        (r"Z:\AV\欧美\某标题.mp4", ContentType.WESTERN),
        (r"Z:\AV\里番\某标题.mp4", ContentType.HENTAI),
    ],
)
def test_tangxin_folder_is_chinese(path: str, expected: ContentType) -> None:
    assert parse_file_info(path).content_type is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 搜索时站名要排除: 索引标题里没有「糖心」二字.
        ("糖心 【饥渴的巨乳人妻】", "饥渴的巨乳人妻"),
        ("糖心VlogUp主【小痛仰】黑丝", "小痛仰 黑丝"),
        ("糖心Vlog 某标题", "某标题"),
    ],
)
def test_clean_strips_site_name(raw: str, expected: str) -> None:
    assert clean_search_title(raw) == expected


_DETAIL_WITH_JSONLD = """<html><head>
<meta property="og:title" content="某标题 - @温温兔 - 糖心Vlog在线观看">
<meta property="og:image" content="https://t.5gcdn.xyz/videos/abc/cover.jpg">
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"VideoObject",
 "name":"某标题","description":"感谢大家的一直以来的喜欢 评论区抽一个真爱粉送红包",
 "thumbnailUrl":"https://t.5gcdn.xyz/videos/abc/cover.jpg",
 "uploadDate":"2026-10-23T00:00:00+08:00","duration":"PT46M27S",
 "keywords":"御姐, 护士, 白丝","creator":{"@type":"Person","name":"温温兔"}}
</script>
</head><body><h1>某标题</h1></body></html>"""


@pytest.mark.asyncio(loop_scope="function")
async def test_scrape_uses_jsonld() -> None:
    """优先用 schema.org VideoObject: 结构化字段比挑 HTML 节点稳."""
    crawler = _crawler(_FakeClient(html=_DETAIL_WITH_JSONLD))
    meta = await crawler._scrape("https://tangxinvlog.app/v/abc/")

    assert meta is not None
    assert meta.title == "某标题"
    # 简介来自 VideoObject.description, 不是 p.description.
    assert meta.plot == "感谢大家的一直以来的喜欢 评论区抽一个真爱粉送红包"
    assert meta.runtime == 46
    assert meta.release == "2026-10-23"
    assert [a.name for a in meta.actors] == ["温温兔"]
    assert meta.tags == ["御姐", "护士", "白丝"]


@pytest.mark.asyncio(loop_scope="function")
async def test_scrape_drops_plot_equal_to_title() -> None:
    """站点把标题填进 description 时不算简介."""
    html = _DETAIL_WITH_JSONLD.replace("感谢大家的一直以来的喜欢 评论区抽一个真爱粉送红包", "某标题")
    crawler = _crawler(_FakeClient(html=html))
    meta = await crawler._scrape("https://tangxinvlog.app/v/abc/")

    assert meta is not None
    assert meta.plot is None


@pytest.mark.asyncio(loop_scope="function")
async def test_scrape_drops_placeholder_plot() -> None:
    """「当前暂无简介」是占位文案, 不是内容."""
    html = _DETAIL_WITH_JSONLD.replace("感谢大家的一直以来的喜欢 评论区抽一个真爱粉送红包", "当前暂无简介")
    crawler = _crawler(_FakeClient(html=html))
    meta = await crawler._scrape("https://tangxinvlog.app/v/abc/")

    assert meta is not None
    assert meta.plot is None


@pytest.mark.parametrize(
    ("raw", "actor", "expected"),
    [
        # 各种分隔符都要能剥掉开头的演员名.
        ("温温兔-淫荡的家庭护士 体验40度的肉棒", "温温兔", "淫荡的家庭护士 体验40度的肉棒"),
        ("【温温兔】淫荡的家庭护士", "温温兔", "淫荡的家庭护士"),
        ("[温温兔] 淫荡的家庭护士", "温温兔", "淫荡的家庭护士"),
        ("温温兔 淫荡的家庭护士", "温温兔", "淫荡的家庭护士"),
        ("温温兔·淫荡的家庭护士", "温温兔", "淫荡的家庭护士"),
        ("温温兔｜淫荡的家庭护士", "温温兔", "淫荡的家庭护士"),
        ("（温温兔）淫荡的家庭护士", "温温兔", "淫荡的家庭护士"),
        # 演员名不在开头: 不剥 (可能是作品内容的一部分).
        ("淫荡的家庭护士 温温兔 客串", "温温兔", "淫荡的家庭护士 温温兔 客串"),
        # 演员名是常见词且在中段时同样保留.
        ("玫瑰之夜 玫瑰 绽放", "玫瑰", "玫瑰之夜 玫瑰 绽放"),
        # 开头就是演员名且没有后续内容: 保持原样, 不要剥成空.
        ("温温兔", "温温兔", "温温兔"),
        # 没给演员名时不剥.
        ("温温兔-某标题", None, "温温兔 某标题"),
    ],
)
def test_strip_leading_actor(raw: str, actor: str | None, expected: str) -> None:
    assert clean_search_title(raw, actor) == expected


@pytest.mark.asyncio(loop_scope="function")
async def test_scrape_strips_actor_from_title_and_number() -> None:
    """元数据标题与番号都剥掉开头演员名."""
    original = "温温兔-淫荡的家庭护士 体验40度的肉棒 骑乘榨精精液灌穴"
    with_actor = _DETAIL_WITH_JSONLD.replace("某标题", original).replace(
        '"creator":{"@type":"Person","name":"温温兔"}',
        '"creator":{"@type":"Person","name":"温温兔"}',
    )
    crawler = _crawler(_FakeClient(html=with_actor))
    meta = await crawler._scrape("https://tangxinvlog.app/v/abc/")

    assert meta is not None
    assert [a.name for a in meta.actors] == ["温温兔"]
    # 标题与番号都剥掉了开头的演员名与连字符.
    expected = "淫荡的家庭护士 体验40度的肉棒 骑乘榨精精液灌穴"
    assert meta.title == expected
    assert meta.number == expected


class TestTangxinRejectsNumberShaped:
    """糖心没有番号体系, 番号形态的输入不该交给它.

    拿番号问糖心必然命中不了; 更糟的是索引比对按字覆盖率算, 番号里的字母数字会撞上
    一条标题碰巧含这些字符的无关作品, 把元数据写歪. 因此这类输入直接跳过.
    """

    @pytest.mark.parametrize(
        ("number", "path"),
        [
            ("heydouga-4037-1002", None),
            ("Heydouga-4037-1002", None),
            ("MIDV-123", None),
            ("n1234", None),
            ("FC2-PPV-1234567", None),
            ("010115-001", None),
            ("MIDV-123", r"D:\test\糖心\a.mp4"),
            ("中文标题", r"D:\test\JAV\a.mp4"),
        ],
    )
    def test_number_shaped_is_rejected(self, number: str, path: str | None) -> None:
        assert _looks_like_tangxin(path, number) is False

    @pytest.mark.parametrize(
        ("number", "path"),
        [
            ("淫荡的家庭护士 体验40度的肉棒", None),
            ("13 糖心网红UP主偷情一束光约会成为人妻的前女友", None),
            ("中文标题", r"D:\test\糖心\a.mp4"),
        ],
    )
    def test_chinese_title_is_accepted(self, number: str, path: str | None) -> None:
        assert _looks_like_tangxin(path, number) is True
