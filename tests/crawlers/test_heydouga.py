"""heydouga 爬虫: 番号 ↔ 详情页地址, 以及详情页字段解析.

页面夹具取自线上两页 (限定配信的「配信期間」写法与常规的「配信日」写法各一),
只保留被测选择器依赖的结构.
"""

from unittest.mock import AsyncMock

import pytest
from parsel import Selector

from amane.crawlers.http import HttpClient
from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.heydouga import (
    HeydougaCrawler,
    _actors,
    _avg_rating,
    _clean,
    _cover,
    _gallery,
    _plot,
    _release,
    _runtime,
    _title_and_actors,
    detail_url,
    number_from_url,
)

_URL = "https://www.heydouga.com/moviepages/{provider}/{movie}/index.html"

#: 限定配信: 日期写作区间, 出演带「他」后缀.
_PAGE_LIMITED = """
<html><head>
<meta property="og:title" content="女体解析 ５時間スペシャル Part4 &#45; はるな　他" />
<meta property="og:image" content="https://image01-www.heydouga.com/contents/4382/188/player_thumb.webp" />
</head><body>
<script>var movie_seq = '158510';</script>
<div id="movie-info">
  <ul>
    <li class="mb-2"><span>配信期間：</span><span>2012-06-29 ～ 2026-11-26</span></li>
    <li><span>主演：</span><span><a href="/ppv/search/?keyword=x" title="はるな　他">はるな　他</a></span></li>
    <li><span>提供元：</span><span><a href="/listpages/provider_4382_1.html">CC_archive</a>
      （ <a href="https://x">公式サイト</a> ）<span><button>フォロー</button></span></span></li>
    <li><span>動画再生時間：</span><span>329 分</span></li>
  </ul>
</div>
<div id="movie-description"><div class="movie-description"><p>看板シリーズの第４弾です。</p></div></div>
<div class="lazy" data-src="/contents/4382/188/sample.mp4"></div>
</body></html>
"""

#: 常规作品: 日期写作「配信日」, 出演是单个名字.
_PAGE_PLAIN = """
<html><head>
<meta property="og:title" content="クリ剥きしこしこオナニー &#45; さや" />
<meta property="og:image" content="https://image01-www.heydouga.com/contents/4039/2248/player_thumb.webp" />
</head><body>
<script>var movie_seq = '141376';</script>
<div id="movie-info">
  <ul>
    <li class="mb-2"><span>配信日：</span><span>2024-05-21</span></li>
    <li><span>主演：</span><span><a href="/ppv/search/?keyword=y">さや</a></span></li>
    <li><span>提供元：</span><span><a href="/listpages/provider_4039_1.html">女体のしんぴ</a></span></li>
    <li><span>動画再生時間：</span><span>23 分</span></li>
  </ul>
</div>
<div id="movie-description"><div class="movie-description"><p>本日初めての撮影。</p></div></div>
</body></html>
"""

_TAG_RESPONSE = '{"limit":30,"tag":[{"tag_id":41,"tag_name":"\\u7d20\\u4eba"},{"tag_id":2,"tag_name":"\\u30ae\\u30e3\\u30eb"}],"result":0}'


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        ("Heydouga-4382-188", "https://www.heydouga.com/moviepages/4382/188/index.html"),
        ("heydouga-4039-2248", "https://www.heydouga.com/moviepages/4039/2248/index.html"),
        ("HEYDOUGA_4037_100", "https://www.heydouga.com/moviepages/4037/100/index.html"),
        # 不是 heydouga 番号: 不拼地址
        ("MIDV-123", None),
        ("HEYZO-1234", None),
        ("Heydouga-4037", None),
        ("Heydouga-abc-123", None),
        ("", None),
    ],
)
def test_detail_url(number: str, expected: str | None) -> None:
    """两段数字直接拼成详情页; 其它番号一律不命中."""
    assert detail_url(number) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.heydouga.com/moviepages/4382/188/index.html", "Heydouga-4382-188"),
        ("https://www.heydouga.com/moviepages/4039/2248/", "Heydouga-4039-2248"),
        ("https://www.heydouga.com/listpages/all_1.html", None),
        ("https://www.heydouga.com/moviepages/4382/index.html", None),
    ],
)
def test_number_from_url(url: str, expected: str | None) -> None:
    """手动贴地址时按路径里的两段数字读番号."""
    assert number_from_url(url) == expected


