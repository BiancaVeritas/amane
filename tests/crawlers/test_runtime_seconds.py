"""MediaMetadata 的时长口径: 秒是精确值, 分钟由它向上取整."""

import pytest

from amane.crawlers.models import MediaMetadata


@pytest.mark.parametrize(
    ("seconds", "minutes"),
    [
        # 整分钟不进位: NFO 里不会凭空多一分钟.
        (3600, 60),
        (5760, 96),
        (0, None),
        # 有余秒就进 1.
        (1, 1),
        (3601, 61),
        (5761, 97),
        (6553, 110),
        (5800, 97),
    ],
)
def test_seconds_drive_minutes(seconds: int, minutes: int | None) -> None:
    meta = MediaMetadata(number="X-1", runtime_seconds=seconds)
    assert meta.runtime_seconds == (seconds or None)
    assert meta.runtime == minutes


@pytest.mark.parametrize(
    ("minutes", "seconds"),
    [
        (96, 96 * 60),
        (1, 60),
        (139, 139 * 60),
    ],
)
def test_minutes_only_sites_gain_seconds(minutes: int, seconds: int) -> None:
    """只提供分钟的站点也要留下秒数, 免得数据里缺这一项."""
    meta = MediaMetadata(number="X-2", runtime=minutes)
    assert meta.runtime == minutes
    assert meta.runtime_seconds == seconds


def test_no_duration_stays_empty() -> None:
    meta = MediaMetadata(number="X-3")
    assert meta.runtime is None
    assert meta.runtime_seconds is None


def test_seconds_win_over_minutes() -> None:
    """两个都给时以秒为准: 分钟可能来自旧缓存, 秒才是站点原值."""
    meta = MediaMetadata(number="X-4", runtime=10, runtime_seconds=6553)
    assert meta.runtime_seconds == 6553
    assert meta.runtime == 110
