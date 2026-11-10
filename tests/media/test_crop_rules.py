"""精准裁切规则的匹配与裁切."""

import pytest
from PIL import Image

from amane.config import PosterCropRule
from amane.media.crop_rules import find_rule, rule_matches


def test_rule_matches_single_condition() -> None:
    rule = PosterCropRule(series="プレステージ")

    assert rule_matches(rule, number="ABP-123", series="プレステージ", studio=None, publisher=None)
    assert not rule_matches(rule, number="ABP-123", series="其他系列", studio=None, publisher=None)


def test_rule_requires_all_filled_conditions() -> None:
    """填了多项时按 AND 收窄; 只满足一项不该命中."""
    rule = PosterCropRule(number_prefix="ABP", studio="Prestige")

    assert rule_matches(rule, number="ABP-123", series=None, studio="Prestige", publisher=None)
    # 前缀对上但供应商不对: 不命中.
    assert not rule_matches(rule, number="ABP-123", series=None, studio="其他", publisher=None)


def test_empty_rule_never_matches() -> None:
    """全空的规则等于「全都匹配」, 会盖掉人脸检测却毫无依据, 必须不命中."""
    rule = PosterCropRule()

    assert not rule_matches(rule, number="ABP-123", series="任意", studio="任意", publisher="任意")


def test_number_prefix_ignores_case_and_suffix() -> None:
    rule = PosterCropRule(number_prefix="abp")

    assert rule_matches(rule, number="ABP-123", series=None, studio=None, publisher=None)
    assert not rule_matches(rule, number="SSIS-001", series=None, studio=None, publisher=None)


def test_find_rule_returns_first_match_in_order() -> None:
    first = PosterCropRule(number_prefix="ABP", left=0.6)
    second = PosterCropRule(number_prefix="ABP", left=0.2)
    rules = [first, second]

    found = find_rule(rules, number="ABP-123", series=None, studio=None, publisher=None)

    assert found is first


def test_find_rule_returns_none_without_match() -> None:
    rules = [PosterCropRule(series="甲")]

    assert find_rule(rules, number="ABP-1", series="乙", studio=None, publisher=None) is None


@pytest.mark.parametrize(
    ("box", "expected_size"),
    [
        ((0.0, 0.0, 0.5, 1.0), (50, 100)),  # 左半
        ((0.5, 0.0, 0.5, 1.0), (50, 100)),  # 右半
        ((0.0, 0.0, 1.0, 0.5), (100, 50)),  # 上半
    ],
)
def test_crop_poster_by_rule_uses_ratio_box(tmp_path, box: tuple[float, float, float, float], expected_size):
    from amane.media.images import crop_poster_by_rule

    src = tmp_path / "src.jpg"
    Image.new("RGB", (100, 100), "red").save(src)
    dest = tmp_path / "dest.jpg"

    left, top, width, height = box
    assert crop_poster_by_rule(src, dest, left=left, top=top, width=width, height=height)
    with Image.open(dest) as out:
        assert out.size == expected_size


def test_crop_poster_by_rule_rejects_empty_box(tmp_path) -> None:
    from amane.media.images import crop_poster_by_rule

    src = tmp_path / "src.jpg"
    Image.new("RGB", (100, 100), "red").save(src)

    assert not crop_poster_by_rule(src, tmp_path / "dest.jpg", left=0.5, top=0.0, width=0.0, height=1.0)


def test_filename_keyword_matches_filename() -> None:
    rule = PosterCropRule(filename_keyword="BD")

    assert rule_matches(
        rule, number="ABP-123", series=None, studio=None, publisher=None, media_path=r"D:\片\ABP-123-BD.mp4"
    )
    assert not rule_matches(
        rule, number="ABP-123", series=None, studio=None, publisher=None, media_path=r"D:\片\ABP-123.mp4"
    )


def test_filename_keyword_falls_back_to_number() -> None:
    """没有文件名时改看番号 —— 实际文件名基本就是番号, 这样判与看文件名几乎一致."""
    rule = PosterCropRule(filename_keyword="n1")

    assert rule_matches(rule, number="n1234", series=None, studio=None, publisher=None)
    assert not rule_matches(rule, number="ABP-123", series=None, studio=None, publisher=None)


def test_filename_keyword_ignores_directory_segments() -> None:
    """库目录常带 BD/4K 之类的命名, 不该让规则误命中."""
    rule = PosterCropRule(filename_keyword="BD")

    assert not rule_matches(
        rule,
        number="ABP-123",
        series=None,
        studio=None,
        publisher=None,
        media_path=r"D:\BD 合集\ABP-123.mp4",
    )


def test_filename_keyword_combines_with_other_conditions() -> None:
    rule = PosterCropRule(number_prefix="ABP", filename_keyword="BD")

    assert rule_matches(rule, number="ABP-123", series=None, studio=None, publisher=None, media_path="ABP-123-BD.mp4")
    assert not rule_matches(
        rule, number="SSIS-001", series=None, studio=None, publisher=None, media_path="SSIS-001-BD.mp4"
    )
