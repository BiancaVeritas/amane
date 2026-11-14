"""演员名字行: 名字后面跟一到多个括号组, 最右边一组是读音, 其余是注记.

读音写作「假名 / 罗马字」并作为别名; 注记多为名字来源站点 (如 ``相川美羽(tenshigao)``),
不进别名. 站点名也会被填进读音的位置, 用同页标签行 (演员所属站点与系列) 比对后丢弃.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from parsel import Selector

from amane.enums import ActorGender, SiteName
from amane.net.connectivity import ConnectivityOutcome
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

#: actress id → {name, aliases} 的离线表. 搜索页 ``search_result.php`` 处于 Cloudflare
#: 挑战之后 (403), 而列表页与详情页不设防, 所以整表枚举一次, 运行时查表直达详情页.
#: 表里只有「主名与别名」这一件事; 生日三围等资料一律现抓网页, 不在本地留快照.
_NAMES_PATH = Path(__file__).with_name("minnano_actress_names.json")


@lru_cache(maxsize=1)
def _actress_table() -> dict[str, dict[str, object]]:
    """id → {name, aliases}; 文件缺失或损坏时返回空表, 退回在线检索."""
    try:
        raw = json.loads(_NAMES_PATH.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return {}
    table: dict[str, dict[str, object]] = {}
    for actress_id, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        aliases = [str(a).strip() for a in (entry.get("aliases") or []) if str(a).strip()]
        if name or aliases:
            table[str(actress_id)] = {"name": name, "aliases": aliases}
    return table


def _fold(value: str) -> str:
    """比较用的规范形: 去空白、统一全角半角、忽略大小写."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


def lookup_actress_id(name: str) -> str | None:
    """按单个名字查 id; 命中多个 id 时返回 None (交给候选比较).

    只做「这个名字属于哪个 id」的原始判断, 优先主名还是别名由调用方按候选决定.
    """
    found = lookup_actress_ids(name)
    return next(iter(found)) if len(found) == 1 else None


def lookup_actress_ids(name: str) -> set[str]:
    """这个名字可能对应的所有 id, 不分主名与别名."""
    table = _actress_table()
    if not table:
        return set()
    key = name.strip()
    if not key:
        return set()
    folded = _fold(key)
    hits: set[str] = set()
    for actress_id, entry in table.items():
        if str(entry["name"]).strip() == key or any(
            str(a).strip() == key for a in entry["aliases"]  # type: ignore[union-attr]
        ):
            hits.add(actress_id)
            continue
        names = [str(entry["name"]), *(str(a) for a in entry["aliases"])]  # type: ignore[union-attr]
        if any(_fold(candidate) == folded for candidate in names if candidate.strip()):
            hits.add(actress_id)
    return hits


def match_actress(
    names: list[str],
) -> tuple[str | None, list[str]]:
    """按「主名优先, 再看别名」把候选缩到最少, 返回 (唯一 id 或 None, 待比较的 id).

    规则:
    1. 先只拿主名查; 命中唯一 id 就用它 (别名不参与, 以免主名精确命中却输给别人的别名).
    2. 主名撞了多个人时, 只有「双向互通」的那些候选才算同一个人 ——
       A 的主名落在 B 的名称集里, 且 B 的主名也落在 A 的名称集里.
       单向命中 (A 的主名只是 B 的别名) 说明是两个人共用了一个名字, 直接排除.
    3. 主名没有命中才退到别名.
    4. 仍有多个候选时交给调用方现抓网页比较资料完整度 —— 这里只负责把候选列出来.
    """
    if not names:
        return None, []
    primary, *rest = (n.strip() for n in names)
    aliases = [n for n in rest if n]

    # 1. 主名
    if primary:
        hits = _ids_by_field(primary, "name")
        if len(hits) == 1:
            return next(iter(hits)), []
        if hits:
            # 主名撞了多个人: 先用双向互通收窄, 再用别名收窄.
            confirmed = _mutual_links(hits)
            if len(confirmed) == 1:
                return next(iter(confirmed)), []
            if confirmed:
                narrowed = _narrow_by_aliases(confirmed, aliases)
                if len(narrowed) == 1:
                    return next(iter(narrowed)), []
                return None, sorted(narrowed)
            # 没有任何一对是双向互通的: 说明这些是同名不同人, 不是同一人的多种写法.
            narrowed = _narrow_by_aliases(hits, aliases)
            if len(narrowed) == 1:
                return next(iter(narrowed)), []
            return None, sorted(narrowed)

    # 2. 别名
    alias_hits: set[str] = set()
    for alias in aliases:
        alias_hits |= _ids_by_field(alias, "alias")
    if len(alias_hits) == 1:
        return next(iter(alias_hits)), []
    if alias_hits:
        return None, sorted(alias_hits)
    return None, []


