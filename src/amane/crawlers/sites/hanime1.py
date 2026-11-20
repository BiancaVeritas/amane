import re
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from parsel import Selector

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery

_BRACKETS = {
    "[": "]",
    "(": ")",
    "{": "}",
    "【": "】",
    "（": "）",
    "〔": "〕",
    "［": "］",
    "｛": "｝",
    "「": "」",
    "『": "』",
    "《": "》",
    "〈": "〉",
    "<": ">",
}
_REVERSE_BRACKETS = {right: left for left, right in _BRACKETS.items()}


def _remove_leading_bracket_group(text: str) -> str:
    if not text or text[0] not in _BRACKETS:
        return text

    stack: list[str] = []
    for index, char in enumerate(text):
        if char in _BRACKETS:
            stack.append(_BRACKETS[char])
        elif stack and char == stack[-1]:
            stack.pop()
            if not stack:
                return text[index + 1 :].strip()

    return text


def _remove_trailing_bracket_group(text: str) -> str:
    if not text or text[-1] not in _REVERSE_BRACKETS:
        return text

    stack: list[str] = []
    for index in range(len(text) - 1, -1, -1):
        char = text[index]
        if char in _REVERSE_BRACKETS:
            stack.append(_REVERSE_BRACKETS[char])
        elif stack and char == stack[-1]:
            stack.pop()
            if not stack:
                return text[:index].strip()

    return text


def clean_search_title(value: str) -> str:
    """仅供 Hanime1 使用：清除文件名首尾括号信息及无关标记。"""
    text = value.strip()

    while True:
        previous = text
        text = _remove_leading_bracket_group(text)
        text = _remove_trailing_bracket_group(text)
        if text == previous:
            break

    # -C / -c 必须带连字符才能匹配，只删除固定词组，不限制后续内容。
    # 独立的 C/c 不会被删除。
    text = re.sub(r"-C", "", text, flags=re.IGNORECASE)

    # 有码、无码只删除带连字符的形式。
    # DVDRIP、BDRIP、分辨率和 K 数标记，带或不带连字符都删除。
    # 固定词组出现即删除，不限制前后是否还有其他字符。
    removable = re.compile(
        r"-(?:有码|无码|DVDRIP|BDRIP|\d{3,4}P|\d{1,2}K)"
        r"|(?:DVDRIP|BDRIP|\d{3,4}P|\d{1,2}K)",
        flags=re.IGNORECASE,
    )
    text = removable.sub("", text)

    # 合并清理产生的空白，但保留标题中正常使用的连字符。
    return re.sub(r"\s+", " ", text).strip()


def _search_keyword(query: SearchQuery) -> str:
    # Hanime1 只能通过本地文件名提取标题检索，不允许回退到番号。
    if not query.hanime1_file_path:
        return ""
    return clean_search_title(Path(query.hanime1_file_path).stem)


class Hanime1Crawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(
            name=SiteName.HANIME1,
            base_url="https://hanime1.me",
            urls=["https://hanime1.me"],
        )

    async def _search(
        self,
        query: SearchQuery,
        options: FetchOptions | None = None,
    ) -> str | None:
        keyword = _search_keyword(query)
        if not keyword:
            return None

        search_url = f"{self.base_url}/search?{urlencode({'query': keyword})}"
        text = await self.client.get_html(
            search_url,
            headers=self.headers,
            cookies=self.cookies,
        )
        if not text:
            return None

        html = Selector(text=text)
        links = html.css(".video-item-container a.video-link::attr(href)").getall()

        # 只接受真正的视频详情链接，排除搜索页中的广告链接。
        for link in links:
            full_url = urljoin(self.base_url, link)
            parsed = urlparse(full_url)
            video_ids = parse_qs(parsed.query).get("v", [])

            if parsed.path.rstrip("/") == "/watch" and video_ids:
                return full_url

        return None

    async def fetch(
        self,
        query: SearchQuery,
        options: FetchOptions | None = None,
    ) -> MediaMetadata | None:
        # 必须保留 Amane 原始作品身份，不能把 Hanime1 视频 ID 当作番号。
        url = await self._search(query, options)
        if not url:
            return None

        text = await self.client.get_html(
            url,
            headers=self.headers,
            cookies=self.cookies,
        )
        if not text:
            return None

        html = Selector(text=text)
        tags: list[str] = []

        for item in html.css(".video-tags-wrapper .single-video-tag"):
            # 只读取标签链接的直接文本，排除内部的数量统计 span。
            tag = "".join(item.xpath("./a/text()").getall()).strip()
            tag = tag.lstrip("#").strip()

            if not tag or tag.casefold() in {"add", "remove"}:
                continue

            tags.append(tag)

        return MediaMetadata(
            number=query.number,
            tags=tags,
            source_url=url,
        )

    async def _scrape(
        self,
        url: str,
        options: FetchOptions | None = None,
    ) -> MediaMetadata | None:
        # Hanime1 需要在 fetch 中保留 SearchQuery.number，
        # 因此由上面的 fetch 完成详情页解析。
        return None
