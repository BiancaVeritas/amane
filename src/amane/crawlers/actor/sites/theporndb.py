"""ThePornDB 演员资料爬虫.

GraphQL 负责搜索、精确匹配与 deleted 条目的合并跟随；
REST 负责最终演员资料。REST 失败或数据无效时不回退 GraphQL 资料。

演员出生地和简介使用独立的简体中文翻译入口，不受影片翻译字段配置影响。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from amane.enums import ActorGender, MetadataField, SiteName
from amane.net.errors import RequestError
from amane.plugins.models import SourceCapability

from ...base import CrawlerProfile
from ...parsing import is_same_name, normalize_name
from ..base import ActorCrawler
from ..models import ActorMetadata

if TYPE_CHECKING:
    from amane.llm.protocol import Translator


_PERFORMER_FIELDS = """
    id name aliases gender birth_date country height disambiguation
    deleted merged_into_id
    measurements { cup_size band_size waist hip }
    images { url width height }
    urls { url type }
"""

_SEARCH_QUERY = f"""
query SearchPerformer($term: String!) {{
  searchPerformer(term: $term) {{
    {_PERFORMER_FIELDS}
  }}
}}"""

_FIND_QUERY = f"""
query FindPerformer($id: ID!) {{
  findPerformer(id: $id) {{
    {_PERFORMER_FIELDS}
  }}
}}"""

_GENDER: dict[str, ActorGender] = {
    "FEMALE": ActorGender.FEMALE,
    "MALE": ActorGender.MALE,
    "TRANSGENDER FEMALE": ActorGender.TRANSGENDER_FEMALE,
}

# ThePornDB REST links 的键名 → Amane provider_ids 键名.
# 仅保留明确识别的类型，不猜测未知网站的类型。
_URL_TYPE_KEYS: dict[str, str] = {
    "iafd": "iafd",
    "freeones": "freeones",
    "indexxx": "indexxx",
    "twitter": "twitter",
    "instagram": "instagram",
    "imdb": "imdb",
    "wikidata": "wikidata",
    "wikipedia": "wikipedia",
    "fanza": "fanza",
    "manyvids": "manyvids",
    "onlyfans": "onlyfans",
    "official homepage": "homepage",
    "homepage": "homepage",
    "official": "homepage",
}

_MERGE_HOPS = 4

_IMPORT_CODE_RE = re.compile(r"^([A-Za-z][A-Za-z'\- ]*?)\s+[A-Za-z]{3}\d{2,4}$")
_ID_SUFFIX_RE = re.compile(r"^(.+?)\s+(?:ID\s*\d{3,6}|\d{4,6})$", re.IGNORECASE)

_HEIGHT_RE = re.compile(r"^\s*(\d{2,3})\s*(?:cm)?\s*$", re.IGNORECASE)
_MEASUREMENTS_RE = re.compile(r"^\s*(\d{2,3})\s*([A-Za-z]+)\s*[-–—]\s*(\d{2,3})\s*[-–—]\s*(\d{2,3})\s*$")

# ISO 3166-1 alpha-2 国家/地区代码及其简体中文名称。
# 该表仅用于精确映射国家/地区，不进行模糊匹配。
_COUNTRY_CODES: dict[str, str] = {
    "AD": "安道尔",
    "AE": "阿联酋",
    "AF": "阿富汗",
    "AG": "安提瓜和巴布达",
    "AI": "安圭拉",
    "AL": "阿尔巴尼亚",
    "AM": "亚美尼亚",
    "AO": "安哥拉",
    "AQ": "南极洲",
    "AR": "阿根廷",
    "AS": "美属萨摩亚",
    "AT": "奥地利",
    "AU": "澳大利亚",
    "AW": "阿鲁巴",
    "AX": "奥兰群岛",
    "AZ": "阿塞拜疆",
    "BA": "波斯尼亚和黑塞哥维那",
    "BB": "巴巴多斯",
    "BD": "孟加拉国",
    "BE": "比利时",
    "BF": "布基纳法索",
    "BG": "保加利亚",
    "BH": "巴林",
    "BI": "布隆迪",
    "BJ": "贝宁",
    "BL": "圣巴泰勒米",
    "BM": "百慕大",
    "BN": "文莱",
    "BO": "玻利维亚",
    "BQ": "荷兰加勒比区",
    "BR": "巴西",
    "BS": "巴哈马",
    "BT": "不丹",
    "BV": "布韦岛",
    "BW": "博茨瓦纳",
    "BY": "白俄罗斯",
    "BZ": "伯利兹",
    "CA": "加拿大",
    "CC": "科科斯群岛",
    "CD": "刚果民主共和国",
    "CF": "中非共和国",
    "CG": "刚果共和国",
    "CH": "瑞士",
    "CI": "科特迪瓦",
    "CK": "库克群岛",
    "CL": "智利",
    "CM": "喀麦隆",
    "CN": "中国",
    "CO": "哥伦比亚",
    "CR": "哥斯达黎加",
    "CU": "古巴",
    "CV": "佛得角",
    "CW": "库拉索",
    "CX": "圣诞岛",
    "CY": "塞浦路斯",
    "CZ": "捷克",
    "DE": "德国",
    "DJ": "吉布提",
    "DK": "丹麦",
    "DM": "多米尼克",
    "DO": "多米尼加共和国",
    "DZ": "阿尔及利亚",
    "EC": "厄瓜多尔",
    "EE": "爱沙尼亚",
    "EG": "埃及",
    "EH": "西撒哈拉",
    "ER": "厄立特里亚",
    "ES": "西班牙",
    "ET": "埃塞俄比亚",
    "FI": "芬兰",
    "FJ": "斐济",
    "FK": "福克兰群岛",
    "FM": "密克罗尼西亚联邦",
    "FO": "法罗群岛",
    "FR": "法国",
    "GA": "加蓬",
    "GB": "英国",
    "GD": "格林纳达",
    "GE": "格鲁吉亚",
    "GF": "法属圭亚那",
    "GG": "根西岛",
    "GH": "加纳",
    "GI": "直布罗陀",
    "GL": "格陵兰",
    "GM": "冈比亚",
    "GN": "几内亚",
    "GP": "瓜德罗普",
    "GQ": "赤道几内亚",
    "GR": "希腊",
    "GS": "南乔治亚和南桑威奇群岛",
    "GT": "危地马拉",
    "GU": "关岛",
    "GW": "几内亚比绍",
    "GY": "圭亚那",
    "HK": "香港",
    "HM": "赫德岛和麦克唐纳群岛",
    "HN": "洪都拉斯",
    "HR": "克罗地亚",
    "HT": "海地",
    "HU": "匈牙利",
    "ID": "印度尼西亚",
    "IE": "爱尔兰",
    "IL": "以色列",
    "IM": "马恩岛",
    "IN": "印度",
    "IO": "英属印度洋领地",
    "IQ": "伊拉克",
    "IR": "伊朗",
    "IS": "冰岛",
    "IT": "意大利",
    "JE": "泽西岛",
    "JM": "牙买加",
    "JO": "约旦",
    "JP": "日本",
    "KE": "肯尼亚",
    "KG": "吉尔吉斯斯坦",
    "KH": "柬埔寨",
    "KI": "基里巴斯",
    "KM": "科摩罗",
    "KN": "圣基茨和尼维斯",
    "KP": "朝鲜",
    "KR": "韩国",
    "KW": "科威特",
    "KY": "开曼群岛",
    "KZ": "哈萨克斯坦",
    "LA": "老挝",
    "LB": "黎巴嫩",
    "LC": "圣卢西亚",
    "LI": "列支敦士登",
    "LK": "斯里兰卡",
    "LR": "利比里亚",
    "LS": "莱索托",
    "LT": "立陶宛",
    "LU": "卢森堡",
    "LV": "拉脱维亚",
    "LY": "利比亚",
    "MA": "摩洛哥",
    "MC": "摩纳哥",
    "MD": "摩尔多瓦",
    "ME": "黑山",
    "MF": "法属圣马丁",
    "MG": "马达加斯加",
    "MH": "马绍尔群岛",
    "MK": "北马其顿",
    "ML": "马里",
    "MM": "缅甸",
    "MN": "蒙古",
    "MO": "澳门",
    "MP": "北马里亚纳群岛",
    "MQ": "马提尼克",
    "MR": "毛里塔尼亚",
    "MS": "蒙特塞拉特",
    "MT": "马耳他",
    "MU": "毛里求斯",
    "MV": "马尔代夫",
    "MW": "马拉维",
    "MX": "墨西哥",
    "MY": "马来西亚",
    "MZ": "莫桑比克",
    "NA": "纳米比亚",
    "NC": "新喀里多尼亚",
    "NE": "尼日尔",
    "NF": "诺福克岛",
    "NG": "尼日利亚",
    "NI": "尼加拉瓜",
    "NL": "荷兰",
    "NO": "挪威",
    "NP": "尼泊尔",
    "NR": "瑙鲁",
    "NU": "纽埃",
    "NZ": "新西兰",
    "OM": "阿曼",
    "PA": "巴拿马",
    "PE": "秘鲁",
    "PF": "法属波利尼西亚",
    "PG": "巴布亚新几内亚",
    "PH": "菲律宾",
    "PK": "巴基斯坦",
    "PL": "波兰",
    "PM": "圣皮埃尔和密克隆",
    "PN": "皮特凯恩群岛",
    "PR": "波多黎各",
    "PS": "巴勒斯坦",
    "PT": "葡萄牙",
    "PW": "帕劳",
    "PY": "巴拉圭",
    "QA": "卡塔尔",
    "RE": "留尼汪",
    "RO": "罗马尼亚",
    "RS": "塞尔维亚",
    "RU": "俄罗斯",
    "RW": "卢旺达",
    "SA": "沙特阿拉伯",
    "SB": "所罗门群岛",
    "SC": "塞舌尔",
    "SD": "苏丹",
    "SE": "瑞典",
    "SG": "新加坡",
    "SH": "圣赫勒拿",
    "SI": "斯洛文尼亚",
    "SJ": "斯瓦尔巴和扬马延",
    "SK": "斯洛伐克",
    "SL": "塞拉利昂",
    "SM": "圣马力诺",
    "SN": "塞内加尔",
    "SO": "索马里",
    "SR": "苏里南",
    "SS": "南苏丹",
    "ST": "圣多美和普林西比",
    "SV": "萨尔瓦多",
    "SX": "荷属圣马丁",
    "SY": "叙利亚",
    "SZ": "斯威士兰",
    "TC": "特克斯和凯科斯群岛",
    "TD": "乍得",
    "TF": "法属南部领地",
    "TG": "多哥",
    "TH": "泰国",
    "TJ": "塔吉克斯坦",
    "TK": "托克劳",
    "TL": "东帝汶",
    "TM": "土库曼斯坦",
    "TN": "突尼斯",
    "TO": "汤加",
    "TR": "土耳其",
    "TT": "特立尼达和多巴哥",
    "TV": "图瓦卢",
    "TW": "台湾",
    "TZ": "坦桑尼亚",
    "UA": "乌克兰",
    "UG": "乌干达",
    "UM": "美国本土外小岛屿",
    "US": "美国",
    "UY": "乌拉圭",
    "UZ": "乌兹别克斯坦",
    "VA": "梵蒂冈",
    "VC": "圣文森特和格林纳丁斯",
    "VE": "委内瑞拉",
    "VG": "英属维尔京群岛",
    "VI": "美属维尔京群岛",
    "VN": "越南",
    "VU": "瓦努阿图",
    "WF": "瓦利斯和富图纳",
    "WS": "萨摩亚",
    "XK": "科索沃",
    "YE": "也门",
    "YT": "马约特",
    "ZA": "南非",
    "ZM": "赞比亚",
    "ZW": "津巴布韦",
}

# 英文国家/地区名称的精确映射。
_COUNTRY_NAMES: dict[str, str] = {
    "andorra": "安道尔",
    "argentina": "阿根廷",
    "australia": "澳大利亚",
    "austria": "奥地利",
    "belgium": "比利时",
    "brazil": "巴西",
    "canada": "加拿大",
    "china": "中国",
    "colombia": "哥伦比亚",
    "croatia": "克罗地亚",
    "cuba": "古巴",
    "czech republic": "捷克",
    "czechia": "捷克",
    "denmark": "丹麦",
    "dominican republic": "多米尼加共和国",
    "finland": "芬兰",
    "france": "法国",
    "germany": "德国",
    "greece": "希腊",
    "hong kong": "香港",
    "hungary": "匈牙利",
    "iceland": "冰岛",
    "india": "印度",
    "indonesia": "印度尼西亚",
    "ireland": "爱尔兰",
    "israel": "以色列",
    "italy": "意大利",
    "japan": "日本",
    "mexico": "墨西哥",
    "netherlands": "荷兰",
    "new zealand": "新西兰",
    "norway": "挪威",
    "philippines": "菲律宾",
    "poland": "波兰",
    "portugal": "葡萄牙",
    "romania": "罗马尼亚",
    "russia": "俄罗斯",
    "serbia": "塞尔维亚",
    "singapore": "新加坡",
    "slovakia": "斯洛伐克",
    "slovenia": "斯洛文尼亚",
    "south africa": "南非",
    "south korea": "韩国",
    "spain": "西班牙",
    "sweden": "瑞典",
    "switzerland": "瑞士",
    "taiwan": "台湾",
    "thailand": "泰国",
    "turkey": "土耳其",
    "uk": "英国",
    "united kingdom": "英国",
    "usa": "美国",
    "united states": "美国",
    "united states of america": "美国",
    "ukraine": "乌克兰",
    "venezuela": "委内瑞拉",
    "vietnam": "越南",
}


class ThePornDBActorCrawler(ActorCrawler):
    """无 token 不发请求. 精确匹配 name/aliases, 不允许回退首条."""

    def __init__(
        self,
        client: Any,
        config: Any = None,
        translator: Translator | None = None,
    ) -> None:
        super().__init__(client, config)
        self._translator = translator

    def set_translator(self, translator: Translator | None) -> None:
        """供工厂在运行时注入或更新共享翻译器."""
        self._translator = translator

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(
            name=SiteName.THEPORNDB,
            base_url="https://theporndb.net/graphql",
            capabilities=frozenset({SourceCapability.ACTOR_PROFILE}),
            genders=frozenset(
                {
                    ActorGender.FEMALE,
                    ActorGender.TRANSGENDER_FEMALE,
                    ActorGender.MALE,
                }
            ),
        )

    async def fetch(self, name: str) -> ActorMetadata | None:
        token = self.config.api_token if self.config else None
        if not token or not name.strip():
            return None

        headers = {"Authorization": f"Bearer {token}"}
        payload = await self._gql(_SEARCH_QUERY, {"term": name}, headers)
        if payload is None:
            return None

        results = payload.get("searchPerformer") or []
        if not isinstance(results, list):
            return None

        hit = pick_performer(results, name)
        if hit is None:
            return None

        # GraphQL 只负责解析最终 performer ID 与跟随合并目标.
        resolved = await self._follow_merge(hit, headers)
        if resolved is None:
            return None

        performer_id = str(resolved.get("id") or "").strip()
        if not performer_id:
            return None

        # 最终演员资料必须来自 REST；REST 失败时不回退 GraphQL 资料.
        try:
            detail = await self.client.get_json(
                f"https://api.theporndb.net/performers/{performer_id}",
                headers=headers,
            )
        except RequestError:
            return None

        if not isinstance(detail, dict):
            return None

        data = detail.get("data")
        if not isinstance(data, dict):
            return None

        extras = data.get("extras")
        if not isinstance(extras, dict):
            return None

        metadata = rest_performer_to_metadata(data, performer_id)
        if metadata is None:
            return None

        # 出生地：国家/地区精确映射优先；复杂地名整体交给专用 LLM.
        raw_birthplace = normalize_name(str(extras.get("birthplace") or ""))
        if raw_birthplace:
            mapped = _map_country_only(raw_birthplace, extras.get("birthplace_code"))
            if mapped is not None:
                metadata.birthplace = mapped
            else:
                metadata.birthplace = await self._translate_actor_value(
                    raw_birthplace,
                    MetadataField.ACTOR_BIRTHPLACE,
                )
        else:
            # birthplace 缺失时，允许用代码映射出国家名称；
            # 代码本身不作为最终显示内容。
            code = str(extras.get("birthplace_code") or "").strip().upper()
            metadata.birthplace = _COUNTRY_CODES.get(code) or None

        # 简介独立翻译；未启用翻译器或翻译失败时保留原文.
        raw_overview = normalize_name(str(data.get("bio") or ""))
        if raw_overview:
            metadata.overview = await self._translate_actor_value(
                raw_overview,
                MetadataField.ACTOR_OVERVIEW,
            )

        return metadata

    async def _translate_actor_value(self, text: str, field: MetadataField) -> str:
        if self._translator is None:
            return text

        try:
            translated = await self._translator.translate_actor(text, field)
        except Exception:
            # 翻译服务异常不能导致演员资料整体刮削失败.
            return text

        return normalize_name(translated) if isinstance(translated, str) and translated.strip() else text

    async def _search(self, name: str) -> str | None:
        raise NotImplementedError("ThePornDBActorCrawler overrides fetch()")

    async def _scrape(self, url: str) -> ActorMetadata | None:
        raise NotImplementedError("ThePornDBActorCrawler overrides fetch()")

    async def _follow_merge(
        self,
        hit: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any] | None:
        """deleted 条目跟随 ``merged_into_id``; 无合并目标则丢弃. 环或过深视为未命中."""
        current: dict[str, Any] | None = hit
        seen: set[str] = set()

        for hop in range(_MERGE_HOPS + 1):
            if current is None:
                return None

            pid = str(current.get("id") or "").strip()
            if pid:
                if pid in seen:
                    return None
                seen.add(pid)

            if current.get("deleted") is not True:
                return current

            if hop == _MERGE_HOPS:
                return None

            merge_id = str(current.get("merged_into_id") or "").strip()
            if not merge_id:
                return None

            payload = await self._gql(_FIND_QUERY, {"id": merge_id}, headers)
            found = payload.get("findPerformer") if payload is not None else None
            current = found if isinstance(found, dict) else None

        return None

    async def _gql(
        self,
        query: str,
        variables: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any] | None:
        data = await self.client.post_json(
            self.base_url,
            json={"query": query, "variables": variables},
            headers=headers,
        )
        if not isinstance(data, dict):
            return None

        payload = data.get("data")
        return payload if isinstance(payload, dict) else None


def pick_performer(results: list[Any], name: str) -> dict[str, Any] | None:
    """精确匹配 canonical 名或 aliases. 不允许回退首条."""
    for item in results:
        if not isinstance(item, dict):
            continue
        if any(is_same_name(n, name) for n in _performer_names(item)):
            return item
    return None


def performer_to_metadata(perf: dict[str, Any]) -> ActorMetadata | None:
    """保留 GraphQL 资料转换函数，供既有调用与测试使用."""
    name = normalize_name(str(perf.get("name") or ""))
    if not name:
        return None

    raw_aliases = perf.get("aliases")
    alias_src = raw_aliases if isinstance(raw_aliases, list) else []
    aliases = _dedupe_preserve([a for a in (normalize_name(str(x)) for x in alias_src if x) if a != name])
    aliases = _drop_garbled_aliases(aliases, name)

    raw_meas = perf.get("measurements")
    meas = raw_meas if isinstance(raw_meas, dict) else {}
    performer_id = str(perf.get("id") or "").strip()
    birthday = perf.get("birth_date")
    tagline = normalize_name(str(perf.get("disambiguation") or "")) or None

    return ActorMetadata(
        name=name,
        aliases=aliases,
        gender=_GENDER.get(str(perf.get("gender") or "").strip().upper().replace("_", " ")),
        birthday=birthday if isinstance(birthday, str) else None,
        birthplace=normalize_name(str(perf.get("country") or "")) or None,
        height=_positive_int(perf.get("height")),
        bust=_positive_int(meas.get("band_size")),
        waist=_positive_int(meas.get("waist")),
        hip=_positive_int(meas.get("hip")),
        cup=normalize_name(str(meas.get("cup_size") or "")) or None,
        tagline=tagline,
        image_urls=_image_urls(perf),
        provider_ids=_provider_ids(perf, performer_id),
        source_url=f"https://theporndb.net/performers/{performer_id}" if performer_id else None,
    )


def rest_performer_to_metadata(
    data: dict[str, Any],
    fallback_performer_id: str,
) -> ActorMetadata | None:
    """将 ThePornDB REST data 映射为 Amane 演员资料."""
    name = normalize_name(str(data.get("name") or ""))
    if not name:
        return None

    extras = data.get("extras")
    if not isinstance(extras, dict):
        return None

    raw_aliases = data.get("aliases")
    alias_src = raw_aliases if isinstance(raw_aliases, list) else []
    aliases = _dedupe_preserve(
        [alias for alias in (normalize_name(str(item)) for item in alias_src if item) if alias and alias != name]
    )
    aliases = _drop_garbled_aliases(aliases, name)

    performer_id = str(data.get("id") or fallback_performer_id).strip()
    tagline = normalize_name(str(data.get("disambiguation") or "")) or None

    bust, cup, waist, hip = _parse_measurements(extras.get("measurements"))

    return ActorMetadata(
        name=name,
        aliases=aliases,
        gender=_GENDER.get(str(extras.get("gender") or "").strip().upper().replace("_", " ")),
        birthday=(extras.get("birthday") if isinstance(extras.get("birthday"), str) else None),
        height=_height_cm(extras.get("height")),
        bust=bust,
        cup=cup,
        waist=waist,
        hip=hip,
        tagline=tagline,
        image_urls=_rest_image_urls(data),
        provider_ids=_rest_provider_ids(extras.get("links"), performer_id),
        source_url=(f"https://theporndb.net/performers/{performer_id}" if performer_id else None),
    )


def _map_country_only(
    birthplace: str,
    birthplace_code: object = None,
) -> str | None:
    """仅精确映射国家/地区名称或代码；复杂地名不使用代码覆盖."""
    value = normalize_name(birthplace)
    if not value:
        return None

    # 只有整个字段就是两位代码时才按代码映射.
    if re.fullmatch(r"[A-Za-z]{2}", value):
        return _COUNTRY_CODES.get(value.upper())

    # 国家名称必须整体精确匹配；不对逗号分隔的城市+国家做截断.
    country = _COUNTRY_NAMES.get(value.casefold())
    if country is not None:
        return country

    # birthplace_code 仅用于辅助确认国家-only 的同名原文，不覆盖复杂地点.
    code = str(birthplace_code or "").strip().upper()
    code_name = _COUNTRY_CODES.get(code)
    if code_name is not None and value.casefold() == code_name.casefold():
        return code_name

    return None


def _height_cm(value: object) -> int | None:
    """从 REST 的身高字符串中提取厘米整数."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if not isinstance(value, str):
        return None

    match = _HEIGHT_RE.fullmatch(value)
    if match is None:
        return None

    height = int(match.group(1))
    return height if height > 0 else None


