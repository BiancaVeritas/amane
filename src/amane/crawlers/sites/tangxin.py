"""糖心 Vlog 刮削器.

站点结构:
- 详情页 ``/v/{id}/`` 直接给出标题、演员、时长、标签、日期与封面 (``og:image``).
- 搜索页 ``/search/?q=`` 是纯前端过滤 —— 它把整份索引拉下来在浏览器里打分排序,
  因此抓 HTML 永远拿不到结果. 数据源是 ``/search-index.json``, 一条记录是
  ``[id, 标题, 演员, 标签, 时长, 描述]`` 的数组.

站内没有番号体系, ``id`` 就是唯一标识. 搜索按标题做: 去掉标点与开头残留符号后
匹配索引里的标题 (与站点自身的打分口径一致 —— 它也把标点当分隔符).
"""

from __future__ import annotations

import json
import re
from typing import ClassVar
from urllib.parse import quote

from parsel import Selector

from ...enums import SiteName
from ...observability import current
from ...utils.language import has_han
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors

_BASE_URL = "https://tangxinvlog.app"
_INDEX_URL = f"{_BASE_URL}/search-index.json"
_MEDIA_BASE = "https://t.5gcdn.xyz/videos"

#: 制作商与发行商都固定为站点本身: 站内没有厂牌字段, 原创内容只属于糖心.
_STUDIO = "糖心Vlog"

#: 详情页地址里的 id: 长短两种形态都有 (``6aef1a3558ccd9a2e6045645`` / ``29570``).
_ID_RE = re.compile(r"/v/([0-9a-z]+)/?", re.IGNORECASE)

#: 标点与各种分隔符号. 搜索时统一折成空格.
_PUNCT_RE = re.compile(r"[\s\u3000·・.,;:!?()\[\]{}<>\"'`~@#$%^&*_+=|\\/—–\-…、。，；：！？（）【】「」『』《》〔〕]+")

#: 索引里的一行: ``[id, 标题, 演员, 标签, 时长, 描述]``.
_Row = list[object]

#: 索引快照. 站点每版更新索引地址带不同的 ``?v=``, 内容随刮削周期变化, 进程内缓一次即可.
_index_cache: list[_Row] | None = None


def _normalize_search_text(value: str) -> str:
    """标题折成搜索用的形态: 标点变空格, 去首尾残留符号, 合并多余空格."""
    folded = _PUNCT_RE.sub(" ", value)
    return " ".join(folded.split())


#: 文件名里混进来的站点域名 (``489155.com``) 与"Up主"之类的前缀标签.
_SITE_DOMAIN_RE = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}\s*", re.IGNORECASE)
_UPLOADER_TAG_RE = re.compile(r"^@?\s*(?:糖心\s*Vlog\s*)?Up\s*主\s*", re.IGNORECASE)


def clean_search_title(value: str, actor: str | None = None) -> str:
    """把下载来的文件名式标题收成可搜索的标题.

    这类标题前面常挂站点域名与「糖心VlogUp主」标签, 后面跟着演员的方括号名, 再后面才是
    真正的标题. 站点索引里没有这些前缀, 原样拿去搜一条都命中不了, 所以先剥掉:

    - 开头的域名 (``489155.com``)
    - 「糖心Vlog」「Up主」这类发布者标签
    - 开头的演员名 (``温温兔`` / ``【温温兔】`` / ``温温兔-`` 都剥)
    - 结尾的分辨率/编码标记 (``[720p]`` ``-1080p`` ``x264``)
    - 文件扩展名

    ``actor`` 只剥**开头**的那一处, 且剥离后必须还有内容: 演员名本身常是普通词 (玫瑰、
    小鹿), 出现在标题中段时可能是作品内容的一部分, 一并删掉会误伤.
    """
    text = value.strip()
    # 扩展名先去掉: 后面按分隔符切分时不该把它当标题的一部分.
    text = re.sub(r"\.(?:mp4|mkv|avi|wmv|mov|ts|m2ts|iso)$", "", text, flags=re.IGNORECASE)
    text = _SITE_DOMAIN_RE.sub("", text)
    text = _UPLOADER_TAG_RE.sub("", text)
    # 站名本身不进标题: 索引里的标题没有「糖心」二字, 带着它一条都匹配不上.
    text = re.sub(r"糖\s*心(?:\s*Vlog)?", " ", text, flags=re.IGNORECASE)
    if actor:
        stripped = _strip_leading_actor(text, actor)
        if stripped:
            text = stripped
    # 分辨率与编码标记常出现在任意位置, 去掉后不留多余分隔符.
    text = re.sub(r"[\[【(（]?\s*(?:480|720|1080|2160|4k)\s*[pPiI]\s*[\]】)）]?", " ", text)
    text = re.sub(r"\b(?:x264|x265|h264|h265|hevc|aac)\b", " ", text, flags=re.IGNORECASE)
    # 去掉标记后常留下孤立的连接符, 收尾处一并清掉.
    text = text.strip(" \t-_.·・")
    return _normalize_search_text(text)


