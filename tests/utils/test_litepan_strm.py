"""LitePan STRM 中 115 文件身份的解析测试。"""

import base64
from pathlib import Path

from amane.utils.litepan_strm import parse_litepan_115_file_id, read_litepan_115_file_id


def _encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def test_parse_litepan_play_url_ignores_access_key_and_signature():
    file_id = "3500105553135273569"
    url = f"http://nas.example/api/strm/play/{_encoded(file_id)}?t=NOT_A_FILE_ID&sign=signature"
    assert parse_litepan_115_file_id(url) == file_id


def test_reject_non_litepan_strm_urls():
    assert parse_litepan_115_file_id("https://example.com/t/3500105553135273569") is None
    assert parse_litepan_115_file_id("https://example.com/api/strm/play/not-base64!") is None


def test_reject_non_numeric_decoded_value():
    assert parse_litepan_115_file_id(f"https://example.com/api/strm/play/{_encoded('not-a-file-id')}") is None


def test_read_strm_file(tmp_path: Path):
    file_id = "3500105553135273569"
    path = tmp_path / "sample.strm"
    path.write_text(f"https://example.com/api/strm/play/{_encoded(file_id)}\n", encoding="utf-8")
    assert read_litepan_115_file_id.sync(path) == file_id


def test_non_strm_file_is_ignored(tmp_path: Path):
    path = tmp_path / "sample.mp4"
    path.write_text("https://example.com/api/strm/play/MTIz", encoding="utf-8")
    assert read_litepan_115_file_id.sync(path) is None
