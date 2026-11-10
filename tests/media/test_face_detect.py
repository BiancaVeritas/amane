"""主体脸评分: 面积大的侧脸不该压过面积小但端正的正脸."""


def test_subject_score_prefers_frontal_over_larger_profile() -> None:
    """侧脸框又高又窄, 面积往往比正脸大; 只看面积会选错人.

    取自真实封面: 男性侧脸 (108x188, score 0.70) 面积是女主角正脸 (78x80, score 0.84)
    的三倍, 但构图主体是后者.
    """
    from amane.media.face_detect import _subject_score

    profile = (657.0, 65.0, 755.0, 253.0, 0.70)  # 侧脸, 宽高比 0.52
    frontal = (447.0, 52.0, 525.0, 132.0, 0.84)  # 正脸, 宽高比 0.97

    assert (profile[2] - profile[0]) * (profile[3] - profile[1]) > (
        (frontal[2] - frontal[0]) * (frontal[3] - frontal[1])
    )
    assert _subject_score(frontal) > _subject_score(profile)


def test_subject_score_rejects_degenerate_box() -> None:
    from amane.media.face_detect import _subject_score

    assert _subject_score((10.0, 10.0, 10.0, 50.0, 0.9)) == 0.0
    assert _subject_score((10.0, 10.0, 50.0, 10.0, 0.9)) == 0.0