#: 演员名与标题之间的分隔符.
_ACTOR_SEPARATORS = r"[-–—_·・|｜:：,，、\s]+"

#: 包住演员名的括号对; 括号本身就是边界, 后面不必再有分隔符.
_BRACKET_PAIRS: tuple[tuple[str, str], ...] = (
    ("【", "】"),
    ("[", "]"),
    ("［", "］"),
    ("(", ")"),
    ("（", "）"),
    ("「", "」"),
    ("『", "』"),
)


def _strip_leading_actor(text: str, actor: str) -> str:
    """剥掉标题开头的演员名及其包裹括号/分隔符; 剥完为空则原样返回.

    变体都覆盖: ``温温兔-标题`` / ``【温温兔】标题`` / ``[温温兔] 标题`` / ``温温兔 标题``.

    括号形态与裸名分开处理: 括号内的名字后面可以紧跟标题 (``【温温兔】某标题``), 括号本身
    就是边界; 裸名则必须靠分隔符隔开, 否则「玫瑰之夜」这种以演员名开头的标题会被砍成
    「之夜」.
    """
    name = re.escape(actor)
    # 1) 带括号: 括号本身就是边界, 后面的分隔符可有可无.
    for opener, closer in _BRACKET_PAIRS:
        pattern = rf"^\s*{re.escape(opener)}\s*{name}\s*{re.escape(closer)}"
        stripped = re.sub(pattern, "", text, count=1)
        if stripped != text and stripped.strip():
            return stripped.strip(" \t-_.·・")
    # 2) 裸名: 后面必须有分隔符.
    pattern = rf"^\s*{name}\s*{_ACTOR_SEPARATORS}"
    stripped = re.sub(pattern, "", text, count=1)
    return stripped.strip() or text


def _number_from_url(url: str) -> str | None:
    match = _ID_RE.search(url)
    return match.group(1) if match else None


#: 判断"一个字"的范围: 汉字、假名、字母数字都算一个可拆分的字.
_CHAR_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff0-9a-zA-Z]")


def spaced_title(value: str) -> str:
    """标题按字加空格: ``淫荡的家庭护士`` → ``淫 荡 的 家 庭 护 士``.

    站内搜索按空格切词元, 中文标题不切分就只有一个巨大的词元, 任何一条索引都匹配不上.
    这里逐字拆开, 让「词元全覆盖」退化成"每个字都出现", 与站点的模糊匹配口径一致.
    """
    return " ".join(_CHAR_RE.findall(value))


#: 索引匹配的最低覆盖率. 文件名标题常比站点标题多几个字, 全命中太严; 太低又会认错片.
_MIN_MATCH_RATIO = 0.6

#: 站点在没有简介时填的占位文案, 不是内容.
_EMPTY_PLOT_MARKERS = frozenset({"当前暂无简介", "暂无简介", "暂无介绍"})

#: 认定为本站内容的字样: 库路径或文件名里带它才算.
_TANGXIN_MARKERS = ("糖心", "tangxin")