def _parse_measurements(value: object) -> tuple[int | None, str | None, int | None, int | None]:
    """只解析完整的 bust+cup-waist-hip 字符串；格式异常时不猜测."""
    if not isinstance(value, str):
        return None, None, None, None

    match = _MEASUREMENTS_RE.fullmatch(value)
    if match is None:
        return None, None, None, None

    bust = int(match.group(1))
    cup = normalize_name(match.group(2)).upper()
    waist = int(match.group(3))
    hip = int(match.group(4))

    if min(bust, waist, hip) <= 0:
        return None, None, None, None

    return bust, cup, waist, hip


def _rest_image_urls(data: dict[str, Any]) -> list[str]:
    """REST posters 按 order 升序；没有有效 poster 时才回退 image."""
    raw_posters = data.get("posters")
    posters = [item for item in raw_posters if isinstance(item, dict)] if isinstance(raw_posters, list) else []

    posters.sort(key=_poster_order)

    urls: list[str] = []
    for poster in posters:
        url = normalize_name(str(poster.get("url") or ""))
        if url and url not in urls:
            urls.append(url)

    if urls:
        return urls

    image = normalize_name(str(data.get("image") or ""))
    return [image] if image else []


def _poster_order(poster: dict[str, Any]) -> tuple[int, int]:
    value = poster.get("order")
    if isinstance(value, bool) or not isinstance(value, int):
        return 1, 0
    return 0, value


