"""持久化的原始标签→中文标签映射。词典放在 Amane 数据目录，不随源码升级丢失。"""

import json
import os
import re
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

LanguageCode = Literal["ja", "en", "zh", "other"]


def detect_language(value: str) -> LanguageCode:
    if re.search(r"[\u3040-\u30ff]", value):
        return "ja"
    if re.search(r"[A-Za-z]", value):
        return "en"
    if re.search(r"[\u4e00-\u9fff]", value):
        # 纯汉字词条在日文/中文之间可能有歧义；仅凭字符无法可靠判断。
        # 只有包含常见简体字线索时才归为中文，其余放入“其他/未知”。
        simplified_hints = set(
            "这为与专业东丝两严丧个丰临丽举义乌乐习书买乱争云亚产亲亿仅从仓仪们价众优会伟传伤伦体余侠侣侥侦侧侨俭债倾偿儿党兰关兴养兽内冈册写军农冲决况冻净准凉减凤凭击刘则刚创删剂剑剧劝办务动励劳势区医华协单卖卫却厅历压厌县参双发变叙叠叶号叹吗听员呜响哑哗唤喷团园围国图圆场坏块坚坛坝坟垫墙声壳处备复头夹夺奋奖妆妇妈娇孙学宁宝实宠审宪宫宽宾对寻导寿将尔尘尝层岁岗岛岭峡币帅师帐带帮干并广庄庆库应废开异弃张弯弹强归当录忆忧怀总恋恶恼悦悬惊惧惨惯戏战户扑执扩扫扬扰抚抢护报担拟拢拣拥拦拨挠挡挣挤挥损捡换据掷揽搅携摄摆摇摊敌数斗断无旧时旷显晒晓暂术朴机杀杂权条来杨极构枪枫柜标栈栋栏树样桥档检楼欢欧残毁毕气汇汉汤沟没沪泪泷泸泻泼泽洁洒浇浊测济浑浓涛涝涟涡润涨涩渊渍渐渔湾湿溃溅滚滞满滤滥滨滩灭灯灵灾灿炉点炼烂烟烦烧热爱爷牵犹独狭狮狱猎猪猫献环现电画畅疗疯瘫皱盐监盖矿码砖础确碍祸离种积称稳穷窍竞笔筛筹签简篮类粮紧纠纤约级纪纯纱纲纳纵纷纸纹纽线练组细织终经绑绒结绕绘给络绝统绢绣继绩绪续绿缀缓编缘缝缠罗罚罢职联聪肃肠肤肾肿胀胆胜脏脑脚脱脸舰舱艰艳艺节药萝萧蓝虑虽虾蚀蚁蛮蝇补衬袜袭装裤见观规觅视览觉计订认讨让训议讯记讲论设访诀证评识诉诊词译试诗诚话诞询该详语误诱说请读课谁调谅谈谋谎谢谣贝负贡财责贤败账货质贩贪贫购贮贯贱贴贵贷贸费贺贼资赃赎赏赔赖赛赞赠赵赶趋跃践辉辑输辖辞辩边辽达迁过运还这进远违连选递遗邮邻郑钉针钓钙钝钞钟钢钥钦钧钩钮钱钳钻铁铃铅铜铝银铺链销锁锅锋锐错锡锚锤锦键锯镇镜长门闪闭问闯闲间闷闸闹闻阀阅队阳阴阵阶际陆陈险随隐难雾静顶项顺须顾顿颂预领频颗题颜额风飞饥饭饮饰饱饲饶饺饼饿馆馈马驭驮驯驰驱驳驴驶驻驼驾骂骄验骑骗骚骤鱼鲜鸟鸡鸣鸭鹅麦黄齐齿龙龟"
        )
        if any(char in simplified_hints for char in value):
            return "zh"
        return "other"
    return "other"


@dataclass
class TagEntry:
    original: str
    translation: str | None = None
    language: LanguageCode = "other"
    usage_count: int = 0
    sources: list[str] | None = None
    #: 归档标记. 归档的词条仍然参与映射与计数, 只是不出现在主列表里.
    #: 删除做成归档而不是真删, 是为了不让「刮削后又回来」: 真正删掉的词条会在下一次
    #: seed 时凭标签表重建出来, 归档则会被 seed 跳过.
    archived: bool = False


