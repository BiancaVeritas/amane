"""演员名字行: 名字后面跟一到多个括号组, 最右边一组是读音, 其余是注记.

读音写作「假名 / 罗马字」并作为别名; 注记多为名字来源站点 (如 ``相川美羽(tenshigao)``),
不进别名. 站点名也会被填进读音的位置, 用同页标签行 (演员所属站点与系列) 比对后丢弃.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from parsel import Selector

from amane.enums import ActorGender, SiteName
from amane.mappings.actor_store import ActorMappingStore
from amane.net.connectivity import ConnectivityOutcome
from amane.net.errors import FailureReason, SourceError
from amane.parsing import split_actor_aliases
from amane.plugins.models import SourceCapability
from amane.utils.dates import normalize_calendar_date

from ...base import CrawlerProfile
from ...parsing import extract_text, is_same_name, normalize_name
from ..base import ActorCrawler
from ..models import ActorMetadata

# 名字后面可能有多个括号组; 从右往左剥, 只有最右边一组是读音, 其余都是注记.
_TRAILING_GROUP_RE = re.compile(r"^(?P<head>.*?)\s*[（(]\s*(?P<inner>[^（）()]*?)\s*[）)]\s*$")
# 读音只由假名或拉丁字母构成; 站点名、系列名常带汉字或数字.
_READING_RE = re.compile(r"^(?:[ぁ-んァ-ヶー・\s]+|[A-Za-z0-9][A-Za-z0-9 .'\-]*)$")
_READING_SEPARATOR_RE = re.compile(r"[/／]")
# T/B/W/H 缺值时标号仍留在行内, 逐项取值: 整行匹配会让一个空位带走其余各项.
_FIGURE_RE = re.compile(r"(?<![A-Za-z])(?P<key>[TBWH])\s*(?P<value>\d+)", re.IGNORECASE)
_CUP_RE = re.compile(r"([A-Z]+)カップ", re.IGNORECASE)
_ACTRESS_HREF_RE = re.compile(r"actress\d+\.html", re.IGNORECASE)


class MinnanoActorCrawler(ActorCrawler):
    # Minnano 的映射与站内检索只使用演员展示名, 不尝试数据库别名.
    primary_name_only = True

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(
            name=SiteName.MINNANO,
            base_url="https://www.minnano-av.com",
            capabilities=frozenset({SourceCapability.ACTOR_PROFILE}),
            genders=frozenset({ActorGender.FEMALE}),
            # 站点用 JS 注入年龄确认弹层, 仅在缺 cookie 时出现; 预置后页面不再被拦截判定命中.
            cookies={"age_verified": "1"},
        )

    def __init__(self, client, config=None, *, data_dir=None):
        super().__init__(client, config)
        self._actor_mappings = ActorMappingStore(data_dir) if data_dir is not None else None

    async def _search(self, name: str) -> str | None:
        # 优先按展示名精确查询标准主名映射; 别名完全不参与.
        if self._actor_mappings is not None:
            matches = self._actor_mappings.find_ids_by_name(name)
            if len(matches) == 1:
                return f"{self.base_url}/actress{matches[0]}.html"
            if len(matches) > 1:
                raise SourceError(
                    FailureReason.NOT_FOUND,
                    detail=f"演员映射表中标准姓名「{name}」对应多个 ID：{', '.join(matches)}",
                )

        # 映射没有命中时才使用 Minnano 网站检索; 必须唯一命中才允许刮削.
        search_url = self._search_url(name)
        text = await self.client.get_html(search_url, cookies=self.cookies)
        html = Selector(text=text)

        # 站点精确检索常 302 到详情页; 详情页本身代表唯一结果.
        if html.css("div.act-profile").get():
            detail = extract_text(html, '//link[@rel="canonical"]/@href') or extract_text(
                html, '//meta[@property="og:url"]/@content'
            )
            if detail:
                return detail
            img = extract_text(html, '//div[contains(@class,"thumb")]//img[contains(@src,"p_actress")]/@src')
            if img:
                match = re.search(r"/(\d+)\.jpg", img)
                if match:
                    return f"{self.base_url}/actress{match.group(1)}.html"
            raise SourceError(FailureReason.NOT_FOUND, detail=f"Minnano 检索「{name}」返回详情页，但无法确认演员 ID")

        hits = self._pick_search_hits(html, name)
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise SourceError(
                FailureReason.NOT_FOUND,
                detail=f"Minnano 网站检索「{name}」命中 {len(hits)} 个同名演员，结果不唯一",
            )
        raise SourceError(
            FailureReason.NOT_FOUND, detail=f"映射表无标准姓名「{name}」；Minnano 网站检索也没有唯一匹配结果"
        )

    def _search_url(self, name: str) -> str:
        return f"{self.base_url}/search_result.php?search_scope=actress&search_word={quote(name)}&search=Go"

    async def check_connectivity(self) -> ConnectivityOutcome:
        """探测检索入口: 首页可达不代表 ``search_result.php`` 可达, 后者处于挑战保护."""
        return await self.client.check(self._search_url("あ"), cookies=self.cookies)

    def _pick_search_hits(self, html: Selector, name: str) -> list[str]:
        """只收集主名与展示名精确相等的演员; 多个同名结果必须拒绝刮削."""
        if not name.strip():
            return []
        rows = html.xpath('//table[contains(@class,"tbllist") and contains(@class,"actress")]//tr[td]')
        hits: list[str] = []
        for row in rows:
            title = extract_text(row, './/h2[contains(@class,"ttl")]/a/text()')
            href = extract_text(row, './/h2[contains(@class,"ttl")]/a/@href')
            if not title or not href or not _ACTRESS_HREF_RE.search(href):
                continue
            if is_same_name(_strip_annotations(title), name):
                url = urljoin(self.base_url + "/", href.split("?", 1)[0])
                if url not in hits:
                    hits.append(url)
        return hits

    async def _scrape(self, url: str) -> ActorMetadata | None:
        text = await self.client.get_html(url, cookies=self.cookies)
        return parse_minnano_detail(text, page_url=url, base_url=self.base_url)


def parse_minnano_detail(html_text: str, *, page_url: str, base_url: str) -> ActorMetadata | None:
    html = Selector(text=html_text)
    profiles = html.xpath('//div[contains(@class,"act-profile")]')
    if not profiles:
        return None
    profile = profiles[0]

    tags = _tag_names(profile)
    raw_name = extract_text(profile, ".//h2/text()") or extract_text(profile, ".//h2//text()")
    if not raw_name:
        return None
    name, aliases = _parse_name_line(raw_name, tags)
    if not name:
        return None

    # 别名行可重复; 每行一个别名 + 读音, 与名字行同构解析.
    for row in profile.xpath('.//tr[td/span[normalize-space()="別名"]]'):
        raw_alias = extract_text(row, "string(./td/p)") or ""
        alias_name, alias_readings = _parse_name_line(raw_alias.strip(), tags)
        if alias_name and alias_name not in tags:
            aliases.append(alias_name)
        aliases.extend(alias_readings)
    aliases = _dedupe_preserve([a for a in aliases if a and a != name])

    fields = _profile_fields(profile)
    birthday = _parse_birthday(fields.get("生年月日", ""))
    birthplace = _plain(fields.get("出身地", "")) or None
    height, bust, waist, hip, cup = _parse_figure(fields.get("サイズ", ""))

    image = extract_text(html, '//meta[@property="og:image"]/@content') or extract_text(
        html, '//div[contains(@class,"thumb")]//img[contains(@src,"p_actress")]/@src'
    )
    image_urls: list[str] = []
    if image:
        image_urls.append(_stable_url(urljoin(base_url + "/", image)))

    source = (
        extract_text(html, '//link[@rel="canonical"]/@href')
        or extract_text(html, '//meta[@property="og:url"]/@content')
        or page_url
    )

    return ActorMetadata(
        name=name,
        aliases=aliases,
        birthday=birthday,
        birthplace=birthplace,
        height=height,
        bust=bust,
        waist=waist,
        hip=hip,
        cup=cup,
        image_urls=image_urls,
        source_url=source,
        provider_ids={"minnano": source} if source else {},
    )


def _parse_name_line(raw: str, tags: frozenset[str]) -> tuple[str, list[str]]:
    """剥掉末尾括号: 名字取括号前的主体, 只有最右边一组的假名或拉丁片段算读音."""
    text = normalize_name(raw)
    groups: list[str] = []
    while True:
        m = _TRAILING_GROUP_RE.match(text)
        if m is None:
            break
        head = m.group("head").strip()
        if not head:
            # 整串只有一个括号组: 没有名字可言.
            return "", []
        groups.append(m.group("inner"))
        text = head
    if not groups:
        return text, []
    return text, _alias_readings(groups[0], tags)


def _alias_readings(inner: str, tags: frozenset[str]) -> list[str]:
    """括号内容按斜杠切分; 与同页标签同名的片段是站点或系列名, 不是名字."""
    out: list[str] = []
    for token in _READING_SEPARATOR_RE.split(inner):
        token = token.strip()
        if token and token not in out and token not in tags and _READING_RE.match(token):
            out.append(token)
    return out


def _tag_names(profile: Selector) -> frozenset[str]:
    """同页标签行: 演员所属的站点与系列, 也是混进读音里的来源名的比对词表."""
    values = profile.xpath(
        './/tr[td/span[normalize-space()="タグ"]]//div[contains(@class,"tagarea")]//a/text()'
    ).getall()
    return frozenset(v for v in (normalize_name(t) for t in values) if v)


def _strip_annotations(name: str) -> str:
    # 尾部注记括号不写入 aliases.
    while True:
        stripped, _ = split_actor_aliases(name)
        if stripped == name:
            return name
        name = stripped


def _dedupe_preserve(values: list[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        if v not in out:
            out.append(v)
    return out


def _profile_fields(profile: Selector) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in profile.xpath(".//tr"):
        key = extract_text(row, "./td/span/text()")
        if not key:
            continue
        val = extract_text(row, "string(./td/p)") or extract_text(row, "string(./td)")
        if val:
            val = val.strip()
            if val.startswith(key):
                val = val[len(key) :].strip()
            out[key] = val
    return out


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _parse_birthday(raw: str) -> str | None:
    return normalize_calendar_date(raw)


def _parse_figure(raw: str) -> tuple[int | None, int | None, int | None, int | None, str | None]:
    text = unicodedata.normalize("NFKC", raw)
    sizes: dict[str, int] = {}
    for match in _FIGURE_RE.finditer(text):
        sizes.setdefault(match.group("key").upper(), int(match.group("value")))
    cup_match = _CUP_RE.search(text)
    cup = cup_match.group(1).upper() if cup_match else None
    return sizes.get("T"), sizes.get("B"), sizes.get("W"), sizes.get("H"), cup


def _stable_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