def _rest_provider_ids(
    raw_links: object,
    performer_id: str,
) -> dict[str, str]:
    """兼容 REST links 字典；未知键跳过，避免猜测映射."""
    out: dict[str, str] = {}

    if performer_id:
        out["theporndb"] = performer_id

    if not isinstance(raw_links, dict):
        return out

    for raw_type, raw_url in raw_links.items():
        key = _URL_TYPE_KEYS.get(normalize_name(str(raw_type)).casefold())
        if key is None or key in out:
            continue

        url = normalize_name(str(raw_url or ""))
        if not url or "theporndb.net" in url.casefold():
            continue

        out[key] = url

    return out


def _image_urls(perf: dict[str, Any]) -> list[str]:
    raw_images = perf.get("images")
    images = [img for img in raw_images if isinstance(img, dict)] if isinstance(raw_images, list) else []
    ranked = sorted(images, key=_image_area, reverse=True)

    urls: list[str] = []
    for img in ranked:
        url = normalize_name(str(img.get("url") or ""))
        if url and url not in urls:
            urls.append(url)

    return urls


def _image_area(img: dict[str, Any]) -> int:
    width = _positive_int(img.get("width")) or 0
    height = _positive_int(img.get("height")) or 0
    return width * height


def _provider_ids(perf: dict[str, Any], performer_id: str) -> dict[str, str]:
    out: dict[str, str] = {}

    if performer_id:
        out["theporndb"] = performer_id

    raw_urls = perf.get("urls")
    if not isinstance(raw_urls, list):
        return out

    for item in raw_urls:
        if not isinstance(item, dict):
            continue

        url = normalize_name(str(item.get("url") or ""))
        if not url or "theporndb.net" in url.casefold():
            continue

        key = _URL_TYPE_KEYS.get(normalize_name(str(item.get("type") or "")).casefold())
        if key is None or key in out:
            continue

        out[key] = url

    return out


