"""中文别名是该站相对其它资料来源的独特字段."""

from __future__ import annotations

import re
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from parsel import Selector

from amane.enums import ActorGender, SiteName
from amane.plugins.models import SourceCapability

from ...base import CrawlerProfile
from ...parsing import extract_text, is_same_name, normalize_name
from ...sites.javdb import reject_login_page
from ..base import ActorCrawler
from ..models import ActorMetadata

_ACTOR_PATH_RE = re.compile(r"^/actors/([A-Za-z0-9]+)/?$")
_ACTOR_INDEX_SLUGS = frozenset({"censored", "uncensored", "western"})
_BG_URL_RE = re.compile(r"url\(\s*['\"]?(https?://[^)'\"]+)['\"]?\s*\)", re.IGNORECASE)
_PLACEHOLDER_AVATAR_RE = re.compile(r"actor_unknow", re.IGNORECASE)
# 影片数标注随语言变化; 可能写入 aliases.
_FILM_COUNT_RE = re.compile(r"\d+\s*(?:部影片|movie\(s\))", re.IGNORECASE)
# 无码条目的名字带站点标记后缀 ((無碼) / (Uncensored)); 全角括号已由 normalize_name 的 NFKC 折叠归一.
_UNCENSORED_MARKER_RE = re.compile(r"\s*\((?:無碼|无码|uncensored)\)\s*$", re.IGNORECASE)
_GENDER_TOKENS: dict[str, ActorGender] = {
    "男優": ActorGender.MALE,
    "男优": ActorGender.MALE,
    # 无 locale cookie 时按 Accept-Language 返回英文页, 性别标注为英文.
    "Male": ActorGender.MALE,
    "女優": ActorGender.FEMALE,
    "女优": ActorGender.FEMALE,
    "Female": ActorGender.FEMALE,
}