def _mutual_links(candidates: set[str]) -> set[str]:
    """候选里彼此「对得上」的那些 id.

    两条记录指向同一个人时, 站点通常给出同一组名字. 判据取两种, 满足任一即可:
    1. 名字集互为印证: A 的主名在 B 的别名里且 B 的主名在 A 的别名里;
    2. 两份记录的名字完全一致 (主名相同, 别名集也相同) —— 站点为同一个人存了两条时
       就是这样, 此时没有交叉引用可看.
    其余情形一律不算: 只同名而别名不同, 或只有一方把对方的名称收进别名, 都说明是两个
    不同的人共用了名字.
    """
    table = _actress_table()
    alias_of = {
        aid: {str(a).strip() for a in table[aid]["aliases"] if str(a).strip()}  # type: ignore[union-attr]
        for aid in candidates
        if aid in table
    }
    primary_of = {
        aid: str(table[aid]["name"]).strip() for aid in candidates if aid in table  # type: ignore[union-attr]
    }
    if len(alias_of) < 2:
        return set()

    linked: set[str] = set()
    for aid, own_aliases in alias_of.items():
        primary = primary_of.get(aid, "")
        if not primary:
            continue
        for other_id, other_aliases in alias_of.items():
            if other_id == aid:
                continue
            other_primary = primary_of.get(other_id, "")
            if not other_primary:
                continue
            crossed = primary in other_aliases and other_primary in own_aliases
            # 两份记录名字完全一致 —— 但别名为空时不能算: 空集恒相等, 那样所有只留主名的
            # 同名者都会被并成一个.
            same_record = (
                bool(own_aliases) and primary == other_primary and own_aliases == other_aliases
            )
            if crossed or same_record:
                linked.add(aid)
                linked.add(other_id)
    return linked


def _ids_by_field(value: str, field: str) -> set[str]:
    """按主名或别名精确查 (含宽松匹配), 只在该字段内匹配."""
    table = _actress_table()
    key = value.strip()
    if not table or not key:
        return set()
    folded = _fold(key)
    hits: set[str] = set()
    for actress_id, entry in table.items():
        candidates = (
            [str(entry["name"])] if field == "name" else [str(a) for a in entry["aliases"]]  # type: ignore[union-attr]
        )
        if any(c.strip() == key or (c.strip() and _fold(c) == folded) for c in candidates):
            hits.add(actress_id)
    return hits


def _narrow_by_aliases(candidates: set[str], aliases: list[str]) -> set[str]:
    """在给定候选里, 找出与这些别名有关的那些; 无一相关时原样返回."""
    if not aliases:
        return candidates
    table = _actress_table()
    folded_aliases = {_fold(a) for a in aliases}
    matched: set[str] = set()
    for actress_id in candidates:
        entry = table.get(actress_id)
        if entry is None:
            continue
        names = [str(entry["name"]), *(str(a) for a in entry["aliases"])]  # type: ignore[union-attr]
        if any(_fold(n) in folded_aliases for n in names if n.strip()):
            matched.add(actress_id)
    return matched or candidates


def _profile_completeness(meta: ActorMetadata) -> int:
    """资料完整度: 实际填了几项.

    只在候选之间做相对比较, 因此不需要权重 —— 每一项都算一分. 「更全」就是填得更多,
    不是某个字段更靠前.
    """
    filled = 0
    for value in (
        meta.name,
        meta.birthday,
        meta.birthplace,
        meta.height,
        meta.bust,
        meta.waist,
        meta.hip,
        meta.cup,
    ):
        if value not in (None, "", 0):
            filled += 1
    # 别名本身就是资料的一部分: 收录得多说明这一条更完整.
    filled += len(meta.aliases or [])
    return filled