@pytest.mark.parametrize(
    ("page", "title", "actors", "release", "runtime"),
    [
        (_PAGE_LIMITED, "女体解析 ５時間スペシャル Part4", ["はるな"], "2012-06-29", 329),
        (_PAGE_PLAIN, "クリ剥きしこしこオナニー", ["さや"], "2024-05-21", 23),
    ],
)
def test_detail_fields(page: str, title: str, actors: list[str], release: str, runtime: int) -> None:
    """标题剥掉尾部的演员; 日期两种写法都能读; 「他」不算演员名."""
    html = Selector(text=page)
    actual_title, actual_actors = _title_and_actors(html)
    assert actual_title == title
    assert actual_actors == actors
    assert _release(html) == release
    assert _runtime(html) == runtime


def test_title_keeps_inner_separator_when_tail_is_not_an_actor() -> None:
    """标题本身含「 - 」时不能硬切: 尾段不是演员名就整段保留."""
    page = _PAGE_PLAIN.replace("クリ剥きしこしこオナニー &#45; さや", "前編 - 後編")
    title, _ = _title_and_actors(Selector(text=page))
    assert title == "前編 - 後編"


def test_cover_and_plot_and_trailer() -> None:
    html = Selector(text=_PAGE_LIMITED)
    assert _cover(html) == "https://image01-www.heydouga.com/contents/4382/188/player_thumb.webp"
    assert _plot(html) == "看板シリーズの第４弾です。"
    # 没有样片的页面不含 sample.mp4
    assert "sample.mp4" not in _PAGE_PLAIN


#: 剧照按编号降序渲染 (30, 29, …), 刮削要按播放顺序升序.
_PAGE_GALLERY = """
<html><body>
<div id="movie-gallery-images">
  <div class="swiper-container"><div class="swiper-wrapper">
    <div class="swiper-slide"><a class="fancybox" rel="my-gallery" href="/image_gallery/4268/176/10.jpg"><img/></a></div>
    <div class="swiper-slide"><a class="fancybox" rel="my-gallery" href="/image_gallery/4268/176/9.jpg"><img/></a></div>
    <div class="swiper-slide"><a class="fancybox" rel="my-gallery" href="/image_gallery/4268/176/2.jpg"><img/></a></div>
    <div class="swiper-slide"><a class="fancybox" rel="my-gallery" href="/image_gallery/4268/176/1.jpg"><img/></a></div>
  </div></div>
</div>
</body></html>
"""


def test_gallery_sorted_by_number_ascending() -> None:
    """剧照链接转成绝对地址, 并按编号升序重排: 站点给的是降序, 直接存会倒着播."""
    urls = _gallery(Selector(text=_PAGE_GALLERY))
    assert urls == [
        "https://www.heydouga.com/image_gallery/4268/176/1.jpg",
        "https://www.heydouga.com/image_gallery/4268/176/2.jpg",
        "https://www.heydouga.com/image_gallery/4268/176/9.jpg",
        "https://www.heydouga.com/image_gallery/4268/176/10.jpg",
    ]


@pytest.mark.parametrize(
    "page",
    [
        "<html><body></body></html>",
        # 容器在但没有链接
        '<html><body><div id="movie-gallery-images"></div></body></html>',
        # 链接在别的容器里, 不算剧照
        '<html><body><div class="other"><a class="fancybox" href="/image_gallery/1/2/1.jpg"></a></div></body></html>',
    ],
)
def test_gallery_empty_when_absent(page: str) -> None:
    """没有剧照区时不报错, 返回空列表."""
    assert _gallery(Selector(text=page)) == []


def test_gallery_keeps_order_when_numbers_not_parseable() -> None:
    """编号认不出时不猜顺序, 保持站点给的顺序."""
    page = (
        '<html><body><div id="movie-gallery-images">'
        '<a class="fancybox" href="/image_gallery/1/2/b.jpg"></a>'
        '<a class="fancybox" href="/image_gallery/1/2/a.jpg"></a>'
        "</div></body></html>"
    )
    assert _gallery(Selector(text=page)) == [
        "https://www.heydouga.com/image_gallery/1/2/b.jpg",
        "https://www.heydouga.com/image_gallery/1/2/a.jpg",
    ]


