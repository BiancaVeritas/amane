"""Hey動画 (heydouga.com) 单品种贩详情页爬虫.

番号 ``Heydouga-<提供元>/<作品>`` 直接对应详情页 ``/moviepages/<提供元>/<作品>/index.html``,
没有搜索接口: 两段数字本身就是地址, 不需要先搜再比对.

详情页把大部分字段放在 ``#movie-info > ul > li`` 的「标签：值」里, 但「配信期間」(限定配信)
与「配信日」(常规) 是两套写法, 取哪个要看具体作品, 因此按两种标签分别读. 标签 (本作标签)
由 ``/get_movie_tag_all/`` 的表单接口给出 —— 页面上的 ``#movie_tag_list`` 只是占位, 靠脚本填.
"""

from __future__ import annotations

import html as html_entities
import re
from urllib.parse import urlparse

from parsel import Selector

from ...enums import SiteName
from ...utils.dates import normalize_calendar_date
from ...utils.language import has_kana
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors

_BASE_URL = "https://www.heydouga.com"
#: 官方评分: 返回总票数与均分. 只打星不写字的票也算在内, 因此与评论列表的条数不同.
_RATING_PATH = "/ppv/movie_get_movie_rating/?movie_seq={}"
_MOVIE_PATH = "/moviepages/{provider}/{movie}/index.html"
#: 番号 → (提供元, 作品). 与 ``parsing/file_info.py`` 的 ``Heydouga-x-y`` 归一写法一致.
_NUMBER_RE = re.compile(r"^heydouga[-_]?(\d{2,4})[-_](\d{1,4})$", re.IGNORECASE)
#: 详情页地址 → (提供元, 作品). 手动贴地址时按它读番号.
_URL_RE = re.compile(r"/moviepages/(\d{2,4})/(\d{1,4})/")

#: 播放器缩略图是横版封面 (960×540), 站点没有提供竖版海报.
_THUMB_HOST = "https://image01-www.heydouga.com"

#: 发行商固定为站点本身: 「提供元」是片商 (各提供元各不相同), 发行方始终是 Hey動画.
PUBLISHER = "Heydouga"

#: 出演栏的占位词: 表示「还有其他人」, 不是演员名.
_NON_NAMES = frozenset({"他", "ほか", "他多数", "その他"})
#: 「はるな　他」这种写法要剥掉尾部的「他」, 但只有它独立成段 (前面有分隔) 时才算.
_TRAILING_OTHERS = re.compile(r"[\s　]+(?:他|ほか|他多数|その他)\s*$")
#: 标签接口回 ``\uXXXX`` 转义的 JSON, 按正则取字段前先把转义解回真字符.
_JSON_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")


def detail_url(number: str) -> str | None:
    """番号 → 详情页地址; 不是 heydouga 番号时返回 ``None``."""
    match = _NUMBER_RE.match(number.strip())
    if match is None:
        return None
    return f"{_BASE_URL}{_MOVIE_PATH.format(provider=match[1], movie=match[2])}"


def number_from_url(url: str) -> str | None:
    """详情页地址 → 番号; 地址里没有两段数字时返回 ``None``."""
    match = _URL_RE.search(urlparse(url).path)
    if match is None:
        return None
    return f"Heydouga-{match[1]}-{match[2]}"


def _clean(value: str | None) -> str | None:
    """站点正文里的实体 (``&#45;`` / ``&nbsp;``) 与多余空白都收掉."""
    if value is None:
        return None
    text = html_entities.unescape(value)
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _spec(html: Selector, *labels: str) -> str | None:
    """``#movie-info`` 里按标签读一个值, 多个标签按顺序取第一个非空.

    两套日期写法 (限定配信写「配信期間」, 常规写「配信日」) 用同一处读, 调用方按需列标签.
    """
    for label in labels:
        raw = html.xpath(
            f'//div[@id="movie-info"]//li[span[starts-with(normalize-space(.), "{label}")]]/span[2]//text()'
        ).getall()
        value = _clean(" ".join(raw))
        if value:
            return value
    return None


def _spec_names(html: Selector, label: str) -> list[str]:
    """同一个标签下可能挂多个 ``<a>`` (出演栏)."""
    values = html.xpath(
        f'//div[@id="movie-info"]//li[span[starts-with(normalize-space(.), "{label}")]]/span[2]//a/text()'
    ).getall()
    return [_clean(value) or "" for value in values]


def _spec_first_link(html: Selector, label: str) -> str | None:
    """取该标签下第一个 ``<a>`` 的文本.

    「提供元」的结构是 ``<a>厂牌</a>（ <a>公式サイト</a> ）`` 外加一个关注按钮,
    按 ``//text()`` 读会把「公式サイト」「フォロー」一起收进来.
    """
    for value in _spec_names(html, label):
        if value:
            return value
    return None