#: 番号形态: 只由字母、数字与分隔符组成, 且至少含一段数字.
#:
#: 覆盖面比枚举各种站内写法更宽: ``ABC-123`` / ``FC2-PPV-1234567`` / ``n1234`` /
#: ``010115-001`` / ``heydouga-4037-1002`` 全都命中. 枚举写法必然漏 (heydouga 就是
#: 「字母-数字-数字」两段, 单段规则抓不到).
_NUMBER_SHAPED_RE = re.compile(r"^(?=.*\d)[A-Za-z0-9][A-Za-z0-9._-]*$")


def _is_number_shaped(value: str) -> bool:
    """这个搜索词看起来是番号而不是标题.

    判据是「不含汉字 + 含数字 + 只有字母数字与分隔符」. 中文标题无论怎么拆分都带汉字,
    所以这条不会误伤糖心自己的内容; 反过来, 任何带数字的纯 ASCII 串都当番号, 不放过
    糖心没有的写法.
    """
    text = value.strip()
    if not text or has_han(text):
        return False
    return bool(_NUMBER_SHAPED_RE.fullmatch(text))


def _looks_like_tangxin(file_path: str | None, number: str) -> bool:
    """这个搜索词是否属于糖心的内容.

    两道门槛:

    1. **只接中文标题**: 番号形态的输入一律跳过. 糖心没有番号体系, 拿番号问它必然命中不了,
       而索引比对是按字覆盖率算的, 番号里的字母数字会撞上无关作品的标题 —— 那种误命中比
       搜不到更糟.
    2. **有文件路径时只看路径**: 库目录名或文件名带「糖心」才算本站内容, 否则库里任何一条
       中文标题都会被拿去糖心索引里比对, 碰巧命中就把元数据写歪.

    没有路径时不做第 2 道: 手动搜索是用户主动要查, 拦下来等于让人搜不到 —— 那种场景下
    用户已经用搜索结果证明了自己的意图, 由他挑选哪一条.
    """
    if _is_number_shaped(number):
        return False
    if not file_path:
        return True
    haystack = file_path.casefold()
    return any(marker in haystack for marker in _TANGXIN_MARKERS)


def _parse_runtime(value: str) -> int | None:
    """``24:45`` → 分钟数, 与站内其它来源的口径一致 (向下取整)."""
    match = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})", value.strip())
    if match is None:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    total = hours * 3600 + minutes * 60 + seconds
    return round(total / 60) or None


def _parse_iso_duration(value: str) -> int | None:
    """``PT46M27S`` → 分钟数."""
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value.strip(), re.IGNORECASE)
    if match is None:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    total = hours * 3600 + minutes * 60 + seconds
    return round(total / 60) or None


def _text_of(value: object) -> str:
    """把结构化数据里的值收成字符串; 非字符串一律视为空."""
    return value.strip() if isinstance(value, str) else ""


def _video_object(html: str) -> dict:
    """页面里的 schema.org ``VideoObject``; 没有时返回空字典.

    站点把标题/简介/时长/日期/标签/演员都写在这份结构化数据里, 比挑 HTML 节点稳 ——
    站点改版只影响布局, 不影响这份数据.
    """
    for block in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "VideoObject":
                return item
    return {}


def _description_nodes(sel: Selector) -> list[str]:
    """页面上的简介段落文本."""
    out: list[str] = []
    for node in sel.xpath('//p[contains(@class,"description")]'):
        text = "".join(node.xpath(".//text()").getall()).strip()
        if text:
            out.append(text)
    return out


