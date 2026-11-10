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


def _number_prefix_of(number: str | None) -> str:
    """番号的前缀段 (``ABP-123`` → ``ABP``); 取不到字母段时返回空串."""
    raw = (number or "").strip()
    match = re.match(r"^([A-Za-z]+)", raw)
    return match.group(1).casefold() if match else ""


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
    conditions = [
        (rule.series, _normalize(series)),
        (rule.number_prefix, _number_prefix_of(number)),
        (rule.studio, _normalize(studio)),
        (rule.publisher, _normalize(publisher)),
    ]
    checked = [(expect, actual) for expect, actual in conditions if _normalize(expect)]
    if not all(_normalize(expect) == actual for expect, actual in checked):
        return False

    keyword = _normalize(rule.filename_keyword)
    if keyword and keyword not in _searchable_text(media_path, number):
        return False

    # 至少填了一项, 否则视为未启用.
    return bool(checked) or bool(keyword)


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
