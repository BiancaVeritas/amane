"""跨来源标签合并的规则测试.

规则: 按字段链顺序累加, 以**映射后的显示名**作等价类去重, 先到先得 —— 主来源的写法与顺序
得以保留, 后面各站只补主来源没有的标签; 显示名相同的后到条目直接丢弃.
"""

from collections import defaultdict

import pytest

from amane.aggregate import aggregate
from amane.crawlers.models import MediaMetadata, SearchQuery
from amane.enums import MetadataField
from tests.aggregate.test_engine import MockCrawler

#: 模拟映射表: 不同原文映射到同一中文名, 也有原文本身就是中文、与译文相同的情况.
_MAPPING = {
    "中出し": "中出",
    "中出": "中出",
    "騎乗位": "骑乘位",
    "骑乘位": "骑乘位",
    "潮吹き": "潮吹",
    "黒人": "黑人",
}


def _display(name: str) -> str:
    return _MAPPING.get(name, name)


def _priority(order: list[str]) -> defaultdict[MetadataField, list[str]]:
    """全字段统一按给定站点顺序; 这里只关心标签, 其余字段的链条不影响断言."""
    return defaultdict(lambda: list(order))


class TestTagMergeAcrossSources:
    """标签不像其它单源字段那样整份替换, 而是按显示名去重后累加."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("sites", "expected"),
        [
            # 映射后撞车的后到条目丢弃, 独有的补上.
            (
                [("a", ["中出し", "騎乗位"]), ("b", ["中出", "潮吹き"]), ("c", ["黒人"])],
                ["中出し", "騎乗位", "潮吹き", "黒人"],
            ),
            # 原文即中文且与主来源的译名相同: 跳过.
            ([("a", ["中出"]), ("b", ["中出し"])], ["中出"]),
            # 完全相同原文跳过.
            ([("a", ["69"]), ("b", ["69", "素人"])], ["69", "素人"]),
            # 未映射的原文按自身作等价类.
            ([("a", ["未知词"]), ("b", ["未知词", "别的"])], ["未知词", "别的"]),
            # 主来源没标签时由下一个非空来源接手.
            ([("a", []), ("b", ["潮吹き"]), ("c", ["潮吹"])], ["潮吹き"]),
        ],
    )
    async def test_merge_by_display_name(self, sites, expected: list[str]) -> None:
        crawlers = {name: MockCrawler(result=MediaMetadata(number="X", title="T", tags=tags)) for name, tags in sites}
        order = [name for name, _ in sites]

        result = await aggregate(
            SearchQuery("X"),
            crawlers,
            _priority(order),
            tag_display=_display,
        )

        assert result.metadata.tags == expected

    @pytest.mark.asyncio
    async def test_without_tag_display_keeps_single_source(self) -> None:
        """不传映射时保持旧行为: 取字段链首个非空来源的整份列表, 不跨站合并."""
        c1 = MockCrawler(result=MediaMetadata(number="X", title="T", tags=["A", "B"]))
        c2 = MockCrawler(result=MediaMetadata(number="X", title="T", tags=["C"]))

        result = await aggregate(SearchQuery("X"), {"a": c1, "b": c2}, _priority(["a", "b"]))

        assert result.metadata.tags == ["A", "B"]

    @pytest.mark.asyncio
    async def test_field_source_reports_primary(self) -> None:
        """合并后 tags 的来源仍是主来源: 它的写法与顺序决定了这份列表的开头."""
        c1 = MockCrawler(result=MediaMetadata(number="X", title="T", tags=["中出し"]))
        c2 = MockCrawler(result=MediaMetadata(number="X", title="T", tags=["潮吹き"]))

        result = await aggregate(
            SearchQuery("X"),
            {"a": c1, "b": c2},
            _priority(["a", "b"]),
            tag_display=_display,
        )

        assert result.field_sources["tags"] == "a"