class TangxinCrawler(Crawler):
    """糖心 Vlog: 标题搜索 + 详情页解析."""

    #: 索引只拉一次: 站点整份索引近 2MB, 每次搜索都重下一次没有必要.
    _index: ClassVar[list[_Row] | None] = None

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.TANGXIN, base_url=_BASE_URL, publisher=_STUDIO)

    async def _load_index(self) -> list[_Row]:
        """整份搜索索引, 进程内复用."""
        if TangxinCrawler._index is not None:
            return TangxinCrawler._index
        data = await self.client.get_json(_INDEX_URL, headers={"Accept": "application/json"})
        rows = [row for row in data if isinstance(row, list) and len(row) >= 2] if isinstance(data, list) else []
        TangxinCrawler._index = rows
        return rows

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        """按标题找详情页地址.

        两级: 先在整份索引里按词元匹配 (快, 一次请求); 索引里没有时再走站内搜索页
        ``/search/?q=`` —— 站点的索引只收精选条目, 新片与冷门片可能还没进索引.
        """
        del options
        # 只处理「糖心」相关的文件: 库路径或文件名带这两个字时才算本站内容.
        #
        # 糖心是国产原创站, 没有番号体系, 只能靠标题认片. 若不加这道门槛, 库里任何一条
        # 中文标题都会被拿去糖心索引里比对, 命中一条不相干的片就把元数据写歪了.
        if not _looks_like_tangxin(query.file_path, query.number):
            current().debug("not a tangxin file, skip", path=query.file_path, number=query.number[:30])
            return None

        rows = await self._load_index()

        raw = clean_search_title(query.number)
        if not raw:
            return None

        best: tuple[float, int] | None = None
        best_id: str | None = None
        for order, row in enumerate(rows):
            raw_id = row[0]
            raw_title = row[1]
            if not isinstance(raw_id, str) or not isinstance(raw_title, str):
                continue
            # 这一条的演员名: 文件名常写成 ``温温兔-某标题``, 而索引标题只有后半段,
            # 用本条自带的演员名剥掉开头, 两边才在同一口径上比.
            row_actor = row[2].strip() if len(row) > 2 and isinstance(row[2], str) else ""
            text = clean_search_title(query.number, row_actor or None)
            if not text:
                continue
            terms = spaced_title(text).casefold().split()
            if not terms:
                continue
            title = spaced_title(raw_title).casefold()
            hit = sum(1 for term in terms if term in title)
            if hit == 0:
                continue
            # 覆盖率而非全命中: 文件名标题常比站点标题多几个字, 要求逐字全覆盖会把
            # 明明存在的那条排除掉. 命中比例高者优先, 同比例时取短标题与更靠前的位置.
            ratio = hit / len(terms)
            if ratio < _MIN_MATCH_RATIO:
                continue
            candidate = (ratio, 1000 - len(title))
            if best is None or candidate > best:
                best = candidate
                best_id = raw_id
        if best_id:
            return f"{_BASE_URL}/v/{best_id}/"

        # 索引未命中: 交给站内搜索页, 由页面给出最接近的一条.
        return await self._search_site(raw)

    async def _search_site(self, text: str) -> str | None:
        """索引里没有时走站内搜索页, 从结果里取第一条详情链接."""
        search_url = f"{_BASE_URL}/search/?q={quote(spaced_title(text))}"
        html = await self.client.get_html(search_url)
        if not html:
            return None
        sel = Selector(text=html)
        href = sel.xpath('//a[contains(@href,"/v/")]/@href').get()
        number = _number_from_url(href) if href else None
        return f"{_BASE_URL}/v/{number}/" if number else None

    async def scrape_url(self, url: str) -> MediaMetadata | None:
        """站点地址直接解析."""
        number = _number_from_url(url)
        return await self._scrape(f"{_BASE_URL}/v/{number}/") if number else None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        del options
        number = _number_from_url(url)
        if number is None:
            return None
        html = await self.client.get_html(url)
        if not html:
            return None
        sel = Selector(text=html)

        # 优先用页面里的 schema.org ``VideoObject``: 它把标题/简介/时长/日期/标签/演员
        # 都写成了结构化字段, 比从 HTML 里挑节点稳. HTML 只作兜底 —— 站点改版时节点会变,
        # 结构化数据不会.
        video = _video_object(html)

        title = _text_of(video.get("name"))
        if not title:
            title = sel.xpath('//meta[@property="og:title"]/@content').get()
            if title:
                # og:title 带站点后缀与演员前缀: ``标题 - @演员 - 糖心Vlog在线观看``.
                title = re.split(r"\s+-\s+", title.strip())[0].strip() or None
        if not title:
            title = (sel.xpath("//h1//text()").get() or "").strip() or None

        # 演员: 结构化数据里的 creator; 没有时退回详情区的 ``/a/`` 链接.
        #
        # 不能按全页取 ``/a/`` 链接: 「相关视频」区块里每个推荐片的演员都是同一个形状,
        # 一起抓会把十几个不相干的演员挂到本条上; 导航栏那个链接的文本还是「演员」二字.
        actors: list[str] = []
        creator = video.get("creator")
        for item in creator if isinstance(creator, list) else [creator]:
            name = _text_of(item.get("name")) if isinstance(item, dict) else _text_of(item)
            if name and name not in actors:
                actors.append(name)
        if not actors:
            for node in sel.xpath("//h1/..").xpath('.//a[starts-with(@href,"/a/")]'):
                text = "".join(node.xpath(".//text()").getall()).strip()
                if not text.startswith("@"):
                    continue
                cleaned = text.lstrip("@").strip()
                if cleaned and cleaned not in actors:
                    actors.append(cleaned)

        # 标题开头的演员名去掉: 站点把演员写进了标题 (``温温兔-淫荡的家庭护士...``),
        # 而演员已经单独成字段, 留在标题里就是重复. 只剥开头那一处, 用本条抓到的演员名比对,
        # 「玫瑰之夜」这类以演员名开头的普通词不会被误砍 (见 _strip_leading_actor).
        #
        if title:
            for actor_name in actors:
                stripped = _strip_leading_actor(title, actor_name)
                if stripped != title:
                    title = stripped
                    break

        # 本站没有番号体系: 剥离演员名后的标题同时作为 number 与 title.
        # 两者一致时 NFO 的 ``_title_with_number`` 会去重, 不会写成「番号 标题」两遍.
        display_number = title or number

        # 时长: ``PT46M27S`` (ISO 8601); 退回页面上的 ``46:27``.
        runtime = _parse_iso_duration(_text_of(video.get("duration")))
        if runtime is None:
            meta_line = " ".join(t.strip() for t in sel.xpath("//body//text()").getall() if t.strip())
            runtime_match = re.search(r"(?<!\d)(\d{1,3}:\d{2}(?::\d{2})?)(?!\d)", meta_line)
            runtime = _parse_runtime(runtime_match.group(1)) if runtime_match else None

        # 日期: ``2026-10-23T00:00:00+08:00`` 取日期段.
        release = _text_of(video.get("uploadDate"))
        release = release[:10] if release and len(release) >= 10 else None
        if not release:
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", " ".join(sel.xpath("//body//text()").getall()))
            release = date_match.group(1) if date_match else None

        # 简介: 站点没有独立简介时把标题填进 description, 或写「当前暂无简介」——
        # 这两种都不是内容, 与标题相同或命中占位文案时不留.
        plot: str | None = None
        for candidate in (_text_of(video.get("description")), *_description_nodes(sel)):
            text = (candidate or "").strip()
            if not text or text in _EMPTY_PLOT_MARKERS:
                continue
            if title and text == title:
                continue
            plot = text
            break

        # 标签: 结构化数据里是逗号分隔的一串; 退回页面上的 ``/tag/`` 链接.
        tags: list[str] = []
        keywords = _text_of(video.get("keywords"))
        if keywords:
            tags = [t.strip() for t in keywords.split(",") if t.strip()]
        if not tags:
            tags = [t.strip() for t in sel.xpath('//ul[contains(@class,"tags")]//a//text()').getall() if t.strip()]
        if not tags:
            tags = [t.strip() for t in sel.xpath('//a[contains(@href,"/tag/")]//text()').getall() if t.strip()]

        cover = _text_of(video.get("thumbnailUrl"))
        if not cover:
            cover = sel.xpath('//meta[@property="og:image"]/@content').get()
            cover = cover.strip() if cover else None
        if not cover:
            cover = f"{_MEDIA_BASE}/{number}/cover.jpg"

        return MediaMetadata(
            number=display_number,
            title=title,
            actors=film_actors(actors),
            tags=list(dict.fromkeys(tags)),
            studio=_STUDIO,
            publisher=_STUDIO,
            release=release,
            runtime=runtime,
            plot=plot,
            # 封面与海报同源: 站点的 cover.jpg 是横版 640×360, 竖版海报交给物化阶段裁.
            poster_urls=[cover],
            original_poster_urls=[cover],
            thumb_urls=[cover],
            source_url=url,
        )