def _performer_names(perf: dict[str, Any]) -> list[str]:
    names = [str(perf.get("name") or "")]
    aliases = perf.get("aliases") or []

    if isinstance(aliases, list):
        names.extend(str(alias) for alias in aliases if alias)

    return [name for name in names if normalize_name(name)]


def _dedupe_preserve(values: list[str]) -> list[str]:
    out: list[str] = []

    for value in values:
        if value not in out:
            out.append(value)

    return out


def _drop_garbled_aliases(aliases: list[str], name: str) -> list[str]:
    """丢弃本站数据缺陷产生的杂糅别名，并合并同词序的格式变体."""
    normalized = [relaxed for alias in aliases if (relaxed := _strip_import_code(alias, name)) is not None]

    kept: list[str] = []
    for value in normalized:
        if value == name:
            continue

        tokens = _tokens(value)
        if _is_repeat(tokens, name):
            continue
        if _is_splice(tokens, name, kept):
            continue

        kept.append(value)

    best_by_order: dict[tuple[str, ...], tuple[int, tuple[int, int, int], str]] = {}

    for position, value in enumerate(kept):
        order = tuple(_tokens(value))
        score = _alias_format_score(value)
        current = best_by_order.get(order)

        if current is None or score < current[1]:
            best_by_order[order] = (position, score, value)

    winners = sorted(best_by_order.values(), key=lambda item: item[0])
    return [value for _, _, value in winners]


