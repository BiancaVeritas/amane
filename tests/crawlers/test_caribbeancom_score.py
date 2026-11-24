"""加勒比评分: 按每条用户点评的星数取平均.

页面上的评分有三个容易混淆的位置:

- ``li.movie-spec`` 里的「ユーザー評価」—— 恒为五颗星, 是模板占位, 不是数据;
- 投稿表单里的 ``.post-info__rating label`` —— 点击控件;
- 点评区的 ``<div class="rating" itemparop="reviewRating">`` —— 真实点评的星数, 各条不同.

只有第三处可用. 一条点评都没有时不给 0 分: 「没人评分」与「评了 0 分」不是一回事.
"""

import pytest

from amane.crawlers.sites.caribbeancom import _score


def _review_block(stars: str) -> str:
    return (
        f'<div class="section is-dense">'
        f'<div class="rating" itemparop="reviewRating" itemscope '
        f'itemtype="http://schema.org/Rating">{stars}</div>'
        f'<div class="review-comment">コメント</div>'
        f'<div class="review-info"><span class="review-info__user">by u</span>'
        f'<span class="review-info__date">2017-02-16 16:42:43</span></div>'
        f"</div>"
    )


#: 与详情页同形的总评占位, 必须被忽略.
_TEMPLATE_SPEC = (
    '<li class="movie-spec"><span class="spec-title">ユーザー評価</span>'
    '<span class="spec-content rating meta-rating">★★★★★</span></li>'
)
#: 投稿表单里的星级点击控件, 也必须被忽略.
_FORM_RATING = (
    '<div class="post-info__rating">' + "".join(f'<label for="r0{i}">★</label>' for i in range(1, 6)) + "</div>"
)


@pytest.mark.parametrize(
    ("stars", "expected"),
    [
        (["★★★★★", "★★★★★"], 5.0),
        (["★★★★★", "★★★★"], 4.5),
        (["★★★★★", "★★★★", "★★★"], 4.0),
        # 末位四舍五入到两位.
        (["★★★★★", "★★★★", "★★★", "★★★"], 3.75),
        (["★"], 1.0),
    ],
)
def test_score_averages_review_stars(stars: list[str], expected: float) -> None:
    html = _TEMPLATE_SPEC + _FORM_RATING + "".join(_review_block(s) for s in stars)
    assert _score(html) == expected


def test_score_ignores_template_and_form() -> None:
    """只有模板总评与表单控件时没有评分, 不能把它们当成五颗星的数据."""
    assert _score(_TEMPLATE_SPEC + _FORM_RATING) is None


def test_score_none_without_reviews() -> None:
    """没点评 -> 无评分, 而不是 0 分."""
    assert _score("<html><body></body></html>") is None


def test_score_skips_review_without_stars() -> None:
    """有 review 区但没给星的条目 (如 download-message) 不参与平均."""
    html = '<div class="section is-dense"><div class="download-message"></div></div>' + _review_block("★★★★")
    assert _score(html) == 4.0
