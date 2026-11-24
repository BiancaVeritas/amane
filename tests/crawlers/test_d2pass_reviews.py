"""D2PASS 系站点的评分口径: 以评论列表为准, 详情 JSON 只作兜底.

实测背景 (2026-11): 同一部 10musume 片, ``movie_details`` 的 ``AvgRating`` 是 4.6, 而
``movie_reviews`` 的 11 条 ``UserRating`` 自算是 4.4545 —— 评论接口自己的 ``AvgRating``
与列表一致。界面上星与下面的评论必须能对上, 所以取评论列表那一路。
"""

import pytest

from amane.crawlers.sites._d2pass_reviews import average_rating, rating_from_rows


class TestAverageRating:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (4.4545454545455, 4.45),
            (3.8333333333333, 3.83),
            (4.6, 4.6),
            (5.0, 5.0),
            # 无人评分时站点写 0: 不给 0 分, 否则界面上会显示成「评了 0 分」.
            (0, None),
            (0.0, None),
            (None, None),
            ("4.5", None),
            (True, None),
        ],
    )
    def test_average_rating(self, raw, expected: float | None) -> None:
        assert average_rating(raw) == expected


class TestRatingFromRows:
    @pytest.mark.parametrize(
        ("rows", "expected"),
        [
            # 一本道 022815_036 的真实分布: 18 票合计 69.
            ([{"UserRating": str(v)} for v in [1, 3, 3, 3, 3, 4, 4, 4, 4, 4, 4, 4, 4, 4, 5, 5, 5, 5]], 3.83),
            ([{"UserRating": "5"}, {"UserRating": "4"}], 4.5),
            ([{"UserRating": "5"}], 5.0),
            # 缺字段 / 非法值的条目跳过, 不让它们把平均拉低.
            ([{"UserRating": "5"}, {}, {"UserRating": None}, {"UserRating": "x"}, {"UserRating": "3"}], 4.0),
            ([], None),
            (None, None),
            ("not a list", None),
        ],
    )
    def test_rating_from_rows(self, rows, expected: float | None) -> None:
        assert rating_from_rows(rows) == expected
