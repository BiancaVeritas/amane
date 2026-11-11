"""多盘片只刮 CD1 的选择规则."""

from __future__ import annotations

import pytest

from amane.handlers.refresh import _scrape_targets


class _FakeMediaFile:
    """只需要 ``path`` 让 ``parse_file_info`` 取番号与分集."""

    def __init__(self, path: str) -> None:
        self.path = path


def _names(files: list[_FakeMediaFile]) -> list[str]:
    return [f.path.rsplit("/", 1)[-1] for f in files]


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        # 标准多盘: 只留 CD1.
        (["/x/SKYHD-104-CD1.strm", "/x/SKYHD-104-CD2.strm", "/x/SKYHD-104-CD3.strm"], ["SKYHD-104-CD1.strm"]),
        # 缺首盘: 整组不刮, 免得按 CD2 的数据落库.
        (["/x/ABC-100-CD2.strm", "/x/ABC-100-CD3.strm"], []),
        # 没有分集标记: 原样保留.
        (["/x/DEF-200.strm"], ["DEF-200.strm"]),
        # 混合: 有 CD1 的取 CD1, 单文件照留, 缺 CD1 的整组丢掉.
        (
            [
                "/x/GHI-300-CD1.strm",
                "/x/GHI-300-CD2.strm",
                "/x/JKL-400.strm",
                "/x/MNO-500-CD2.strm",
            ],
            ["JKL-400.strm", "GHI-300-CD1.strm"],
        ),
        # 空输入.
        ([], []),
    ],
)
def test_scrape_targets_keeps_only_cd1(paths: list[str], expected: list[str]) -> None:
    files = [_FakeMediaFile(p) for p in paths]

    picked = _scrape_targets(files)  # type: ignore[arg-type]

    assert sorted(_names(picked)) == sorted(expected)


def test_scrape_targets_treats_distinct_numbers_separately() -> None:
    """分组只看番号: 不同番号的 CD1 各自入选, 不会因为都有 CD1 就互相顶掉."""
    files = [
        _FakeMediaFile("/x/SKYHD-104-CD1.strm"),
        _FakeMediaFile("/x/SKYHD-104-CD2.strm"),
        _FakeMediaFile("/x/HEYZO-0721-CD1.strm"),
        _FakeMediaFile("/x/HEYZO-0721-CD2.strm"),
    ]

    picked = _scrape_targets(files)  # type: ignore[arg-type]

    assert sorted(_names(picked)) == ["HEYZO-0721-CD1.strm", "SKYHD-104-CD1.strm"]