_PAGE_BILINGUAL = """
<html><body>
<div id="movie-description">
  <div class="movie-description">
    <p>無修正映像。ポルノ・ダン教授は、お気に入りの生徒であるソフィ・ミエルを迎え、彼の有名なブダペストの温泉の前で立っている彼女の写真を撮る。</p>
    <p>順番を幸抱強く待っていたポルノ・ダンは、もはや我慢の限界に達してしまう。</p>
    <p>Uncensored, Professor Porno Dan greets his favorite student, Sofi Miel, and takes pictures of her standing in front of his famous Budapest thermal baths.</p>
    <p>Porno Dan, who has been patiently waiting for his turn, cannot take it any more.</p>
  </div>
</div>
</body></html>
"""


def test_plot_keeps_only_japanese_paragraphs() -> None:
    """英文另起一段时整段丢掉: 两段说的是同一件事, 存下来详情页会重复显示."""
    plot = _plot(Selector(text=_PAGE_BILINGUAL))
    assert plot is not None
    assert "ポルノ・ダン" in plot
    assert "Porno Dan" not in plot
    assert "Uncensored" not in plot
    # 段落边界保留
    assert plot.count("\n\n") == 1


#: 英文不带独立段落, 直接接在日文正文之后的同一段里 (线上确实这么写).
_PAGE_INLINE_BILINGUAL = """
<html><body>
<div id="movie-description">
  <div class="movie-description">
    <p>無修正映像。ポルノ・ダン教授は、お気に入りの生徒であるソフィ・ミエルを迎える。ぜひその目でお確かめください。 Uncensored, Professor Porno Dan greets his favorite student, Sofi Miel. Next, the walk over the castle, and Porno Dan takes more photos.</p>
  </div>
</div>
</body></html>
"""


def test_plot_trims_translation_inside_one_paragraph() -> None:
    """英文接在同一段末尾时按句界截断: 日文句子之间没有空白, 不能误切."""
    plot = _plot(Selector(text=_PAGE_INLINE_BILINGUAL))
    assert plot is not None
    assert "ポルノ・ダン教授" in plot
    assert plot.endswith("お確かめください。")
    assert "Uncensored" not in plot
    assert "Porno Dan" not in plot


def test_plot_returns_none_without_japanese() -> None:
    """整块只有英文时不存剧情, 而不是把译文当正文."""
    page = (
        _PAGE_BILINGUAL.replace("無修正映像。", "")
        .replace(
            "ポルノ・ダン教授は、お気に入りの生徒であるソフィ・ミエルを迎え、彼の有名なブダペストの温泉の前で立っている彼女の写真を撮る。",
            "",
        )
        .replace("順番を幸抱強く待っていたポルノ・ダンは、もはや我慢の限界に達してしまう。", "")
    )
    assert _plot(Selector(text=page)) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("&#45;", "-"),
        ("はるな　他", "はるな 他"),
        ("  多余   空白  ", "多余 空白"),
        ("", None),
        (None, None),
    ],
)
def test_clean(raw: str | None, expected: str | None) -> None:
    """实体与全角空格都收掉; 空串归一为 None."""
    assert _clean(raw) == expected


@pytest.mark.parametrize(
    ("actors_html", "expected"),
    [
        ("<a>Ａ</a><a>Ｂ</a>", ["Ａ", "Ｂ"]),
        ("<a>Ａ、Ｂ</a>", ["Ａ", "Ｂ"]),
        ("<a>Ａ 他</a>", ["Ａ"]),
        ("<a>他</a>", []),
        ("<a>Ａ</a><a>Ａ</a>", ["Ａ"]),
    ],
)
def test_actors_split_and_dedupe(actors_html: str, expected: list[str]) -> None:
    """出演栏可能多个 <a> 或一段带分隔符的文本; 「他」是占位词, 同名只留一次."""
    page = f'<div id="movie-info"><ul><li><span>主演：</span><span>{actors_html}</span></li></ul></div>'
    assert _actors(Selector(text=page)) == expected


