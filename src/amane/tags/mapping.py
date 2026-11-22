"""原始名 → 中文显示名的映射词典, 按页面上的滑动选项分组持有.

映射只影响「怎么显示」: 搜索、去重与刮削的比对一律仍用原始名, 否则同一个名称换个显示名
就会被当成另一个. 每组一份 JSON 在数据目录, 不随源码升级丢失, 也不碰数据库.

条目分两种来源. **自动同步**的由 ``sync`` 按分类表对账出来, 当前分类表里没有这个名字 (或
引用数已归零) 就删掉 —— 用户删掉标签后映射表里留一条「0 次」既没用也碍眼. **手动**新增的
(自己填的译名条目、导入的词典) 不受对账删除: 词典本就可能指向还没入库的名字.

片商那一组把制作商 / 发行商 / 系列并成**一张表**: 同一个名字在制作商与发行商里都出现过时只留
一条, 用 ``roles`` 记住它出现在哪几个分类里. 三者本来就是同一次翻译, 分开存会让同一个词在
两处各有一份译名, 迟早对不上.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal

LanguageCode = Literal["ja", "en", "zh", "other"]


class MappingGroup(StrEnum):
    """页面上的滑动选项; 每项一张表, 各自独立对账与展示."""

    TAG = "tag"
    ACTOR = "actor"
    VENDOR = "vendor"


MAPPING_GROUPS: tuple[MappingGroup, ...] = tuple(MappingGroup)


class VendorRole(StrEnum):
    """片商表里一条词条可以身兼的分类; 一个名字同时是制作商与发行商时只占一行."""

    STUDIO = "studio"
    PUBLISHER = "publisher"
    SERIES = "series"


VENDOR_ROLES: tuple[VendorRole, ...] = tuple(VendorRole)

MAPPING_FILES: dict[MappingGroup, str] = {
    MappingGroup.TAG: "tag_mappings.json",
    MappingGroup.ACTOR: "actor_name_mappings.json",
    MappingGroup.VENDOR: "vendor_mappings.json",
}

#: 片商三分类合表之前各存一份的文件; 首次加载时并进 ``vendor_mappings.json``.
LEGACY_VENDOR_FILES: dict[VendorRole, str] = {
    VendorRole.STUDIO: "studio_mappings.json",
    VendorRole.PUBLISHER: "publisher_mappings.json",
    VendorRole.SERIES: "series_mappings.json",
}


def detect_language(value: str) -> LanguageCode:
    # 假名是日文的硬特征. 汉字单独出现时分不出中日, 一律按中文算 ——
    # 「其他」只是个说不清的空档, 摆出来也帮不了「要不要翻译」这个决定.
    # 汉字判定放在拉丁字母之前: 混着英文的汉字词条仍以中文为主, 按英文算更没用.
    if re.search(r"[\u3040-\u30ff]", value):
        return "ja"
    if re.search(r"[\u4e00-\u9fff]", value):
        return "zh"
    if not re.search(r"[A-Za-z]", value):
        return "other"
    # 带数字的技术标记 (1080p / 60fps / 3P / 4K) 不是英语单词: 它们没有可译的语义,
    # 归到英文只会让英文那一档混进一堆不需要翻译的东西.
    return "other" if re.search(r"\d", value) else "en"


@dataclass
class MappingEntry:
    original: str
    """原始名; 作为键, 与库里存的名称完全一致."""

    translation: str | None = None
    """显示用中文名; 为空表示未映射."""

    language: LanguageCode = "other"
    usage_count: int = 0
    sources: list[str] | None = None
    #: 归档标记 (仅标签). 归档的词条仍然参与映射与计数, 只是不出现在主列表里.
    #: 删除做成归档而不是真删, 是为了不让「刮削后又回来」: 真正删掉的词条会在下一次
    #: 对账时凭分类表重建出来, 归档则不会被对账跳过.
    archived: bool = False
    #: 手动新增: 对账时即使当前分类表里没有这个名字也保留.
    manual: bool = False
    #: 这一条出现在片商组的哪几个分类里 (仅片商表). 空表示手动新增, 对三类都生效.
    roles: list[str] = field(default_factory=list)


class MappingStore:
    """一组名称的映射. 读写都在锁内, 保存走临时文件替换."""

    group: MappingGroup = MappingGroup.TAG

    def __init__(self, data_dir: Path, group: MappingGroup = MappingGroup.TAG) -> None:
        self.group = group
        self.path = data_dir / MAPPING_FILES[group]
        self._lock = threading.RLock()
        self._entries: dict[str, MappingEntry] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for key, value in (raw.get("entries") or {}).items():
                if isinstance(value, dict):
                    self._entries[key] = MappingEntry(
                        original=key,
                        translation=(value.get("translation") or None),
                        # 语言按当前规则重算: 存下来的值可能是旧规则认不出的汉字词条.
                        language=detect_language(key),
                        usage_count=max(0, int(value.get("usage_count", 0))),
                        sources=list(value.get("sources") or []),
                        archived=bool(value.get("archived", False)),
                        manual=bool(value.get("manual", False)),
                        roles=[str(role) for role in value.get("roles") or []],
                    )
        except OSError, ValueError, TypeError:
            # 损坏文件不覆盖: 保持服务可用, 后续保存时用当前可解析的内容重写.
            self._entries = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "entries": {k: asdict(v) for k, v in sorted(self._entries.items())}}
        fd, name = tempfile.mkstemp(prefix=f"{self.group}-mappings-", suffix=".json", dir=self.path.parent)
        temp_path = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            temp_path.replace(self.path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    # -- 对账 -------------------------------------------------------------

    def sync(
        self,
        values: list[tuple[str, int]],
        *,
        sources: dict[str, list[str]] | None = None,
    ) -> None:
        """按当前分类表对账: 更新引用数, 建出缺的自动条目, 删掉自动条目里已不存在的名字.

        引用数降到 0 也算不存在: 分类表里留着一条挂不上任何影片的记录时, 映射表再留一条
        「0 次」既没用也碍眼. 手动条目只更新计数, 不因对账消失.

        ``sources`` 是分类名 → 供给它的刮削来源. 对账是唯一稳定的写入点, 顺带把来源对齐:
        来源本就是从条目上读出来的, 攒在映射表里只是省得每次展示都回查一遍.
        """
        self._apply_sync([(None, name, count) for name, count in values], sources=sources)

    def _apply_sync(
        self,
        values: list[tuple[str | None, str, int]],
        *,
        sources: dict[str, list[str]] | None = None,
    ) -> None:
        """对账本体. ``role`` 为 None 表示这一组只有一种分类 (标签 / 演员)."""
        with self._lock:
            present: dict[str, set[str] | None] = {}
            changed = False
            for role, name, count in values:
                if not name:
                    continue
                if role is None:
                    present[name] = None
                else:
                    present.setdefault(name, set()).add(role)
                entry = self._entries.get(name)
                if entry is None:
                    self._entries[name] = MappingEntry(
                        original=name,
                        language=detect_language(name),
                        usage_count=max(0, count),
                        sources=list(sources.get(name, [])) if sources else [],
                        roles=[role] if role is not None else [],
                    )
                    changed = True
                    continue
                if entry.usage_count != max(0, count):
                    entry.usage_count = max(0, count)
                    changed = True
                if role is not None and role not in entry.roles:
                    entry.roles = [*entry.roles, role]
                    changed = True
            if sources is not None:
                # 来源跟着条目一起走: 没在新结果里的就清掉, 别留着已经改投别处的旧站点.
                for name, entry in self._entries.items():
                    if entry.manual or name not in present:
                        continue
                    fresh = list(sources.get(name, []))
                    if entry.sources != fresh:
                        entry.sources = fresh
                        changed = True
            for name, entry in list(self._entries.items()):
                if entry.manual:
                    continue
                if name not in present or not entry.usage_count:
                    del self._entries[name]
                    changed = True
                    continue
                # 同一个名字不再属于某个分类时, 把那个分类从 roles 里摘掉.
                roles = present[name]
                if roles is not None and set(entry.roles) != roles:
                    entry.roles = [role for role in entry.roles if role in roles]
                    changed = True
            if changed:
                self._save()

    # -- 展示 -------------------------------------------------------------

    def display(self, original: str) -> str:
        """显示名; 未映射时回落到原始名."""
        with self._lock:
            entry = self._entries.get(original)
            return entry.translation if entry and entry.translation else original

    def display_map(self, originals: list[str]) -> dict[str, str]:
        """批量取显示名, 只返回有映射的项."""
        with self._lock:
            result: dict[str, str] = {}
            for name in originals:
                entry = self._entries.get(name)
                if entry and entry.translation:
                    result[name] = entry.translation
            return result

    def translations(self, role: str | None = None) -> dict[str, str]:
        """整个 原名 → 译名 表的快照; ``role`` 只取在该分类里出现过的条目."""
        with self._lock:
            return {
                name: entry.translation
                for name, entry in self._entries.items()
                if entry.translation and self._matches_role(entry, role)
            }

    @staticmethod
    def _matches_role(entry: MappingEntry, role: str | None) -> bool:
        if role is None:
            return True
        # 手动条目没有 roles: 用户自己填的名字对每一类都生效.
        return not entry.roles or role in entry.roles

    # -- 查询 -------------------------------------------------------------

    def list(
        self,
        *,
        status: str = "all",
        language: str = "all",
        frequency: str = "all",
        search: str = "",
        archived: bool = False,
        include_archived: bool = False,
        role: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[MappingEntry], int]:
        """按归档状态与筛选条件取一页词条.

        - ``archived=True``: 只看归档区
        - ``include_archived=True``: 主列表与归档区一起看 (「全部词条」那档)
        - 都不给: 只看未归档的

        归档的词条仍参与映射与计数, 这里只决定它们出现在哪一档, 不改变是否生效.
        ``role`` 只对片商表有意义: 同一个名字身兼两类时仍只占一行.
        """
        with self._lock:
            if archived:
                values = [v for v in self._entries.values() if v.archived]
            elif include_archived:
                values = list(self._entries.values())
            else:
                values = [v for v in self._entries.values() if not v.archived]
            if role is not None:
                values = [v for v in values if self._matches_role(v, role)]
            if status == "pending":
                values = [v for v in values if not v.translation]
            elif status == "mapped":
                values = [v for v in values if v.translation]
            if language != "all":
                values = [v for v in values if v.language == language]
            if frequency == "high":
                values = [v for v in values if v.usage_count >= 5]
            if search:
                needle = search.casefold()
                values = [
                    v for v in values if needle in v.original.casefold() or needle in (v.translation or "").casefold()
                ]
            values.sort(key=lambda v: (-v.usage_count, v.original.casefold()))
            return values[offset : offset + limit], len(values)

    def counts(self, role: str | None = None) -> dict[str, int]:
        """``all`` 含归档 (与「全部词条」那一档一致); 其余各项只算未归档的.

        ``archived`` 单独给出, 因此两组数字仍能对上, 不会出现「全部」小于另两项之和的怪象.
        """
        with self._lock:
            scoped = [entry for entry in self._entries.values() if self._matches_role(entry, role)]
            active = [entry for entry in scoped if not entry.archived]
            mapped = sum(1 for entry in active if entry.translation)
            archived = sum(1 for entry in scoped if entry.archived)
            return {
                "all": len(scoped),
                "mapped": mapped,
                "pending": len(active) - mapped,
                "archived": archived,
            }

    def get(self, original: str) -> MappingEntry | None:
        """按原名取一条; 调用方据此区分「不存在」与「有译名不能归档」."""
        with self._lock:
            return self._entries.get(original)

    # -- 写入 -------------------------------------------------------------

    def upsert(self, original: str, translation: str | None, roles: list[str] | None = None) -> MappingEntry:
        """写入译名.

        只有**新建**的条目算手动条目 (对账不删): 给已同步出来的条目填译名只是一个决定,
        不改变它的来源, 否则删掉标签后这条映射会赖在表里不走.
        """
        original = original.strip()
        if not original:
            raise ValueError("原始名不能为空")
        with self._lock:
            entry = self._entries.get(original)
            if entry is None:
                entry = MappingEntry(original, language=detect_language(original), sources=[], manual=True)
                self._entries[original] = entry
            entry.translation = translation.strip() if translation and translation.strip() else None
            if roles is not None:
                entry.roles = [str(role) for role in roles]
            self._save()
            return entry

    def remove(self, original: str) -> bool:
        """真删这条映射."""
        with self._lock:
            if original not in self._entries:
                return False
            del self._entries[original]
            self._save()
            return True

    def delete(self, original: str) -> bool:
        """归档词条 (不真删).

        真删会让它在下次对账时按分类表重建出来 —— 用户看到的就是「删了又回来」.
        归档后对账仍会更新它的引用数, 映射也照旧生效, 只是不在主列表里显示.

        已映射的不许归档: 归档区表示「本来就是中文, 不需要译名」, 带着译名进去自相矛盾,
        而且一键误点就丢了一个人工决定. 要归档先清空译名.
        """
        with self._lock:
            entry = self._entries.get(original)
            if entry is None or entry.archived or entry.translation:
                return False
            entry.archived = True
            self._save()
            return True

    def restore(self, original: str) -> bool:
        """把归档的词条放回主列表."""
        with self._lock:
            entry = self._entries.get(original)
            if entry is None or not entry.archived:
                return False
            entry.archived = False
            self._save()
            return True

    def rename(self, original: str, new_name: str) -> None:
        """分类实体改名时把映射一起搬过去, 免得译名留在旧名字上.

        只在旧名有条目且新名还没有条目时搬; 否则保留新名自己的映射.
        """
        new_name = new_name.strip()
        if not new_name or new_name == original:
            return
        with self._lock:
            entry = self._entries.get(original)
            if entry is None or new_name in self._entries:
                return
            entry.original = new_name
            entry.language = detect_language(new_name)
            del self._entries[original]
            self._entries[new_name] = entry
            self._save()

    # -- 导入导出 ---------------------------------------------------------

    def export(self) -> dict:
        """导出「动过的」词条: 已映射的与已归档的.

        待映射的多是从分类表自动同步进来的原始词条, 既没译名也没归档决定, 导出它们只是
        把噪声一起带走; 更糟的是导回别处会重建出一堆空条目.

        只带「决定」: 译文、语言、归档、手动标记、所属分类. ``usage_count`` 与本机 ``sources``
        是这台机器上的统计, 给别人导入会把对方的计数覆盖成本机的数字 —— 那是它自己的数据.
        """
        with self._lock:
            kept = {
                key: {
                    "original": entry.original,
                    "translation": entry.translation,
                    "language": entry.language,
                    "archived": entry.archived,
                    "manual": entry.manual,
                    "roles": list(entry.roles),
                }
                for key, entry in sorted(self._entries.items())
                if entry.archived or entry.translation
            }
            return {"version": 1, "entries": kept}

    def import_data(self, payload: dict) -> int:
        """导入词典; 文件里带来而本地没有的名字按手动条目建出, 对账不会删掉它们."""
        entries = payload.get("entries", {})
        if not isinstance(entries, dict):
            raise ValueError("导入文件格式不正确")
        count = 0
        with self._lock:
            for original, value in entries.items():
                if not isinstance(original, str) or not isinstance(value, dict):
                    continue
                current = self._entries.get(original)
                translation = value.get("translation")
                if current is None:
                    current = MappingEntry(original, language=detect_language(original), sources=[], manual=True)
                    self._entries[original] = current
                current.translation = (
                    translation.strip() if isinstance(translation, str) and translation.strip() else None
                )
                language = value.get("language")
                if language in {"ja", "en", "zh", "other"}:
                    current.language = language
                if isinstance(value.get("manual"), bool):
                    current.manual = value["manual"]
                roles = value.get("roles")
                if isinstance(roles, list):
                    current.roles = [str(role) for role in roles if str(role) in VENDOR_ROLES]
                # 计数与来源是本机统计, 只由本地观察 / 对账维护. 旧版导出文件里带着这两项,
                # 但导入别人的词典不该把本机的数字改掉, 因此有就跳过.
                # 归档状态要一起带过来, 否则导回后这些词条又回到主列表,
                # 等于把「不显示但仍在用」这个决定丢了.
                if isinstance(value.get("archived"), bool):
                    current.archived = value["archived"]
                count += 1
            self._save()
        return count


class TagMappingStore(MappingStore):
    """标签映射: 多出刮削观察、成对展示与归档语义."""

    group = MappingGroup.TAG

    def __init__(self, data_dir: Path) -> None:
        super().__init__(data_dir, MappingGroup.TAG)
        self._last_seed = 0.0

    def needs_seed(self) -> bool:
        return time.monotonic() - self._last_seed > 60

    def seed(self, values: list[tuple[str, int]], *, sources: dict[str, list[str]] | None = None) -> None:
        """兼容旧调用点: 按分类表对账并记下本次对账时间."""
        with self._lock:
            self.sync(values, sources=sources)
            self._last_seed = time.monotonic()

    def observe(self, tags: list[str], source: str | None = None) -> None:
        """刮削时观察到的标签: 建出条目并累加计数, 记下来源."""
        with self._lock:
            changed = False
            for tag in tags:
                name = tag.strip()
                if not name:
                    continue
                entry = self._entries.get(name)
                if entry is None:
                    entry = MappingEntry(name, language=detect_language(name), sources=[])
                    self._entries[name] = entry
                entry.usage_count += 1
                if source and source not in (entry.sources or []):
                    entry.sources = [*(entry.sources or []), source]
                changed = True
            if changed:
                self._save()

    def map_tags(self, tags: list[str]) -> list[str]:
        with self._lock:
            # 必须在映射后去重；未映射词条原样保留。
            translated: list[str] = []
            for tag in tags:
                if not tag:
                    continue
                entry = self._entries.get(tag)
                translated.append(entry.translation if entry and entry.translation else tag)
            return list(dict.fromkeys(translated))

    def map_tag_pairs(self, tags: list[str]) -> list[tuple[str, str]]:
        """把库里存的标签名收成 ``(原名, 展示名)`` 且保序去重.

        详情页要能在「原文 / 中文」之间切换, 所以展示名与原名必须成对给出: 只给译文的话,
        前端只能靠「译文 → 原名」反查, 而两个不同原名映射到同一译文时反查必然丢掉一个.

        去重按展示名: 与 ``map_tags`` 同一口径 —— 界面上一行里同一个译文出现两次没有意义,
        后到的那条直接丢弃, 保留先出现的那条原名.
        """
        with self._lock:
            seen: set[str] = set()
            out: list[tuple[str, str]] = []
            for tag in tags:
                if not tag:
                    continue
                entry = self._entries.get(tag)
                shown = entry.translation if entry and entry.translation else tag
                if shown in seen:
                    continue
                seen.add(shown)
                out.append((tag, shown))
            return out

    def originals_matching(self, needle: str) -> list[str]:
        """按展示名找原名的那些词条.

        列表里显示的是译文, 而库里存的是原名; 只按原名搜索会让「搜得到的字在列表里有、
        却搜不出来」—— 用户照着界面上的名字输入, 必须能命中.
        只按译文匹配, 原名自己的匹配交给数据库那侧.
        """
        if not needle:
            return []
        lowered = needle.casefold()
        with self._lock:
            return [
                entry.original
                for entry in self._entries.values()
                if entry.translation and lowered in entry.translation.casefold()
            ]

    def map_id_map(self, values: dict[str, int]) -> dict[str, int]:
        """将详情页标签名映射到同名展示标签，保留现有 FacetBadge 的 ID 筛选动作。"""
        mapped: dict[str, int] = {}
        with self._lock:
            for original, facet_id in values.items():
                entry = self._entries.get(original)
                display = entry.translation if entry and entry.translation else original
                mapped.setdefault(display, facet_id)
        return mapped


class ActorMappingStore(MappingStore):
    """演员显示名映射; 归档语义与标签一致 (在基类)."""

    group = MappingGroup.ACTOR

    def __init__(self, data_dir: Path) -> None:
        super().__init__(data_dir, MappingGroup.ACTOR)


class VendorMappingStore(MappingStore):
    """片商三分类合表: 制作商 / 发行商 / 系列共用一张, 同名只占一行."""

    group = MappingGroup.VENDOR

    def __init__(self, data_dir: Path) -> None:
        super().__init__(data_dir, MappingGroup.VENDOR)
        if not self.path.exists():
            self._absorb_legacy(data_dir)

    def _absorb_legacy(self, data_dir: Path) -> None:
        """把合表之前的三份文件并进来: 同名条目合成一条并记住它属于哪些分类."""
        merged = False
        with self._lock:
            for role, filename in LEGACY_VENDOR_FILES.items():
                legacy = data_dir / filename
                if not legacy.is_file():
                    continue
                try:
                    raw = json.loads(legacy.read_text(encoding="utf-8"))
                except OSError, ValueError:
                    continue
                for key, value in (raw.get("entries") or {}).items():
                    if not isinstance(key, str) or not isinstance(value, dict):
                        continue
                    entry = self._entries.get(key)
                    if entry is None:
                        entry = MappingEntry(
                            original=key,
                            translation=(value.get("translation") or None),
                            language=detect_language(key),
                            usage_count=max(0, int(value.get("usage_count", 0))),
                            sources=list(value.get("sources") or []),
                            manual=bool(value.get("manual", False)),
                            roles=[],
                        )
                        self._entries[key] = entry
                    elif not entry.translation:
                        entry.translation = value.get("translation") or None
                    if role not in entry.roles:
                        entry.roles = [*entry.roles, role]
                    merged = True
            if merged:
                self._save()

    def sync_roles(
        self,
        values: list[tuple[str, str, int]],
        *,
        sources: dict[str, list[str]] | None = None,
    ) -> None:
        """按三个分类表对账; 同一个名字在多个分类里出现时只留一条, ``roles`` 记全部归属."""
        self._apply_sync([(role, name, count) for role, name, count in values], sources=sources)


class MappingRegistry:
    """三组映射的集中持有; 每组一个文件, 各自独立加锁."""

    def __init__(self, data_dir: Path) -> None:
        self.tags = TagMappingStore(data_dir)
        self.actors = ActorMappingStore(data_dir)
        self.vendors = VendorMappingStore(data_dir)

    def store(self, group: MappingGroup) -> MappingStore:
        if group is MappingGroup.TAG:
            return self.tags
        if group is MappingGroup.ACTOR:
            return self.actors
        return self.vendors

    def display(self, group: MappingGroup, original: str) -> str:
        """某一组的显示名; 未映射时回落到原始名."""
        return self.store(group).display(original)

    def vendor_roots(self) -> dict[VendorRole, dict[str, str]]:
        """片商三类的 原名 → 译名 快照, 供影片展示与 NFO 一次取齐."""
        return {role: self.vendors.translations(role) for role in VENDOR_ROLES}

    def sync(
        self,
        group: MappingGroup,
        values: list[tuple[str, int]],
        *,
        sources: dict[str, list[str]] | None = None,
    ) -> None:
        """按分类表对账某一组; 标签那一组顺带记下本次对账时间, 免得列表每次都全量扫."""
        store = self.store(group)
        if isinstance(store, TagMappingStore):
            store.seed(values, sources=sources)
        else:
            store.sync(values, sources=sources)

    def sync_vendor(
        self,
        values: list[tuple[str, str, int]],
        *,
        sources: dict[str, list[str]] | None = None,
    ) -> None:
        """片商组要带上分类: 同一个名字可能同时是制作商与发行商."""
        self.vendors.sync_roles(values, sources=sources)

    def needs_sync(self, group: MappingGroup) -> bool:
        store = self.store(group)
        return not isinstance(store, TagMappingStore) or store.needs_seed()

    def rename(self, group: MappingGroup, original: str, new_name: str) -> None:
        """分类实体改名时把译名一起搬走."""
        self.store(group).rename(original, new_name)

    def forget(self, group: MappingGroup, original: str) -> None:
        """分类实体被删掉时清掉它的**自动**映射.

        手动条目与导入的词典不跟着消失: 那些是用户自己做过的决定, 与分类表里还有没有这个
        名字无关 —— 词典本就可能指向还没入库的名字.
        """
        store = self.store(group)
        entry = store.get(original)
        if entry is not None and not entry.manual:
            store.remove(original)