class JavDBActorCrawler(ActorCrawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(
            name=SiteName.JAVDB,
            base_url="https://javdb.com",
            capabilities=frozenset({SourceCapability.ACTOR_PROFILE}),
            genders=frozenset({ActorGender.FEMALE, ActorGender.MALE}),
        )

    async def _search(self, name: str) -> str | None:
        q = quote(name)
        search_url = f"{self.base_url}/search?q={q}&f=actor&locale=zh"
        text = await self.client.get_html(search_url, cookies=self.cookies)
        html = Selector(text=text)
        reject_login_page(html, url=search_url)
        # 精确命中偶发落到详情页.
        if html.css("span.actor-section-name").get():
            detail = extract_text(html, '//link[@rel="canonical"]/@href')
            return detail or None
        return pick_javdb_actor_search_hit(html, name, base_url=self.base_url)

    async def _scrape(self, url: str) -> ActorMetadata | None:
        text = await self.client.get_html(url, cookies=self.cookies)
        reject_login_page(Selector(text=text), url=url)
        return parse_javdb_actor_detail(text, page_url=url, base_url=self.base_url)


def pick_javdb_actor_search_hit(html: Selector, name: str, *, base_url: str) -> str | None:
    """精确匹配 canonical 名或 title 别名 (大小写不敏感). 不允许回退首条, 避免子串误伤."""
    for box in html.xpath('//div[contains(@class,"actor-box")]//a[@href]'):
        href = extract_text(box, "@href")
        actor_id = _actor_id_from_href(href)
        if not actor_id:
            continue
        canonical = normalize_name(extract_text(box, ".//strong/text()") or extract_text(box, "string(.//strong)"))
        aliases = _split_names(extract_text(box, "@title"))
        if any(is_same_name(n, name) for n in [canonical, *aliases]):
            return urljoin(base_url + "/", href)
    return None


def parse_javdb_actor_detail(html_text: str, *, page_url: str, base_url: str) -> ActorMetadata | None:
    html = Selector(text=html_text)
    # 不要先用 XPath string() 扁平化整个节点: JavDB 页面可能把多个演员链接
    # 放在同一个 actor-section-name 容器中, string() 会将它们粘成一个名字.
    names = _actor_section_names(html, page_url=page_url)
    if not names:
        return None
    name, extra = names[0], names[1:]

    aliases: list[str] = list(extra)
    gender: ActorGender | None = None
    for meta in html.xpath("//span[contains(@class,'section-meta')]"):
        raw = extract_text(meta, "string(.)")
        for token in _split_names(raw):
            if token in _GENDER_TOKENS:
                gender = _GENDER_TOKENS[token]
                continue
            if _FILM_COUNT_RE.search(token):
                continue
            aliases.append(token)
    if gender is None:
        # 详情页只为男优标注性别; 女优无标记, 未标注默认女.
        gender = ActorGender.FEMALE
    aliases = _dedupe_preserve([a for a in aliases if a != name])

    image_urls: list[str] = []
    style = extract_text(html, '//div[contains(@class,"actor-avatar")]//span[contains(@class,"avatar")]/@style')
    avatar = _background_image_url(style)
    if avatar and not _PLACEHOLDER_AVATAR_RE.search(avatar):
        image_urls.append(_rewrite_to_base_origin(urljoin(base_url + "/", avatar), base_url))

    source = extract_text(html, '//link[@rel="canonical"]/@href') or page_url
    source = _rewrite_to_base_origin(urljoin(base_url + "/", source), base_url)
    actor_id = _actor_id_from_href(urlsplit(source).path) or _actor_id_from_href(urlsplit(page_url).path)

    return ActorMetadata(
        name=name,
        aliases=aliases,
        gender=gender,
        image_urls=image_urls,
        source_url=source,
        provider_ids={"javdb": actor_id} if actor_id else {},
    )


def _actor_section_names(html: Selector, *, page_url: str) -> list[str]:
    """提取演员名称; 容器含多个演员档案链接时, 每个链接必须独立成名.

    只在链接确实指向不同 /actors/<id> 档案时采用逐链接提取, 避免把同一人的
    普通别名链接误拆成多个演员. 无法确认链接结构时回退到文本分隔符解析.
    """
    containers = html.xpath("//span[contains(@class,'actor-section-name')]")
    if not containers:
        return []

    linked: list[tuple[str, str]] = []
    for node in containers[0].xpath(".//a[@href]"):
        name = (node.xpath("string(.)").get() or "").strip()
        href = (node.xpath("@href").get() or "").strip()
        actor_id = _actor_id_from_href(href)
        if name and actor_id:
            linked.append((actor_id, name))

    unique_ids = {actor_id for actor_id, _ in linked}
    if len(unique_ids) > 1:
        canonical_url = extract_text(html, '//link[@rel="canonical"]/@href') or page_url
        current_id = _actor_id_from_href(urlsplit(canonical_url).path)
        # 容器意外包含多个演员档案链接时, 只取当前详情页对应的演员;
        # 其它链接是其它演员, 绝不能降级为当前演员的 aliases.
        selected = next((name for actor_id, name in linked if actor_id == current_id), None)
        if selected is None:
            selected = linked[0][1]
        normalized = normalize_name(selected)
        return [normalized] if normalized else []

    raw = (containers[0].xpath("string(.)").get() or "").strip()
    return _split_names(raw)


def _split_names(raw: str) -> list[str]:
    if not raw:
        return []
    names: list[str] = []
    for part in re.split(r"[,，、]", raw):
        name = _UNCENSORED_MARKER_RE.sub("", normalize_name(part))
        if name:
            names.append(name)
    return names


def _dedupe_preserve(values: list[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        if v not in out:
            out.append(v)
    return out


def _actor_id_from_href(href: str) -> str | None:
    if not href:
        return None
    path = urlsplit(href).path if "://" in href else href
    m = _ACTOR_PATH_RE.match(path)
    if not m:
        return None
    slug = m.group(1)
    if slug.lower() in _ACTOR_INDEX_SLUGS:
        return None
    return slug


def _rewrite_to_base_origin(url: str, base_url: str) -> str:
    """JAVDB 镜像返回的 canonical / 头像可能仍指向官方主站; 统一改回配置的镜像源."""
    target = urlsplit(url)
    base = urlsplit(base_url)
    if not target.scheme or not target.netloc or not base.scheme or not base.netloc:
        return url
    return urlunsplit((base.scheme, base.netloc, target.path, target.query, target.fragment))


def _background_image_url(style: str) -> str | None:
    if not style:
        return None
    m = _BG_URL_RE.search(style)
    return m.group(1) if m else None
