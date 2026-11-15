"""演员实体自动合并判定.

刮削会把同一个人的不同艺名建成多个 ``Actor``。判定合并需要两道闸门:

1. **名字有交集** —— 展示名或任一侧别名命中对方任一名字. 名字只是"疑似", 不能单独作为依据:
   日文艺名重名常见, 「あかり」可能是多人.
2. **双方都有的身体数据一致** —— 生日、身高、三围、罩杯, 有哪项比哪项, 只要双方都有值就必须
   相等. 不比"齐全": 许多演员只刮到其中一两项, 强求生日身高俱全等于永不合并. 而任一项对不上
   就足以说明不是同一人, 已能挡住重名.
   一项可比的资料都没有时不合并 —— 只凭名字会把重名的不同人并成一个.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ..db.models import Actor, ActorAlias


def normalize_name(name: str | None) -> str:
    """名字归一: NFKC + 去空白 + casefold. 全角半角、大小写差异视为同一名字."""
    if not name:
        return ""
    folded = unicodedata.normalize("NFKC", name).casefold()
    return re.sub(r"[\s\u3000]+", "", folded)


def normalize_birthday(value: str | None) -> str | None:
    """``1982-10-06`` / ``1982/10/06`` / ``19821006`` 统一成 ``1982-10-06``."""
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    if len(digits) != 8:
        return None
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"


def normalize_cup(value: str | None) -> str | None:
    if not value:
        return None
    folded = unicodedata.normalize("NFKC", value).strip().upper()
    return folded or None


@dataclass(frozen=True, slots=True)
class MergeVerdict:
    """一次比对的结果; ``reason`` 说明为何拒绝, 便于日志与人工复核."""

    merge: bool
    reason: str


def _names_of(actor: Actor, aliases: list[str]) -> set[str]:
    names = {normalize_name(actor.name)}
    names.update(normalize_name(alias) for alias in aliases)
    names.discard("")
    return names


def _field(value: object) -> object:
    """三围/身高这类整数, 0 与 None 都视为缺失."""
    return value or None


def compare(
    left: Actor,
    left_aliases: list[str],
    right: Actor,
    right_aliases: list[str],
) -> MergeVerdict:
    """判定两位演员是否为同一人.

    闸门一: 名字有交集. 闸门二: 双方都有的身体数据必须一致 —— 有生日就比生日, 有身高就比
    身高, 有哪项比哪项. 不要求这些字段同时齐备: 许多演员只刮到其中一两项, 强求齐全等于
    永不合并, 而任何一项对不上都足以说明不是同一人, 已经足以挡住重名.
    """
    left_names = _names_of(left, left_aliases)
    right_names = _names_of(right, right_aliases)
    if not (left_names & right_names):
        return MergeVerdict(False, "名字无交集")

    # 生日: 双方都有时才比较.
    left_birthday, right_birthday = normalize_birthday(left.birthday), normalize_birthday(right.birthday)
    compared = False
    if left_birthday is not None and right_birthday is not None:
        if left_birthday != right_birthday:
            return MergeVerdict(False, f"生日不同 {left_birthday} / {right_birthday}")
        compared = True

    # 身高: 双方都有时才比较.
    left_height, right_height = _field(left.height), _field(right.height)
    if left_height is not None and right_height is not None:
        if left_height != right_height:
            return MergeVerdict(False, f"身高不同 {left_height} / {right_height}")
        compared = True

    # 辅助条件: 双方都有的三围与罩杯须一致, 不一致即判为不同人.
    for label, a, b in (
        ("胸围", _field(left.bust), _field(right.bust)),
        ("腰围", _field(left.waist), _field(right.waist)),
        ("臀围", _field(left.hip), _field(right.hip)),
    ):
        if a is not None and b is not None:
            if a != b:
                return MergeVerdict(False, f"{label}不同 {a} / {b}")
            compared = True

    left_cup, right_cup = normalize_cup(left.cup), normalize_cup(right.cup)
    if left_cup is not None and right_cup is not None:
        if left_cup != right_cup:
            return MergeVerdict(False, f"罩杯不同 {left_cup} / {right_cup}")
        compared = True

    if not compared:
        # 一项可比的资料都没有: 只凭名字就合并会把重名的不同人并成一个.
        return MergeVerdict(False, "无可比的资料")
    return MergeVerdict(True, "名字有交集, 可比资料一致")


def needs(actor: Actor, actor_aliases: list[str], other: Actor, other_aliases: list[str]) -> bool:
    return compare(actor, actor_aliases, other, other_aliases).merge


__all__ = [
    "ActorAlias",
    "MergeVerdict",
    "compare",
    "needs",
    "normalize_birthday",
    "normalize_cup",
    "normalize_name",
]
