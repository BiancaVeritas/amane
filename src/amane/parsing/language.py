"""标题语种判定.

用途只有一个: 决定一个搜索词该问哪些来源. 中文标题拿去问有码站必然 404/403, 而每个失败
还要重试两三次 —— 一次预览十几秒里有十秒耗在这上面.

判据刻意只用一条: **出现假名即日文**. 日文标题一定含假名 (助词、送假名), 而中文标题不含;
汉字单独出现无法区分中日, 所以不据此下结论, 保持原有行为.
"""

from __future__ import annotations

import re

#: 平假名 + 片假名 + 半角片假名.
_KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff\uff66-\uff9f]")

#: 汉字 (CJK 统一表意文字).
_HAN = re.compile(r"[\u4e00-\u9fff]")


def has_kana(text: str) -> bool:
    """含假名: 可判为日文."""
    return bool(_KANA.search(text))


def is_chinese_only(text: str) -> bool:
    """只有汉字、没有假名: 判为中文标题.

    纯英文与番号既无汉字也无假名, 返回 False —— 它们本就该走原来的路由.
    """
    return bool(_HAN.search(text)) and not _KANA.search(text)