@pytest.fixture
def web_client() -> AsyncMock:
    """替代 WebClient: 详情页取夹具, 标签接口回转义过的 JSON."""
    client = AsyncMock()
    client.get_text.return_value = _PAGE_LIMITED
    client.post_text.return_value = _TAG_RESPONSE
    return client


@pytest.mark.asyncio
async def test_scrape_collects_tags_from_form_api(web_client: AsyncMock) -> None:
    """标签在页面上只是占位符, 必须走表单接口; 转义的 \\uXXXX 要解回真字符."""
    crawler = HeydougaCrawler(client=HttpClient(web=web_client))

    result = await crawler._scrape(_URL.format(provider="4382", movie="188"))

    assert result is not None
    assert result.number == "Heydouga-4382-188"
    assert result.tags == ["素人", "ギャル"]
    assert result.actors[0].name == "はるな"
    # 提供元是片商, 发行商固定为站点本身
    assert result.studio == "CC_archive"
    assert result.publisher == "Heydouga"
    assert result.release == "2012-06-29"
    assert result.runtime == 329
    assert result.trailer_urls == ["https://www.heydouga.com/contents/4382/188/sample.mp4"]
    assert [a.name for a in result.actors] == ["はるな"]
    # 详情页地址不是 /moviepages/x/y/ 形态时直接拒绝, 不请求
    assert await crawler._scrape("https://www.heydouga.com/index.html") is None


@pytest.mark.asyncio
async def test_scrape_without_movie_seq_skips_tag_api(web_client: AsyncMock) -> None:
    """页面没有 movie_seq 时不发标签请求, 仍返回其它字段."""
    web_client.get_text.return_value = _PAGE_LIMITED.replace("var movie_seq = '158510';", "")
    crawler = HeydougaCrawler(client=HttpClient(web=web_client))

    result = await crawler._scrape(_URL.format(provider="4382", movie="188"))

    assert result is not None
    assert result.tags == []
    web_client.post_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_rejects_non_heydouga_number(web_client: AsyncMock) -> None:
    """非本站番号在搜索阶段就退出, 不发 HTTP."""
    crawler = HeydougaCrawler(client=HttpClient(web=web_client))

    assert await crawler.fetch(SearchQuery(number="MIDV-123")) is None
    web_client.get_text.assert_not_awaited()


class TestAvgRating:
    """官方评分: ``/ppv/movie_get_movie_rating/`` 的 ``movie_rating_average``.

    这个均分把「只打星、不写字」的票也算进来, 而评论列表只回带文字的评论 —— 实测
    4386/101 是 10 票均分 4.4, 评论只有 4 条且全为 4 分。界面显示前者, 所以取官方均分。
    """

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("4.4000", 4.4),
            ("4.6262", 4.63),
            (4.5, 4.5),
            (5, 5.0),
            # 无人评分时站点回 0: 不给 0 分, 否则界面上会显示成「评了 0 分」.
            ("0", None),
            ("0.0000", None),
            (0, None),
            (None, None),
            # 非数字不猜.
            ("", None),
            ("abc", None),
            (True, None),
        ],
    )
    def test_avg_rating(self, raw, expected: float | None) -> None:
        assert _avg_rating(raw) == expected


@pytest.mark.asyncio
async def test_score_uses_official_average_not_review_rows() -> None:
    """取官方均分, 不按评论列表自算: 后者漏掉只有星级的票."""
    from unittest.mock import AsyncMock

    from amane.crawlers.http import HttpClient

    web = AsyncMock()
    web.get_json.return_value = {
        "movie_rating_count": "10",
        "movie_rating_average": "4.4000",
        "movie_rating_count_review_posted": "4",
    }
    crawler = HeydougaCrawler(client=HttpClient(web=web))

    assert await crawler._score("160848") == 4.4


@pytest.mark.asyncio
async def test_score_missing_seq_skips_request() -> None:
    """页面上取不到 movie_seq 时不发请求."""
    from unittest.mock import AsyncMock

    from amane.crawlers.http import HttpClient

    web = AsyncMock()
    crawler = HeydougaCrawler(client=HttpClient(web=web))

    assert await crawler._score(None) is None
    web.get_json.assert_not_awaited()
