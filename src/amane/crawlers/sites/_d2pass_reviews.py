"""D2PASS 系站点 (10musume / 1pondo / pacopacomama) 的用户评论接口.

这几个站是同一套后台: 详情 JSON 与评论 JSON 都在 ``/dyn/phpauto/`` 下, 形状一致。
详情 JSON 里也有 ``AvgRating``, 但它与评论列表里的票不一定对得上 —— 实测 10musume 的
``details`` 给 4.6 而同一批 ``Rows`` 自算只有 4.4545, 评论接口自己的 ``AvgRating`` 才与
``Rows`` 自洽。因此评分以评论接口为准, 详情 JSON 那个只在评论接口不可用时兜底。
"""

from typing import Any

#: 评论接口; ``{}`` 处填番号 (站点内部编号, 如 ``030320_01``).
REVIEW_PATH = "/dyn/phpauto/movie_reviews/movie_id/{}.json"


def average_rating(value: Any) -> float | None:
    """把 ``AvgRating`` 收成两位小数; 0 或缺省表示无人评分, 返回 ``None``.

    无人评分时不能给 0: 界面上「没人评分」与「评了 0 分」是两回事, 前者不该占掉一档。
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return round(float(value), 2) if value > 0 else None


def rating_from_rows(rows: Any) -> float | None:
    """按评论列表里的 ``UserRating`` 自算平均; 没有可用票时返回 ``None``.

    评论接口的 ``AvgRating`` 已经是这个值, 这里自己再算一遍是为了在它缺失或与列表不一致
    时仍有一个自洽的结果 —— 评分必须能由列表复现, 否则界面上的星与下面的评论对不上。
    """
    if not isinstance(rows, list):
        return None
    votes: list[int] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            votes.append(int(row.get("UserRating")))
        except TypeError, ValueError:
            continue
    return round(sum(votes) / len(votes), 2) if votes else None
