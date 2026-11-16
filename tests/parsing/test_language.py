"""标题语种判定: 中文标题只走 CHINESE 路由, 日文标题不受影响."""

from __future__ import annotations

import pytest

from amane.parsing import has_kana, is_chinese_only


@pytest.mark.parametrize(
    ("text", "kana", "chinese_only"),
    [
        # 中文标题: 只有汉字.
        ("【饥渴的巨乳人妻】 背着老公偷情", False, True),
        ("糖心VlogUp主【小痛仰】黑丝淫穴上门家政女仆", False, True),
        # 日文标题: 含假名, 因此不算中文.
        ("ムラムラってくる素人 テスト", True, False),
        ("撮りおろし徹底陵辱ビデオ", True, False),
        ("スカイエンジェル ブルー", True, False),
        # 汉字夹假名 (中日混合): 按日文处理, 宁可多问几个站也不误伤.
        ("这是日本 タイトル 混合", True, False),
        # 纯英文与番号: 既非中文也非日文, 保持原路由.
        ("MIDV-123", False, False),
        ("n0888", False, False),
        ("Tokyo Hot", False, False),
        # 空串.
        ("", False, False),
    ],
)
def test_language_detection(text: str, kana: bool, chinese_only: bool) -> None:
    assert has_kana(text) is kana
    assert is_chinese_only(text) is chinese_only


@pytest.mark.parametrize(
    "text",
    [
        # 这些日文标题都含假名, 判据必须一条都不误伤.
        "美乳OL 傲慢な女上司",
        "巨乳人妻 背徳の情事",
        "南野あかり 東熱発狂汁",
        "スカイエンジェル ブルー",
    ],
)
def test_japanese_titles_never_chinese(text: str) -> None:
    assert is_chinese_only(text) is False