class TagMappingStore:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "tag_mappings.json"
        self._lock = threading.RLock()
        self._entries: dict[str, TagEntry] = {}
        self._last_seed = 0.0
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for key, value in raw.get("entries", {}).items():
                if isinstance(value, dict):
                    self._entries[key] = TagEntry(
                        original=key,
                        translation=value.get("translation") or None,
                        language=value.get("language") or detect_language(key),
                        usage_count=max(0, int(value.get("usage_count", 0))),
                        sources=list(value.get("sources") or []),
                        archived=bool(value.get("archived", False)),
                    )
        except OSError, ValueError, TypeError:
            # 不覆盖损坏文件；让服务保持可用，后续保存时使用当前可解析数据。
            self._entries = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "entries": {k: asdict(v) for k, v in sorted(self._entries.items())}}
        fd, name = tempfile.mkstemp(prefix="tag-mappings-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def needs_seed(self) -> bool:
        return time.monotonic() - self._last_seed > 60

    def seed(self, values: list[tuple[str, int]]) -> None:
        """从现有标签分类表导入待映射词条与真实引用数，不覆盖已有人工作出的映射。"""
        with self._lock:
            changed = False
            for original, count in values:
                if not original:
                    continue
                entry = self._entries.get(original)
                if entry is None:
                    self._entries[original] = TagEntry(
                        original, language=detect_language(original), usage_count=max(0, count), sources=[]
                    )
                    changed = True
                elif entry.usage_count != count:
                    entry.usage_count = max(0, count)
                    changed = True
            self._last_seed = time.monotonic()
            if changed:
                self._save()

    def observe(self, tags: list[str], source: str | None = None) -> None:
        with self._lock:
            changed = False
            for tag in tags:
                name = tag.strip()
                if not name:
                    continue
                entry = self._entries.get(name)
                if entry is None:
                    entry = TagEntry(name, language=detect_language(name), sources=[])
                    self._entries[name] = entry
                    changed = True
                entry.usage_count += 1
                if source and source not in (entry.sources or []):
                    entry.sources = [*(entry.sources or []), source]
                    changed = True
                changed = True
            if changed:
                self._save()

    def map_tags(self, tags: list[str]) -> list[str]:
        with self._lock:
            # 必须在映射后去重；未映射词条原样保留。
            translated = [
                self._entries[tag].translation if tag in self._entries and self._entries[tag].translation else tag
                for tag in tags
                if tag
            ]
            return list(dict.fromkeys(translated))

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

    def list(
        self,
        *,
        status: str = "all",
        language: str = "all",
        frequency: str = "all",
        search: str = "",
        archived: bool = False,
        include_archived: bool = False,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[TagEntry], int]:
        """按归档状态与筛选条件取一页词条.

        - ``archived=True``: 只看归档区
        - ``include_archived=True``: 主列表与归档区一起看 (「全部词条」那档)
        - 都不给: 只看未归档的

        归档的词条仍参与映射与计数, 这里只决定它们出现在哪一档, 不改变是否生效.
        """
        with self._lock:
            if archived:
                values = [v for v in self._entries.values() if v.archived]
            elif include_archived:
                values = list(self._entries.values())
            else:
                values = [v for v in self._entries.values() if not v.archived]
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

    def counts(self) -> dict[str, int]:
        """``all`` 含归档 (与「全部词条」那一档一致); 其余各项只算未归档的.

        ``archived`` 单独给出, 因此两组数字仍能对上, 不会出现「全部」小于另两项之和的怪象.
        """
        with self._lock:
            active = [entry for entry in self._entries.values() if not entry.archived]
            mapped = sum(1 for entry in active if entry.translation)
            archived = sum(1 for entry in self._entries.values() if entry.archived)
            return {
                "all": len(self._entries),
                "mapped": mapped,
                "pending": len(active) - mapped,
                "archived": archived,
            }

    def upsert(self, original: str, translation: str | None) -> TagEntry:
        original = original.strip()
        if not original:
            raise ValueError("原始标签不能为空")
        with self._lock:
            entry = self._entries.get(original) or TagEntry(original, language=detect_language(original), sources=[])
            entry.translation = translation.strip() if translation and translation.strip() else None
            self._entries[original] = entry
            self._save()
            return entry

    def get(self, original: str) -> TagEntry | None:
        """按原名取一条; 调用方据此区分「不存在」与「有译名不能归档」."""
        with self._lock:
            return self._entries.get(original)

    def delete(self, original: str) -> bool:
        """归档词条 (不真删).

        真删会让它在下次 seed 时按标签表重建出来 —— 用户看到的就是「删了又回来」.
        归档后 seed 仍会更新它的引用数, 映射也照旧生效, 只是不在主列表里显示.

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

    def export(self) -> dict:
        """导出「动过的」词条: 已映射的与已归档的.

        待映射的多是从标签表自动同步进来的原始词条, 既没译名也没归档决定, 导出它们只是
        把噪声一起带走; 更糟的是导回别处会重建出一堆空条目.

        只带「决定」: 译文、语言、归档. ``usage_count`` 与本机 ``sources`` 是这台机器上的
        统计, 给别人导入会把对方的计数覆盖成本机的数字 —— 那是它自己的数据, 不该被带过去.
        """
        with self._lock:
            kept = {
                key: {
                    "original": entry.original,
                    "translation": entry.translation,
                    "language": entry.language,
                    "archived": entry.archived,
                }
                for key, entry in sorted(self._entries.items())
                if entry.archived or entry.translation
            }
            return {"version": 1, "entries": kept}

    def import_data(self, payload: dict) -> int:
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
                    current = TagEntry(original, language=detect_language(original), sources=[])
                    self._entries[original] = current
                current.translation = (
                    translation.strip() if isinstance(translation, str) and translation.strip() else None
                )
                language = value.get("language")
                if language in {"ja", "en", "zh", "other"}:
                    current.language = language
                # 计数与来源是本机统计, 只由本地观察/seed 维护. 旧版导出文件里带着这两项,
                # 但导入别人的词典不该把本机的数字改掉, 因此有就跳过.
                # 归档状态要一起带过来, 否则导回后这些词条又回到主列表,
                # 等于把「不显示但仍在用」这个决定丢了.
                if isinstance(value.get("archived"), bool):
                    current.archived = value["archived"]
                count += 1
            self._save()
        return count