class MinnanoActorCrawler(ActorCrawler):
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

    async def fetch_names(self, names: Sequence[str]) -> ActorMetadata | None:
        """先按主名定位, 主名不中再退到别名; 多候选时抓网页比资料完整度.

        站点上同名不同人很常见 (尤其是只留假名的条目), 只按「第一个命中」选会把两个
        人的资料混成一个. 因此候选不唯一时逐个抓详情页, 取字段更全的那个 ——
        「更全」按出生地、生年月日、三围、出身地等实际填了几项来数, 不按字段顺序.
        """
        unique_id, candidates = match_actress(list(names))
        if unique_id is not None:
            return await self._scrape(f"{self.base_url}/actress{unique_id}.html")
        if not candidates:
            # 表里没有才走在线检索 (可能仍被挑战拦下, 那就返回 None).
            return await super().fetch_names(names)
        if len(candidates) == 1:
            return await self._scrape(f"{self.base_url}/actress{candidates[0]}.html")

        best: ActorMetadata | None = None
        best_score = -1
        for actress_id in candidates:
            try:
                meta = await self._scrape(f"{self.base_url}/actress{actress_id}.html")
            except Exception as exc:
                self.logger.warning("minnano 候选抓取失败", actress_id=actress_id, error=str(exc))
                continue
            if meta is None:
                continue
            score = _profile_completeness(meta)
            if score > best_score:
                best, best_score = meta, score
        return best

    async def _search(self, name: str) -> str | None:
        # 命中离线表就直接定位详情页, 完全不碰被 Cloudflare 拦的搜索页.
        # 实际抓取由基类按返回的 URL 进行.
        if local := lookup_actress_id(name):
            return f"{self.base_url}/actress{local}.html"

        # 表里没有才走在线检索 (可能仍被挑战拦下, 那就返回 None).
        search_url = self._search_url(name)
        text = await self.client.get_html(search_url, cookies=self.cookies)
        html = Selector(text=text)

        # 精确命中时常 302 到详情页; 跟随重定向后 HTML 已是详情.
        if html.css("div.act-profile").get():
            detail = extract_text(html, '//link[@rel="canonical"]/@href') or extract_text(
                html, '//meta[@property="og:url"]/@content'
            )
            if detail:
                return detail
            # 未命中则从头像路径回退.
            img = extract_text(html, '//div[contains(@class,"thumb")]//img[contains(@src,"p_actress")]/@src')
            if img:
                m = re.search(r"/(\d+)\.jpg", img)
                if m:
                    return f"{self.base_url}/actress{m.group(1)}.html"
            return None

        return self._pick_search_hit(html, name)

    def _search_url(self, name: str) -> str:
        return f"{self.base_url}/search_result.php?search_scope=actress&search_word={quote(name)}&search=Go"

    async def check_connectivity(self) -> ConnectivityOutcome:
        """探测检索入口: 首页可达不代表 ``search_result.php`` 可达, 后者处于挑战保护."""
        return await self.client.check(self._search_url("あ"), cookies=self.cookies)

    def _pick_search_hit(self, html: Selector, name: str) -> str | None:
        """行标题与查找名 (去掉注记括号后) 相等才算命中; 确认不了返回 None."""
        if not name.strip():
            return None
        rows = html.xpath('//table[contains(@class,"tbllist") and contains(@class,"actress")]//tr[td]')
        for row in rows:
            title = extract_text(row, './/h2[contains(@class,"ttl")]/a/text()')
            href = extract_text(row, './/h2[contains(@class,"ttl")]/a/@href')
            if not title or not href or not _ACTRESS_HREF_RE.search(href):
                continue
            if is_same_name(_strip_annotations(title), name):
                return urljoin(self.base_url + "/", href.split("?", 1)[0])
        return None

    async def _scrape(self, url: str) -> ActorMetadata | None:
        """资料一律现抓网页, 本地只留「名字 → id」那张检索表.

        快照会过期, 也会漏收字段 (例如「出身地」当初就没收进去); 以快照为准等于把网站
        后来更正的值一起丢掉. 索引只负责把名字换成 id, 换取详情页地址.
        """
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
    # 名字只取括号前的主体; 括号里的假名 / 罗马音是读音, 不当名字也不当别名.
    name, _readings = _parse_name_line(raw_name, tags)
    if not name:
        return None
    aliases: list[str] = []

    # 别名行可重复; 每行一个别名 + 读音, 与名字行同构解析.
    for row in profile.xpath('.//tr[td/span[normalize-space()="別名"]]'):
        raw_alias = extract_text(row, "string(./td/p)") or ""
        # 别名的括号里同样是读音, 只留别名主体.
        alias_name, _alias_readings = _parse_name_line(raw_alias.strip(), tags)
        if alias_name and alias_name not in tags:
            aliases.append(alias_name)
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