def _release(html: Selector) -> str | None:
    """限定配信写的是区间 (``2012-06-29 ～ 2026-11-26``), 取起始日; 常规作品直接是日期."""
    raw = _spec(html, "配信日", "配信期間", "公開日")
    if raw is None:
        return None
    start = re.split(r"[～~]", raw, maxsplit=1)[0]
    return normalize_calendar_date(start.strip().replace("/", "-"))


def _avg_rating(value: object) -> float | None:
    """把官方均分收成两位小数; 0 或缺省表示无人评分, 返回 ``None``.

    接口给字符串 (``"4.4000"``); 无人评分时站点回 0 —— 那时不给 0 分, 因为界面上「没人
    评分」与「评了 0 分」是两回事.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    if not isinstance(value, int | float) or value <= 0:
        return None
    return round(float(value), 2)


def _runtime(html: Selector) -> int | None:
    raw = _spec(html, "動画再生時間", "再生時間", "収録時間")
    if raw is None:
        return None
    match = re.search(r"(\d+)", raw)
    return int(match[1]) if match else None


def _actors(html: Selector) -> list[str]:
    """出演栏可能把「其他」写成一串名字, 逗号与顿号都要切; 末尾的「他」只是「还有别人」."""
    names: list[str] = []
    for raw in _spec_names(html, "主演") or _spec_names(html, "出演"):
        for name in re.split(r"[,、/]", raw):
            candidate = _TRAILING_OTHERS.sub("", name).strip()
            if candidate and candidate not in _NON_NAMES and candidate not in names:
                names.append(candidate)
    return names


#: 日文正文之后紧跟英文译文的交界: 句末标点 + 空白 + 大写拉丁字母.
#: 日文句子之间不留空白 (「。ポルノ・ダンは」), 因此不会误切; 译文之间用 ``. `` 分隔, 只取第一处.
_TRANSLATION_BOUNDARY = re.compile(r"(?<=[。．！？!?.])\s+(?=[A-Z])")


def _japanese_only(text: str) -> str:
    """截掉跟在后面的英文译文; 整段没有日文时返回空串.

    站点的写法有两种, 都要处理: 英文另起一段, 或者直接接在同一段正文之后
    (``…確かめてください。 Uncensored, Professor…``). 后者的段内没有段落边界,
    只能在第一个「句末 + 空白 + 大写字母」处切.
    """
    head = _TRANSLATION_BOUNDARY.split(text, maxsplit=1)[0].strip()
    return head if has_kana(head) else ""


def _plot(html: Selector) -> str | None:
    """正文在 ``div.movie-description > p``; 展开按钮的文案不在段落里.

    部分作品的同一块里还跟着英文译文 (与日文正文内容重复), 只留日文:
    逐段截掉译文, 一段日文都不剩时不存剧情 —— 存一段英文会让详情页在那儿显示重复内容.
    """
    paragraphs = html.xpath('//div[@id="movie-description"]//div[contains(@class,"movie-description")]/p')
    kept: list[str] = []
    for paragraph in paragraphs:
        text = _clean(" ".join(paragraph.xpath(".//text()").getall()))
        if not text:
            continue
        japanese = _japanese_only(text)
        if japanese:
            kept.append(japanese)
    return "\n\n".join(kept) or None


def _title_and_actors(html: Selector) -> tuple[str | None, list[str]]:
    """``og:title`` 的形态是 ``标题 - 出演者``; 演员另有字段, 标题不再重复带.

    只在尾段确实等于解析出的演员名时才切: 有些作品标题本身含 `` - ``,
    按分隔符硬切会把标题削掉一半.
    """
    raw = _clean(html.xpath('//meta[@property="og:title"]/@content').get())
    actors = _actors(html)
    if not raw:
        return None, actors
    head, sep, tail = raw.rpartition(" - ")
    if sep and actors and _TRAILING_OTHERS.sub("", tail).strip() == "、".join(actors):
        return _clean(head), actors
    return raw, actors


def _cover(html: Selector) -> str | None:
    """详情页 ``og:image`` 是播放器缩略图 (横版 960×540)."""
    raw = html.xpath('//meta[@property="og:image"]/@content').get()
    value = _clean(raw)
    if not value:
        return None
    # 同站相对地址 (``/contents/...``) 补上图片主机.
    if value.startswith("/"):
        return f"{_THUMB_HOST}{value}"
    return value


def _unescape_json(raw: str) -> str:
    """把 ``\\uXXXX`` 还原成真字符; 不解析整个 JSON, 只为按正则取名."""
    return _JSON_ESCAPE.sub(lambda m: chr(int(m[1], 16)), raw)


def _trailer(html: Selector, provider: str, movie: str) -> str | None:
    """样片是公开的 ``sample.mp4``; 没有样片的作品页面上不会出现它."""
    if f"/contents/{provider}/{movie}/sample.mp4" not in html.get():
        return None
    return f"{_BASE_URL}/contents/{provider}/{movie}/sample.mp4"


def _gallery(html: Selector) -> list[str]:
    """剧照在 ``#movie-gallery-images`` 的 fancybox 链接里, 路径 ``/image_gallery/{提供元}/{作品}/{n}.jpg``.

    站点按编号 **降序** 渲染 (30, 29, …), 而这里要的是播放顺序, 因此按编号升序重排;
    编号不是连续整数时不猜, 保持原始顺序.
    """
    hrefs = html.xpath('//div[@id="movie-gallery-images"]//a[contains(@class,"fancybox")]/@href').getall()
    urls: list[str] = []
    for href in hrefs:
        value = _clean(href)
        if value and value.startswith("/"):
            value = f"{_BASE_URL}{value}"
        if value and value not in urls:
            urls.append(value)
    numbers = [re.search(r"/(\d+)\.jpe?g$", url) for url in urls]
    if urls and all(match is not None for match in numbers):
        urls.sort(key=lambda url: int(re.search(r"/(\d+)\.jpe?g$", url)[1]))
    return urls


class HeydougaCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.HEYDOUGA, base_url=_BASE_URL, publisher=PUBLISHER)

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        """没有搜索接口: 番号即地址, 存在与否由详情页自己回答."""
        del options
        return detail_url(query.number)

    async def _score(self, movie_seq: str | None) -> float | None:
        """官方评分 (5 分制), 取自 ``/ppv/movie_get_movie_rating/``.

        这个均分把「只打星、不写字」的票也算进来, 而 ``/ppv/get_review_ajax/`` 只回带文字
        的评论 —— 实测 4386/101 是 10 票均分 4.4, 但评论只有 4 条且全为 4 分。页面上显示的
        是前者, 所以取官方均分, 不按评论列表自算.

        取不到就返回 ``None``: 评分是补充信息, 不该让整条刮削失败.
        """
        if movie_seq is None:
            return None
        try:
            data = await self.client.get_json(
                f"{self.base_url}{_RATING_PATH.format(movie_seq)}",
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            )
        except Exception as exc:
            self.logger.warning("评分接口不可用", movie_seq=movie_seq, error=str(exc))
            return None
        if not isinstance(data, dict):
            return None
        return _avg_rating(data.get("movie_rating_average"))

    async def _tags(self, movie_seq: str | None) -> list[str]:
        """标签走 ``/get_movie_tag_all/`` 表单接口, 页面上只有占位符.

        取不到就不带标签返回: 标签是补充信息, 不该让整条刮削失败.
        """
        if movie_seq is None:
            return []
        raw = await self.client.post_text(
            f"{self.base_url}/get_movie_tag_all/",
            data={"movie_seq": movie_seq, "provider_id": "3000", "lang": "ja"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        tags: list[str] = []
        for name in re.findall(r'"tag_name"\s*:\s*"([^"]*)"', _unescape_json(raw or "")):
            value = _clean(name)
            if value and value not in tags:
                tags.append(value)
        return tags

    async def scrape_url(self, url: str) -> MediaMetadata | None:
        number = number_from_url(url)
        return await self._scrape(detail_url(number)) if number else None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        del options
        match = _URL_RE.search(urlparse(url).path)
        if match is None:
            return None
        provider, movie = match[1], match[2]

        page = await self.client.get_html(url)
        if not page:
            return None
        html = Selector(text=page)

        title, actors = _title_and_actors(html)
        if not title:
            return None

        movie_seq = html.xpath("//script[contains(., 'var movie_seq')]/text()").re_first(r"var movie_seq\s*=\s*'(\d+)'")
        score = await self._score(movie_seq)
        tags = await self._tags(movie_seq)
        cover = _cover(html)
        series = _spec(html, "シリーズ")
        studio = _spec_first_link(html, "提供元") or _spec_first_link(html, "メーカー")

        return MediaMetadata(
            number=f"Heydouga-{provider}-{movie}",
            title=title,
            actors=film_actors(actors),
            studio=studio,
            publisher=PUBLISHER,
            series=series,
            series_names=[series] if series else [],
            release=_release(html),
            runtime=_runtime(html),
            tags=tags,
            plot=_plot(html),
            thumb_urls=[cover] if cover else [],
            extrafanart=_gallery(html),
            trailer_urls=[trailer] if (trailer := _trailer(html, provider, movie)) else [],
            score=score,
            source_url=url,
        )
