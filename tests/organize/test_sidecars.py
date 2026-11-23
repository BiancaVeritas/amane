"""附属文件 (`.bif` 等) 的发现与搬运: 名字里的参数后缀必须原样保留."""

from pathlib import Path

import pytest

from amane.enums import MoveMode
from amane.organize.subtitles import discover_sidecars, place_sidecars

# (视频名, 同目录文件, 期望被认作附属文件, 说明)
DISCOVER_CASES = [
    ("abc.mp4", "abc-320-10.bif", True, "标准后缀"),
    ("abc.mp4", "abc-480-5.bif", True, "不同的图大小与间隔"),
    ("abc.mp4", "abc.bif", True, "无参数后缀"),
    ("ABC-123.mp4", "ABC-123-320-10.bif", True, "番号形态"),
    ("abc.mp4", "xyz-320-10.bif", False, "别的视频的附属文件"),
    ("abc.mp4", "abcd-320-10.bif", False, "同前缀但是另一个视频"),
    ("abc.mp4", "abc-320-10.srt", False, "扩展名不在列表"),
    ("abc.mp4", "abc.mp4", False, "视频本身"),
]


@pytest.mark.parametrize(("video", "sibling", "expected", "note"), DISCOVER_CASES)
def test_discover_sidecars(tmp_path: Path, video: str, sibling: str, expected: bool, note: str):
    (tmp_path / video).write_bytes(b"v")
    (tmp_path / sibling).write_bytes(b"s")
    found = discover_sidecars.sync(tmp_path / video, [".bif"])
    assert (tmp_path / sibling) in found if expected else (tmp_path / sibling) not in found, note


def test_discover_sidecars_empty_extensions(tmp_path: Path):
    """空扩展名列表关闭发现."""
    (tmp_path / "abc.mp4").write_bytes(b"v")
    (tmp_path / "abc-320-10.bif").write_bytes(b"s")
    assert discover_sidecars.sync(tmp_path / "abc.mp4", []) == []


# (源附属文件名, 源视频主名, 新视频主名, 期望目标名)
PLACE_CASES = [
    ("abc-320-10.bif", "abc", "[演员] XYZ", "[演员] XYZ-320-10.bif"),
    ("abc-480-5.bif", "abc", "[演员] XYZ", "[演员] XYZ-480-5.bif"),
    ("abc.bif", "abc", "[演员] XYZ", "[演员] XYZ.bif"),
]


@pytest.mark.parametrize(("sibling", "source_stem", "video_stem", "expected"), PLACE_CASES)
def test_place_sidecars_keeps_parameters(
    tmp_path: Path, sibling: str, source_stem: str, video_stem: str, expected: str
):
    """视频改名时, 附属文件只换主名, `-320-10` 这类参数原封不动."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    source = src_dir / sibling
    source.write_bytes(b"data")
    video_dest = dest_dir / f"{video_stem}.mp4"
    video_dest.write_bytes(b"v")

    place_sidecars.sync([source], video_dest, MoveMode.MOVE, video_source_stem=source_stem)

    assert (dest_dir / expected).exists()
    assert (dest_dir / expected).read_bytes() == b"data"
    assert not source.exists(), "MOVE 模式应移除源文件"


def test_place_sidecars_unchanged_name(tmp_path: Path):
    """视频不改名时, 附属文件名一个字都不变."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    source = src_dir / "ABC-123-320-10.bif"
    source.write_bytes(b"data")
    video_dest = dest_dir / "ABC-123.mp4"
    video_dest.write_bytes(b"v")

    place_sidecars.sync([source], video_dest, MoveMode.MOVE, video_source_stem="ABC-123")

    assert (dest_dir / "ABC-123-320-10.bif").exists()
