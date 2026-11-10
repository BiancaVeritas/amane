"""按系列 / 番号前缀 / 文件名关键词 / 供应商 / 发行商匹配固定裁切规则.

命中即优先于人脸检测: 同一系列的封面构图一致, 用人眼标定过的窗口位置比逐张检测更准,
也不会因为某张图恰好没检出脸而退回「固定右侧」.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import PosterCropRule


def _normalize(value: str | None) -> str:
    return (value or "").strip().casefold()


def _number_prefix_matches(expect: str, number: str | None) -> bool:
    """番号前缀是否命中 (忽略大小写).

    番号带分隔符时按分隔符切段比相等: ``ABP-123`` 的前缀是 ``ABP``, 于是 ``ABP`` 命中而
    ``ABPX`` 不命中 —— 这是最贴合「番号前缀」本义的判法.

    番号不带分隔符时无从切分 (``MCB3DBD43`` 整串是一个词), 退回「以…开头」: 只有这样
    ``MCB3DBD`` 才能命中。代价是 ``MCB3DBD2`` 这类超集前缀也会命中, 属可接受的取舍 ——
    真实番号里同名加数字的独立厂商极少.

    前缀本身要求至少含一个字母, 纯数字前缀交给别的条件判.
    """
    want = _normalize(expect)
    raw = _normalize(number)
    if not want or not raw:
        return False
    if not any(ch.isalpha() for ch in want):
        return False

    head = re.split(r"[-_. ]+", raw, maxsplit=1)[0]
    if head != raw:
        # 有分隔符: 只认切出来的那一段.
        return head == want
    # 无分隔符: 只能按开头比.
    return raw.startswith(want)


def _searchable_text(media_path: str | None, number: str | None) -> str:
    """关键词比对用的文本: 优先文件名, 没有就用番号.

    实际使用中文件名基本就是番号加些标记 (``ABP-123-BD.mp4``), 所以拿不到文件名时退到
    番号判断, 结果与看文件名几乎一致; 比直接判不命中更符合预期.
    """
    if media_path:
        # 只取文件名本身: 目录名里常带「BD」「4K」之类的库命名, 不参与判断.
        name = re.split(r"[\\/]", media_path)[-1]
        if name:
            return name.casefold()
    return (number or "").casefold()


def rule_matches(
    rule: PosterCropRule,
    *,
    number: str | None,
    series: str | None,
    studio: str | None,
    publisher: str | None,
    media_path: str | None = None,
) -> bool:
    """规则是否命中; 填了多项时**全部**满足才算命中 (AND, 便于收窄范围).

    全空的规则不命中: 那种规则等于「全都匹配」, 会盖掉人脸检测却毫无依据.
    """
    # 番号前缀按「以…开头」比对, 其余条件按等值比对.
    conditions = [
        (rule.series, _normalize(series)),
        (rule.studio, _normalize(studio)),
        (rule.publisher, _normalize(publisher)),
    ]
    checked = [(expect, actual) for expect, actual in conditions if _normalize(expect)]
    if not all(_normalize(expect) == actual for expect, actual in checked):
        return False

    prefix = _normalize(rule.number_prefix)
    if prefix and not _number_prefix_matches(rule.number_prefix, number):
        return False

    keyword = _normalize(rule.filename_keyword)
    if keyword and keyword not in _searchable_text(media_path, number):
        return False

    # 至少填了一项, 否则视为未启用.
    return bool(checked) or bool(prefix) or bool(keyword)


def find_rule(
    rules: list[PosterCropRule],
    *,
    number: str | None,
    series: str | None,
    studio: str | None,
    publisher: str | None,
    media_path: str | None = None,
) -> PosterCropRule | None:
    """按配置顺序取第一条命中的规则; 没有则 None."""
    for rule in rules:
        if rule_matches(
            rule,
            number=number,
            series=series,
            studio=studio,
            publisher=publisher,
            media_path=media_path,
        ):
            return rule
    return None


def rule_window(rule: PosterCropRule, *, image_width: int, image_height: int) -> tuple[float, float, float, float]:
    """规则 → 实际裁切窗口 (left/top/width/height, 都是占封面宽高的 0~1 值).

    ``use_aspect`` 关闭时直接用规则里存的四个比例. 开启时按目标宽高比换算: 高度取满,
    宽度 = 宽高比 / 封面宽高比, 再按 ``align`` 决定水平位置 —— 这样封面换分辨率也不会让
    窗口形状漂移, 而直接存比例时窗口的宽高比会随封面宽高比变化.
    """
    if not rule.use_aspect:
        return rule.left, rule.top, rule.width, rule.height

    top = rule.top
    height = rule.height
    image_ratio = image_width / image_height if image_height else 1.0
    # 窗口宽高比 = (width × 图宽) / (height × 图高); 解出占图宽的 width.
    width = (rule.aspect / image_ratio) * height
    if width >= 1.0:
        # 目标比例比封面本身还宽: 宽度打满, 高度相应回落.
        width = 1.0
        height = min(height, (image_ratio / rule.aspect) if rule.aspect else height)
    width = max(0.0001, min(width, 1.0))

    if rule.align == "left":
        left = 0.0
    elif rule.align == "center":
        left = (1.0 - width) / 2
    elif rule.align == "right":
        left = 1.0 - width
    else:
        left = max(0.0, min(rule.left, 1.0 - width))
    return left, top, width, height
