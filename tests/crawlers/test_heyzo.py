"""HEYZO 爬虫: 番号 ↔ 详情页地址, 以及 RSC payload 的字段解析.

页面实测是 Next.js App Router, 数据不在 HTML 标签里, 而在 ``self.__next_f.push([1, "..."])``
的字符串块中. 夹具用 ``json.dumps`` 生成, 转义与真实页面一致; 故意切成两块, 因为线上就是
按任意位置分块推送的.
"""

import json
from unittest.mock import AsyncMock

import pytest
from parsel import Selector

from amane.crawlers.http import HttpClient
from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.heyzo import (
    HeyzoCrawler,
    _actors,
    _categories,
    _duration_seconds,
    _gallery,
    _image_root,
    _movie_node,
    _plot,
    _release,
    _rsc_payload,
    _score,
    detail_url,
    number_from_url,
)
from amane.parsing import ContentType

_URL = "https://www.heyzo.com/movie/{movie_id}"

_CHUNK_HEAD = (
    '5:["$","$L28",null,{"movie":{"movie_seq":4002,"movie_id":"3987",'
    '"create_date":"$D2026-09-01T18:21:53.000Z","name":"台本無しでイキナリ本番！！",'
    '"detail":{"movie_seq":4002,"memo":"清楚な美女の本宮あすかちゃん！","rv_score":0,"group_id":2},'
    '"categories":['
    '{"category_id":1,"category_name":"69","category_name_en":"69","category_key_name":""},'
    '{"category_id":317,"category_name":"自社２次利用作品","category_name_en":"x",'
    '"category_key_name":"op_flag_inhouse_reuse"}'
)
#: 数据被切成两块: 前半截还没有结束引号, 必须拼接后才是一个完整字面量.
_CHUNK_TAIL = (
    '],"download":{"whole":[{"movie_seq":4002,"file_num":1,"duration":"01:01:12","file_size_mb":221}],"split":[]}}"}]'
)


