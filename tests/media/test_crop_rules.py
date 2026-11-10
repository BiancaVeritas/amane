"""精准裁切规则的匹配与裁切."""

import pytest
from PIL import Image

from amane.config import PosterCropRule
from amane.media.crop_rules import find_rule, rule_matches, rule_window


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


def test_rule_window_passes_through_when_aspect_disabled() -> None:
    """use_aspect 关闭时原样使用四个比例值."""
    rule = PosterCropRule(left=0.52, top=0.0, width=0.48, height=1.0)

    assert rule_window(rule, image_width=1280, image_height=707) == (0.52, 0.0, 0.48, 1.0)


@pytest.mark.parametrize(
    ("align", "expected_left"),
    [
        ("right", 0.5200),
        ("left", 0.0),
        ("center", 0.26),
    ],
)
def test_rule_window_aspect_alignment(align: str, expected_left: float) -> None:
    """宽高比 0.87 + 整高, 在 1.8116 的封面上宽度占比约 0.48."""
    rule = PosterCropRule(use_aspect=True, aspect=0.87, align=align, top=0.0, height=1.0)

    left, top, width, height = rule_window(rule, image_width=1280, image_height=707)

    assert width == pytest.approx(0.48, abs=0.01)
    assert left == pytest.approx(expected_left, abs=0.01)
    assert (top, height) == (0.0, 1.0)


def test_rule_window_aspect_stays_stable_across_resolutions() -> None:
    """同构图不同分辨率: 窗口占比几乎一致, 裁出来的宽高比都是 0.87."""
    rule = PosterCropRule(use_aspect=True, aspect=0.87, align="right", top=0.0, height=1.0)

    ratios = []
    for w, h in [(750, 414), (1280, 707)]:
        left, _, width, height = rule_window(rule, image_width=w, image_height=h)
        ratios.append((width * w) / (height * h))
        assert left + width == pytest.approx(1.0, abs=1e-9)

    assert ratios[0] == pytest.approx(0.87, abs=0.01)
    assert ratios[1] == pytest.approx(0.87, abs=0.01)


def test_rule_window_custom_align_uses_left() -> None:
    rule = PosterCropRule(use_aspect=True, aspect=0.87, align="custom", left=0.3, height=1.0)

    left, _, width, _ = rule_window(rule, image_width=1280, image_height=707)

    assert left == pytest.approx(0.3, abs=1e-6)
    assert left + width <= 1.0 + 1e-9


def test_rule_window_aspect_wider_than_cover_fills_width() -> None:
    """目标比例比封面本身还宽时宽度打满, 不能超过 1."""
    rule = PosterCropRule(use_aspect=True, aspect=3.0, align="left", height=1.0)

    _, _, width, _ = rule_window(rule, image_width=1000, image_height=1000)

    assert width <= 1.0


@pytest.mark.parametrize(
    ("prefix", "number", "expected"),
    [
        # 有分隔符: 只比切出来的那一段.
        ("MCB3DBD", "MCB3DBD-43", True),
        ("MCB3DBD", "mcb3dbd-43", True),
        ("ABP", "ABP-123", True),
        ("MCB3DBD", "CMC3DBD-43", False),
        ("MCB3DBD", "XMCB3DBD-1", False),
        ("MCB3DBD", "MCB3DBD2-1", False),
        ("MCB3DBD", "MCB-1", False),
        ("ABP", "ACBP-123", False),
        # 无分隔符: 退回「以…开头」.
        ("MCB3DBD", "MCB3DBD43", True),
        ("ABP", "ABP123", True),
        ("n", "n1138", True),
        ("MCB3DBD", "XMCB3DBD1", False),
        # 纯数字前缀没有意义.
        ("1234", "1234-ABP", False),
    ],
)
def test_number_prefix_requires_startswith(prefix: str, number: str, expected: bool):
    from amane.media.crop_rules import _number_prefix_matches

    assert _number_prefix_matches(prefix, number) is expected


def test_rule_with_digit_prefix_matches() -> None:
    """前缀含数字的规则要能命中 —— 这是实际踩过的坑."""
    rule = PosterCropRule(number_prefix="MCB3DBD")

    assert rule_matches(rule, number="MCB3DBD-43", series=None, studio=None, publisher=None)
    assert not rule_matches(rule, number="ABP-123", series=None, studio=None, publisher=None)
