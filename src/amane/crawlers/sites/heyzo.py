"""HEYZO (heyzo.com) 单片详情页爬虫.

番号 ``HEYZO-<数字>`` 直接对应详情页 ``/movie/<数字>``, 没有搜索接口: 数字本身就是地址.
站点的「配信日」是 ``create_date``, 首段数字不足 4 位要补零 (``0683`` / ``1275``).

页面是 Next.js App Router: 数据不在 HTML 标签里, 而在内联的 RSC payload
(``self.__next_f.push([1, "..."])``) 里. 因此解析要先把这些字符串块拼起来、反转义, 再按
字段名取值 —— 照标签取会一个字段都拿不到.

站点固定无码 (``content_type`` 由番号推断为 UNCENSORED), 发行方就是站本身.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote, urlparse

from parsel import Selector

from ...enums import SiteName
from ...utils.dates import normalize_calendar_date
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors

_BASE_URL = "https://www.heyzo.com"
_MOVIE_PATH = "/movie/{movie_id}"
#: 番号 → 数字段. 与 ``parsing/file_info.py`` 的 ``HEYZO-<数字>`` 归一写法一致.
_NUMBER_RE = re.compile(r"^heyzo[-_]?(\d{3,})$", re.IGNORECASE)
#: 详情页地址 → 数字段; 手动贴地址时按它读番号.
_URL_RE = re.compile(r"/movie/(\d{3,})/?$")

PUBLISHER = "HEYZO"

#: 图片目录前缀; 后一级 ``_IMAGE_BUCKET`` 是运营编号, 与作品无关.
_CONTENTS = "https://www.heyzo.com/contents"
_IMAGE_BUCKET = "3000"


def detail_url(number: str) -> str | None:
    """番号 → 详情页地址; 不是 HEYZO 番号时返回 ``None``."""
    match = _NUMBER_RE.match(number.strip())
    if match is None:
        return None
    return f"{_BASE_URL}{_MOVIE_PATH.format(movie_id=match[1])}"


def number_from_url(url: str) -> str | None:
    """详情页地址 → 番号; 地址里没有数字段时返回 ``None``."""
    match = _URL_RE.search(urlparse(url).path)
    if match is None:
        return None
    return f"HEYZO-{match[1]}"


def _rsc_payload(text: str) -> str:
    """把内联 RSC 数据块拼成一段可搜索的文本.

    ``self.__next_f.push([1, "..."])`` 的第二个参数是 JSON 字符串字面量, 反转义后才看得见
    中文与嵌套的 ``"``; 分块本身是任意切开的, 必须按出现顺序拼接再解析.
    """
    chunks: list[str] = []
    for literal in re.findall(r'self\.__next_f\.push\(\[1,\s*("(?:[^"\\]|\\.)*")\]\)', text):
        try:
            chunks.append(json.loads(literal))
        except ValueError:
            continue
    return "".join(chunks)


def _movie_node(payload: str, movie_id: str) -> dict[str, Any] | None:
    """取 ``movie_id`` 对应的那个 movie 对象.

    payload 里同一个 id 会出现在多个节点 (推荐位也带 id), 只有详情节点带 ``movie_seq``
    与 ``name``; 取第一个同时具备两者的.
    """
    for match in re.finditer(r'\{"movie_seq":\d+,"movie_id":"[^"]*"', payload):
        start = match.start()
        node = _read_object(payload, start)
        if node is None:
            continue
        if str(node.get("movie_id")) == movie_id and node.get("name"):
            return node
    return None


def _read_object(text: str, start: int) -> dict[str, Any] | None:
    """从 ``start`` 处的 ``{`` 起读一个括号配平的 JSON 对象."""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : index + 1])
                except ValueError:
                    return None
    return None


def _search_int(payload: str, key: str) -> int | None:
    match = re.search(rf'"{re.escape(key)}":(\d+)', payload)
    return int(match.group(1)) if match else None


def _duration_seconds(payload: str) -> int | None:
    """取 ``download.whole[0].duration``; 旧片这里写 ``00:00:00``, 视为没有时长."""
    match = re.search(r'"duration":"(\d{1,2}):(\d{2}):(\d{2})"', payload)
    if match is None:
        return None
    hours, minutes, seconds = (int(part) for part in match.groups())
    total = hours * 3600 + minutes * 60 + seconds
    return total or None


def _actors(payload: str) -> list[str]:
    """演员名在互相引用的 actor 节点里, 按 ``actor_name`` 取."""
    names = re.findall(r'"actor_name":"([^"]+)"', payload)
    return [name for name in dict.fromkeys(names) if name]


def _score(node: dict[str, Any]) -> float | None:
    """用户评分 (5 分制), 取自 ``detail.rv_score``.

    无人评分时站点写 ``0``: 返回 ``None`` 而不是 0.0 —— 「没人评分」与「评了 0 分」在展示上
    是两回事, 前者不该占掉一档.
    """
    detail = node.get("detail")
    if not isinstance(detail, dict):
        return None
    raw = detail.get("rv_score")
    if isinstance(raw, int | float) and raw > 0:
        return round(float(raw), 2)
    return None


def _categories(payload: str) -> list[str]:
    """标签 = 分类名. 同一批关键词里也混着运营开关 (``op_flag_*``), 只取有成品名的."""
    tags: list[str] = []
    for name, key in re.findall(
        r'"category_name":"([^"]*)","category_name_en":"[^"]*","category_key_name":"([^"]*)"', payload
    ):
        if not name or key.startswith("op_flag_"):
            continue
        if name not in tags:
            tags.append(name)
    return tags


def _gallery(html: Selector, movie_id: str) -> list[str]:
    """剧照: ``contents/3000/<id>/gallery/<编号>.jpg``, 编号从页面上的缩略图读.

    页面上渲染的是 ``gallery/thumbnail_<编号>.jpg`` (小图), 同编号的 ``gallery/<编号>.jpg``
    才是大图 —— 抓小图会让剧照糊成一团. 编号不连号也不能靠数量猜, 因此逐个从页面取.
    """
    root = _image_root(movie_id, None)
    numbers: list[str] = []
    for src in html.xpath("//img/@src").getall():
        # Next.js 的图片优化地址把真实路径塞在 ``url=`` 查询参数里, 需要解码后才看得见.
        for match in re.finditer(r"gallery/thumbnail_(\d+)\.jpg", unquote(src)):
            if match.group(1) not in numbers:
                numbers.append(match.group(1))
    return [f"{root}/gallery/{number}.jpg" for number in numbers]


def _image_root(movie_id: str, movie_seq: int | None) -> str:
    """图片目录前缀.

    ``contents`` 下那一级是运营编号 (实测全部为 ``3000``), 与 ``movie_seq`` 无关 ——
    照 seq 推算会拼出不存在的地址, 图片全 404.
    """
    del movie_seq
    return f"{_CONTENTS}/{_IMAGE_BUCKET}/{movie_id}"


def _plot(node: dict[str, Any]) -> str | None:
    detail = node.get("detail")
    if not isinstance(detail, dict):
        return None
    memo = detail.get("memo")
    return memo.strip() or None if isinstance(memo, str) else None


def _release(node: dict[str, Any]) -> str | None:
    """发行日期: ``create_date`` 是站点的「配信日」."""
    raw = node.get("create_date")
    if not isinstance(raw, str):
        return None
    # RSC 把 Date 序列化成 ``$D2026-09-01T18:21:53.000Z``; 去掉前缀再交给日期归一.
    return normalize_calendar_date(raw.removeprefix("$D")[:10])


class HeyzoCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.HEYZO, base_url=_BASE_URL, publisher=PUBLISHER)

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        """没有搜索接口: 番号即地址, 存在与否由详情页自己回答."""
        del options
        return detail_url(query.number)

    async def scrape_url(self, url: str) -> MediaMetadata | None:
        number = number_from_url(url)
        return await self._scrape(detail_url(number)) if number else None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        del options
        match = _URL_RE.search(urlparse(url).path)
        if match is None:
            return None
        movie_id = match[1]

        text = await self.client.get_text(url)
        if not text:
            return None
        payload = _rsc_payload(text)
        # 剧照相册只在 HTML 的 <img> 里, payload 中没有成品地址, 所以两处都要解析.
        html = Selector(text=text)
        node = _movie_node(payload, movie_id)
        if node is None:
            return None

        title = node.get("name")
        if not isinstance(title, str) or not title.strip():
            return None

        root = _image_root(movie_id, None)
        # 竖版封面 (站点只提供这一种), 播放器缩略图是横版, 留给剧照位.
        thumb = f"{root}/images/thumbnail.jpg"

        return MediaMetadata(
            number=f"HEYZO-{movie_id}",
            title=title.strip(),
            actors=film_actors(_actors(payload)),
            studio=PUBLISHER,
            publisher=PUBLISHER,
            release=_release(node),
            runtime_seconds=_duration_seconds(payload),
            tags=_categories(payload),
            plot=_plot(node),
            score=_score(node),
            thumb_urls=[thumb],
            extrafanart=_gallery(html, movie_id),
            source_url=url,
        )