def _dumps(value: object) -> str:
    """紧凑序列化: 线上 payload 不带空格, 夹具照抄这个形态才测得出真实解析."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _page(*chunks: str) -> str:
    """把若干片段包成 ``self.__next_f.push([1, "..."])``, 转义交给 json.dumps."""
    scripts = "".join(f"<script>self.__next_f.push([1,{json.dumps(chunk)}])</script>" for chunk in chunks)
    return f"<html><body>{scripts}</body></html>"


PAGE = _page(_CHUNK_HEAD + _CHUNK_TAIL)

#: 旧片的下载时长写 00:00:00 —— 应当当作没有时长, 而不是 0 秒.
PAGE_OLD = _page(
    _dumps(
        {
            "movie_seq": 690,
            "movie_id": "0683",
            "create_date": "$D2014-08-09T00:39:44.000Z",
            "name": "極太黒砲 VS 絶倫美女",
            "detail": {"movie_seq": 690, "memo": "初めての黒マラ", "rv_score": 4},
            "categories": [],
            "download": {"whole": [{"duration": "00:00:00"}], "split": []},
        }
    )
)

#: 剧照缩略图只在 HTML 的 <img> 里, 不在 payload 中; Next.js 把真实路径放在 url= 参数里.
PAGE_WITH_GALLERY = """<html><body>
<img src="/_next/image?url=https%3A%2F%2Fwww.heyzo.com%2Fcontents%2F3000%2F1438%2Fgallery%2Fthumbnail_001.jpg&w=256&q=75">
<img src="/_next/image?url=https%3A%2F%2Fwww.heyzo.com%2Fcontents%2F3000%2F1438%2Fgallery%2Fthumbnail_013.jpg&w=256&q=75">
<img src="/_next/image?url=https%3A%2F%2Fwww.heyzo.com%2Fcontents%2F3000%2F1438%2Fgallery%2Fthumbnail_021.jpg&w=256&q=75">
</body></html>"""

#: 缩略图地址也可能直接给出完整路径.
_NEXT_IMG_PAGE = """<html><body>
<img src="https://www.heyzo.com/contents/3000/3987/gallery/thumbnail_007.jpg">
</body></html>"""

PAGE_WITH_TEASER = _page(
    _dumps([{"movie_id": "3987", "title": "推荐位噪声"}]),
    _dumps(
        {
            "movie_seq": 4002,
            "movie_id": "3987",
            "create_date": "$D2026-09-01T18:21:53.000Z",
            "name": "真标题",
            "detail": {"memo": "真剧情"},
        }
    ),
)


class TestNumberAndUrl:
    @pytest.mark.parametrize(
        ("number", "expected"),
        [
            ("HEYZO-3987", "https://www.heyzo.com/movie/3987"),
            ("heyzo-1438", "https://www.heyzo.com/movie/1438"),
            ("HEYZO_0683", "https://www.heyzo.com/movie/0683"),
            # 前导零必须保留: 站点用它区分 0683 与 683.
            ("HEYZO-1275", "https://www.heyzo.com/movie/1275"),
            ("SSIS-497", None),
            ("HEYZO-", None),
            ("", None),
        ],
    )
    def test_detail_url(self, number: str, expected: str | None) -> None:
        assert detail_url(number) == expected

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://www.heyzo.com/movie/3987", "HEYZO-3987"),
            ("https://www.heyzo.com/movie/0683/", "HEYZO-0683"),
            ("https://www.heyzo.com/", None),
            ("https://www.heyzo.com/listpages/1", None),
        ],
    )
    def test_number_from_url(self, url: str, expected: str | None) -> None:
        assert number_from_url(url) == expected


class TestPayloadParsing:
    def test_chunks_are_joined_and_unescaped(self) -> None:
        """数据被切在任意位置, 只有拼接后才读得到完整节点."""
        payload = _rsc_payload(PAGE)
        assert '"movie_seq":4002' in payload
        assert "台本無しでイキナリ本番" in payload

    def test_movie_node_prefers_detail_node(self) -> None:
        """推荐位也带同一个 id, 只有详情节点有 movie_seq 与 name."""
        node = _movie_node(_rsc_payload(PAGE_WITH_TEASER), "3987")
        assert node is not None
        assert node["name"] == "真标题"

    def test_movie_node_missing_id(self) -> None:
        assert _movie_node(_rsc_payload(PAGE), "9999") is None

    def test_plot_drops_whitespace(self) -> None:
        node = _movie_node(_rsc_payload(PAGE), "3987")
        assert node is not None
        assert _plot(node) == "清楚な美女の本宮あすかちゃん！"

    @pytest.mark.parametrize(
        ("page", "expected"),
        [
            (PAGE, 3672),
            # 旧片这里写 00:00:00, 视为没有时长而不是 0 秒.
            (PAGE_OLD, None),
        ],
    )
    def test_duration_seconds(self, page: str, expected: int | None) -> None:
        assert _duration_seconds(_rsc_payload(page)) == expected

    def test_release_from_create_date(self) -> None:
        node = _movie_node(_rsc_payload(PAGE), "3987")
        assert node is not None
        assert _release(node) == "2026-09-01"

    def test_release_missing(self) -> None:
        assert _release({"movie_seq": 1}) is None

    def test_categories_skip_operator_flags(self) -> None:
        """``op_flag_*`` 是运营开关, 不是标签."""
        assert _categories(_rsc_payload(PAGE)) == ["69"]

    def test_actors_deduped(self) -> None:
        payload = '{"actor_name":"本宮あすか"},{"actor_name":"本宮あすか"}'
        assert _actors(payload) == ["本宮あすか"]


class TestImages:
    @pytest.mark.parametrize("movie_id", ["3987", "0683", "1438", "1275"])
    def test_image_root_uses_fixed_bucket(self, movie_id: str) -> None:
        """桶是运营编号 (恒为 3000), 与 movie_seq 无关; 照 seq 算会拼出 404 地址."""
        assert _image_root(movie_id, 4002) == f"https://www.heyzo.com/contents/3000/{movie_id}"
        assert _image_root(movie_id, None) == f"https://www.heyzo.com/contents/3000/{movie_id}"

    def test_gallery_reads_numbers_from_html_thumbnails(self) -> None:
        """剧照编号从页面缩略图读, 大图与小图同编号.

        页面上是 ``gallery/thumbnail_<n>.jpg``, 取 ``images/capture<n>.jpg`` 会拿到播放器截图
        而不是剧照; 编号也不能按数量猜 —— 站点给 21 张, 猜 12 张会漏一半.
        """
        urls = _gallery(Selector(text=PAGE_WITH_GALLERY), "1438")
        assert urls == [
            "https://www.heyzo.com/contents/3000/1438/gallery/001.jpg",
            "https://www.heyzo.com/contents/3000/1438/gallery/013.jpg",
            "https://www.heyzo.com/contents/3000/1438/gallery/021.jpg",
        ]

    def test_gallery_handles_next_image_proxy_urls(self) -> None:
        """Next.js 图片优化地址把真实路径放在 ``url=`` 里, 且是百分号编码的."""
        urls = _gallery(Selector(text=_NEXT_IMG_PAGE), "3987")
        assert urls == ["https://www.heyzo.com/contents/3000/3987/gallery/007.jpg"]

    def test_gallery_empty_without_thumbnails(self) -> None:
        assert _gallery(Selector(text="<html></html>"), "3987") == []


class TestScrape:
    @pytest.fixture
    def web_client(self) -> AsyncMock:
        client = AsyncMock()
        client.get_text.return_value = PAGE
        return client

    @pytest.mark.asyncio
    async def test_scrape_collects_fields(self, web_client: AsyncMock) -> None:
        crawler = HeyzoCrawler(client=HttpClient(web=web_client))

        result = await crawler._scrape(_URL.format(movie_id="3987"))

        assert result is not None
        assert result.number == "HEYZO-3987"
        assert result.title == "台本無しでイキナリ本番！！"
        assert result.studio == "HEYZO"
        assert result.publisher == "HEYZO"
        assert result.release == "2026-09-01"
        assert result.runtime_seconds == 3672
        assert result.runtime == 62
        assert result.tags == ["69"]
        assert result.plot == "清楚な美女の本宮あすかちゃん！"
        assert result.thumb_urls == ["https://www.heyzo.com/contents/3000/3987/images/thumbnail.jpg"]
        # 夹具的 HTML 里没有缩略图, 因此剧照为空; 有缩略图的情形见 TestImages.
        assert result.source_url == _URL.format(movie_id="3987")

    @pytest.mark.asyncio
    async def test_scrape_rejects_non_detail_url(self, web_client: AsyncMock) -> None:
        crawler = HeyzoCrawler(client=HttpClient(web=web_client))
        assert await crawler._scrape("https://www.heyzo.com/") is None
        web_client.get_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_scrape_returns_none_when_page_has_no_node(self) -> None:
        client = AsyncMock()
        client.get_text.return_value = "<html></html>"
        crawler = HeyzoCrawler(client=HttpClient(web=client))
        assert await crawler._scrape(_URL.format(movie_id="3987")) is None

    @pytest.mark.asyncio
    async def test_search_builds_url_from_number(self, web_client: AsyncMock) -> None:
        """没有搜索接口: 番号即地址, 不联网也能得到结果."""
        crawler = HeyzoCrawler(client=HttpClient(web=web_client))
        query = SearchQuery("HEYZO-3987", None, None, ContentType.UNCENSORED)
        assert await crawler._search(query) == "https://www.heyzo.com/movie/3987"
        web_client.get_text.assert_not_awaited()


class TestScore:
    """``detail.rv_score`` 是用户评分 (5 分制)."""

    @pytest.mark.parametrize(
        ("detail", "expected"),
        [
            ({"rv_score": 4.5}, 4.5),
            ({"rv_score": 3.75}, 3.75),
            ({"rv_score": 4.5909}, 4.59),
            # 无人评分时站点写 0: 不给 0 分, 否则界面上会显示成「评了 0 分」.
            ({"rv_score": 0}, None),
            ({}, None),
            (None, None),
        ],
    )
    def test_score(self, detail, expected: float | None) -> None:
        assert _score({"detail": detail}) == expected

    def test_score_without_detail(self) -> None:
        assert _score({"movie_seq": 1}) is None
