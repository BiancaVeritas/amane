"""演员实体自动合并判定.

刮削会把同一个人的不同艺名建成多个 ``Actor``。判定合并需要两道闸门:

1. **名字有交集** —— 展示名或任一侧别名命中对方任一名字. 名字只是"疑似", 不能单独作为依据:
   日文艺名重名常见, 「あかり」可能是多人.
2. **身体数据一致** —— ``birthday`` 与 ``height`` 必须都存在且完全相等. 这是判据主体:
   同一个人换艺名不会换生日与身高, 而重名的两个人几乎不可能两者都一样.
   ``bust`` / ``waist`` / ``hip`` 与 ``cup`` 在双方都有值时也须相等, 用来挡住生日身高巧合
   相同的极少数情况.

任一项存在但不等 → 不合并. 数据缺失的字段不参与比较 (缺生日的人仍可与另一位缺生日的比对,
但生日+身高必须都在才可能合并).
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
    return value if value else None


def compare(
    left: Actor,
    left_aliases: list[str],
    right: Actor,
    right_aliases: list[str],
) -> MergeVerdict:
    """判定两位演员是否为同一人."""
    left_names = _names_of(left, left_aliases)
    right_names = _names_of(right, right_aliases)
    if not (left_names & right_names):
        return MergeVerdict(False, "名字无交集")

    # 硬条件: 生日与身高都必须在, 且完全相等.
    left_birthday, right_birthday = normalize_birthday(left.birthday), normalize_birthday(right.birthday)
    if left_birthday is None or right_birthday is None:
        return MergeVerdict(False, "缺少生日")
    if left_birthday != right_birthday:
        return MergeVerdict(False, f"生日不同 {left_birthday} / {right_birthday}")

    left_height, right_height = _field(left.height), _field(right.height)
    if left_height is None or right_height is None:
        return MergeVerdict(False, "缺少身高")
    if left_height != right_height:
        return MergeVerdict(False, f"身高不同 {left_height} / {right_height}")

    # 辅助条件: 双方都有的三围与罩杯须一致, 不一致即判为不同人.
    for label, a, b in (
        ("胸围", _field(left.bust), _field(right.bust)),
        ("腰围", _field(left.waist), _field(right.waist)),
        ("臀围", _field(left.hip), _field(right.hip)),
    ):
        if a is not None and b is not None and a != b:
            return MergeVerdict(False, f"{label}不同 {a} / {b}")

    left_cup, right_cup = normalize_cup(left.cup), normalize_cup(right.cup)
    if left_cup is not None and right_cup is not None and left_cup != right_cup:
        return MergeVerdict(False, f"罩杯不同 {left_cup} / {right_cup}")

    return MergeVerdict(True, "生日身高一致, 名字有交集")


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
