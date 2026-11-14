"""通用页面解析: 任何链接都能打开, 从页面里认出番号与标题.

不注册进 ``SiteName``, 也不出现在类型路由里 —— 它没有固定站点, 只在「用户贴了一条
没有专属爬虫的地址」时兜底. 站点专属爬虫知道自己的字段在哪, 这个只能靠通用特征猜:
番号取页面里出现最多的那个像番号的串, 标题取 ``og:title`` 或 ``<title>``.
"""

from __future__ import annotations

import re
from collections import Counter

from parsel import Selector

from ...parsing import ContentType, parse_file_info
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata

#: 番号样式的候选: 字母前缀 + 数字, 或日期_序号 (020616_348).
_NUMBER_PATTERNS = (
    re.compile(r"\b([A-Za-z]{2,6})[-_ ]?(\d{2,5})\b"),
    re.compile(r"\b(\d{6})_(\d{3})\b"),
    re.compile(r"\b(FC2)[-_ ]?(?:PPV[-_ ]?)?(\d{6,8})\b", re.IGNORECASE),
)

#: 页面上常见的非番号字母数字串, 别让它们混进候选.
_NOISE = frozenset(
    {
        "html",
        "http",
        "https",
        "www",
        "com",
        "net",
        "jpg",
        "jpeg",
        "png",
        "webp",
        "mp4",
        "m3u8",
        "php",
    }
)


def _candidates(text: str) -> list[str]:
    """从一段文本里抠出所有像番号的串.

    直接用匹配到的原文, 不按捕获组重拼: 重拼会把 ``020616_348`` 变成 ``020616348``,
    那已不是番号, 后续类型判定与别的站都认不出来.
    """
    found: list[str] = []
    for pattern in _NUMBER_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0).strip()
            if value.casefold() in _NOISE:
                continue
            found.append(value.upper())
    return found


def _pick_number(selector: Selector, url: str) -> str | None:
    """在页面与地址里挑一个最像番号的值.

    先看地址末段 (站点常把番号放在路径里), 再看页面正文; 多个候选时取出现次数最多的,
    并列时取更长的那个 —— 较长的更可能是完整番号.
    """
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    for candidate in _candidates(tail):
        info = parse_file_info(candidate)
        if info.number == candidate:
            return candidate

    counter: Counter[str] = Counter(_candidates(selector.get()))
    if not counter:
        return None
    return max(counter.items(), key=lambda kv: (kv[1], len(kv[0])))[0]


class GenericPageCrawler(Crawler):
    """认不出站点时用它把页面读一遍."""

    @classmethod
    def profile(cls) -> CrawlerProfile:
        # base_url 留空: 它不对应任何站点, 匹配逻辑不会拿它去比域名.
        return CrawlerProfile(name="generic", base_url="")

    async def _search(self, query, options: FetchOptions | None = None) -> str | None:
        # 通用爬虫不做站内搜索: 它只会被要求解析一条用户给定的地址.
        return None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        del options
        html = await self.client.get_html(url, cookies=self.cookies or None)
        if not html:
            return None
        selector = Selector(text=html)

        title = (
            selector.xpath("string(//meta[@property='og:title']/@content)").get()
            or selector.xpath("string(//title)").get()
            or ""
        ).strip()

        number = _pick_number(selector, url)
        if not number and not title:
            return None
        content_type = parse_file_info(number).content_type if number else ContentType.CENSORED

        poster = selector.xpath("string(//meta[@property='og:image']/@content)").get() or None
        return MediaMetadata(
            number=number or "",
            title=title or None,
            content_type=content_type,
            source_url=url,
            poster_urls=[poster] if poster else [],
            thumb_urls=[poster] if poster else [],
        )