def _alias_format_score(value: str) -> tuple[int, int, int]:
    """同词序别名的格式评分，分数越低越优先."""
    punctuation = len(re.findall(r"[^\w\s'-]", value))
    spacing = int(value != " ".join(value.split()))

    casing = 0
    for token in value.split():
        if len(token) > 1 and token.isupper():
            casing += 2
        elif len(token) > 1 and token.islower():
            casing += 1

    return punctuation, spacing, casing


def _strip_import_code(value: str, name: str) -> str | None:
    """删除批量导入留下的编号后缀; 编号底名不是本人名字时返回 None."""
    match = _IMPORT_CODE_RE.match(value) or _ID_SUFFIX_RE.match(value)
    if match is None:
        return value

    base = match.group(1).strip()
    name_tokens = _tokens(name)
    last = name_tokens[-1] if name_tokens else ""

    return base if _tokens(base)[:1] == [last] else None


def _is_repeat(tokens: list[str], name: str) -> bool:
    """判断别名是否重复包含本人姓名."""
    name_tokens = _tokens(name)

    if not len(name_tokens) < len(tokens) <= 2 * len(name_tokens):
        return False
    if not set(tokens) <= set(name_tokens):
        return False

    return _contains_in_order(tokens, name_tokens)


def _contains_in_order(tokens: list[str], expected: list[str]) -> bool:
    """expected 是否为 tokens 的子序列."""
    cursor = 0

    for want in expected:
        while cursor < len(tokens) and tokens[cursor] != want:
            cursor += 1

        if cursor == len(tokens):
            return False

        cursor += 1

    return True


def _is_splice(tokens: list[str], name: str, known: list[str]) -> bool:
    """别名由本人两个已有名字拼接而成时丢弃."""
    signatures = {_token_signature(other) for other in (name, *known)}

    if len(tokens) < 4:
        return False

    return any(
        frozenset(tokens[:cut]) in signatures and frozenset(tokens[cut:]) in signatures
        for cut in range(2, len(tokens) - 1)
    )


def _tokens(value: str) -> list[str]:
    return re.findall(r"[\w']+", value.casefold())


def _token_signature(value: str) -> frozenset[str]:
    return frozenset(_tokens(value))


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None

    return value
