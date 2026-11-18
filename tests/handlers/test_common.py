"""tests for amane.handlers._common -- handler 间共享单元."""

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from amane.db import MediaFileStatus
from amane.db.models import MediaIdentityType
from amane.handlers._common import (
    ensure_oshash,
    finalize_media_file,
    refresh_external_subtitle,
    refresh_media_association,
    register_media_file,
    scan_library,
)
from amane.library import LibraryFileKind, LibraryHit, LibraryScan
from amane.parsing import ContentType

if TYPE_CHECKING:
    from amane.db.repository import Repository


def _names(hits: list[LibraryHit], kind: LibraryFileKind) -> set[str]:
    return {h.path.name for h in hits if h.kind is kind}


class TestScanLibrary:
    """扫库遍历 + 分类."""

    @pytest.fixture
    def tree(self, tmp_path: Path) -> Path:
        (tmp_path / "a.mp4").touch()
        (tmp_path / "b.txt").touch()
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "c.mkv").touch()
        (sub / "d.nfo").touch()
        deep = sub / "deep"
        deep.mkdir()
        (deep / "e.avi").touch()
        return tmp_path

    def test_recursive_media(self, tree: Path):
        hits = scan_library.sync(tree, recursive=True, scan=LibraryScan())
        assert _names(hits, LibraryFileKind.MEDIA) == {"a.mp4", "c.mkv", "e.avi"}
        assert _names(hits, LibraryFileKind.SKIP) == set()
        assert _names(hits, LibraryFileKind.UNWANTED) == set()

    def test_non_recursive_top_level(self, tree: Path):
        hits = scan_library.sync(tree, recursive=False, scan=LibraryScan())
        assert _names(hits, LibraryFileKind.MEDIA) == {"a.mp4"}

    def test_patterns_and_blacklist(self, tree: Path):
        (tree / "广告.html").touch()
        (tree / "trailer.mp4").touch()
        hits = scan_library.sync(
            tree,
            recursive=True,
            scan=LibraryScan(patterns=["*.txt", "*.nfo"], trailer_pattern="(?i)trailer", blacklist_patterns=["广告"]),
        )
        assert _names(hits, LibraryFileKind.MEDIA) == {"b.txt", "d.nfo"}
        assert _names(hits, LibraryFileKind.SKIP) == {"trailer.mp4"}
        assert _names(hits, LibraryFileKind.UNWANTED) == {"广告.html"}

    def test_empty_dir(self, tmp_path: Path):
        assert scan_library.sync(tmp_path, recursive=True, scan=LibraryScan()) == []

    def test_directories_and_trash_omitted(self, tree: Path):
        (tree / "fake.mp4").mkdir()
        trash = tree / ".amane_trash"
        trash.mkdir()
        (trash / "ad.mp4").touch()
        hits = scan_library.sync(tree, recursive=True, scan=LibraryScan())
        assert all(h.path.is_file() for h in hits)
        assert {h.path.name for h in hits} == {"a.mp4", "c.mkv", "e.avi"}

    def test_min_file_size(self, tree: Path):
        (tree / "a.mp4").write_bytes(b"x" * 10)
        (tree / "sub" / "c.mkv").write_bytes(b"x" * 100)
        (tree / "sub" / "deep" / "e.avi").write_bytes(b"x" * 100)
        hits = scan_library.sync(tree, recursive=True, scan=LibraryScan(min_file_size=50))
        assert _names(hits, LibraryFileKind.MEDIA) == {"c.mkv", "e.avi"}
        assert _names(hits, LibraryFileKind.UNWANTED) == {"a.mp4"}

    @pytest.mark.asyncio
    async def test_in_thread_matches_sync(self, tree: Path):
        scan = LibraryScan()
        assert await scan_library(tree, recursive=True, scan=scan) == scan_library.sync(tree, recursive=True, scan=scan)


class TestEnsureOshash:
    """按需计算指纹; 已有值不重算, 失败留 None."""

    @pytest.mark.asyncio
    async def test_computes_and_persists(self, repo: Repository, tmp_path):
        video = tmp_path / "MIDV-123.mkv"
        video.write_bytes(bytes(range(256)) * (65536 * 2 // 256))
        media = await repo.create_media_file(library_id=1, path=str(video))
        assert media.id is not None
        assert await ensure_oshash(repo, media) == "a0601fdf9f610000"
        stored = await repo.get_media_file(media.id)
        assert stored is not None
        assert stored.oshash == "a0601fdf9f610000"

    @pytest.mark.asyncio
    async def test_reuses_existing(self, repo: Repository):
        media = await repo.create_media_file(library_id=1, path="/nope.mp4")
        assert media.id is not None
        await repo.update_media_file(media.id, oshash="already")
        media = await repo.get_media_file(media.id)
        assert media is not None
        assert await ensure_oshash(repo, media) == "already"

    @pytest.mark.asyncio
    async def test_strm_never_computes_oshash(self, repo: Repository, tmp_path):
        strm = tmp_path / "Lancelot-styles-evolution.26.05.13.strm"
        strm.write_text("https://example.invalid/strm-placeholder", encoding="utf-8")
        media = await repo.create_media_file(library_id=1, path=str(strm))
        assert media.id is not None
        assert await ensure_oshash(repo, media) is None
        stored = await repo.get_media_file(media.id)
        assert stored is not None
        assert stored.oshash is None

    @pytest.mark.asyncio
    async def test_zero_byte_file_stays_none(self, repo: Repository, tmp_path):
        video = tmp_path / "blank-placeholder.mp4"
        video.write_bytes(b"")
        media = await repo.create_media_file(library_id=1, path=str(video))
        assert media.id is not None
        assert await ensure_oshash(repo, media) is None
        stored = await repo.get_media_file(media.id)
        assert stored is not None
        assert stored.oshash is None

    @pytest.mark.asyncio
    async def test_tiny_file_stays_none(self, repo: Repository, tmp_path):
        video = tmp_path / "MIDV-123.mp4"
        video.write_bytes(b"tiny")
        media = await repo.create_media_file(library_id=1, path=str(video))
        assert media.id is not None
        assert await ensure_oshash(repo, media) is None
        stored = await repo.get_media_file(media.id)
        assert stored is not None
        assert stored.oshash is None


class TestFinalizeMediaFile:
    """标记 MediaFile 已刮削并关联 Metadata."""

    @pytest.mark.asyncio
    async def test_updates_status_and_metadata(self, repo: Repository):
        media = await repo.create_media_file(library_id=1, path="/m/ABC-123.mp4")
        assert media.id is not None
        await finalize_media_file(repo, media.id, metadata_id=42)

        updated = await repo.get_media_file(media.id)
        assert updated is not None
        assert updated.status == MediaFileStatus.SCRAPED
        assert updated.metadata_id == 42

    @pytest.mark.asyncio
    async def test_none_media_file_id_is_noop(self, repo: Repository):
        # 不应抛异常
        await finalize_media_file(repo, None, metadata_id=42)

    @pytest.mark.asyncio
    async def test_none_metadata_id_still_marks_scraped(self, repo: Repository):
        media = await repo.create_media_file(library_id=1, path="/m/ABC-456.mp4")
        assert media.id is not None
        await finalize_media_file(repo, media.id, metadata_id=None)

        updated = await repo.get_media_file(media.id)
        assert updated is not None
        assert updated.status == MediaFileStatus.SCRAPED


class TestRefreshMediaAssociation:
    """WESTERN 本地身份预检查：只读核对身份，命中后恢复关联。"""

    @pytest.mark.asyncio
    async def test_matches_oshash_among_same_number_candidates(
        self, repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        number = "Lancelotstylesevolution.26.10.16"
        first = await repo.upsert_western_metadata("scene-first", number)
        second = await repo.upsert_western_metadata("scene-second", number)
        assert first.id is not None
        assert second.id is not None

        await repo.register_media_identity(second.id, MediaIdentityType.LOCAL_OSHASH, "matched-hash")

        video = tmp_path / "video.mkv"
        video.touch()
        media = await repo.create_media_file(library_id=1, path=str(video))
        assert media.id is not None

        monkeypatch.setattr(
            "amane.handlers._common.parse_file_info",
            lambda path: SimpleNamespace(
                number=number,
                content_type=ContentType.WESTERN,
            ),
        )

        async def fake_existing_disk_path(path: Path) -> Path:
            return video

        async def fake_compute_oshash(path: Path) -> str:
            return "matched-hash"

        monkeypatch.setattr(
            "amane.handlers._common.existing_disk_path",
            fake_existing_disk_path,
        )
        monkeypatch.setattr(
            "amane.handlers._common.compute_oshash",
            fake_compute_oshash,
        )

        assert await refresh_media_association(repo, media) is True

        stored = await repo.get_media_file(media.id)
        assert stored is not None
        assert stored.metadata_id == second.id
        assert stored.status == MediaFileStatus.SCRAPED
        assert stored.oshash is None

    @pytest.mark.asyncio
    async def test_does_not_match_identity_from_another_number(
        self, repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        number = "Lancelotstylesevolution.26.10.16"
        other_number = "Lancelotstylesevolution.26.10.17"

        other = await repo.upsert_western_metadata("scene-other", other_number)
        assert other.id is not None

        await repo.register_media_identity(other.id, MediaIdentityType.LOCAL_OSHASH, "same-hash")

        video = tmp_path / "video.mkv"
        video.touch()
        media = await repo.create_media_file(library_id=1, path=str(video))
        assert media.id is not None

        monkeypatch.setattr(
            "amane.handlers._common.parse_file_info",
            lambda path: SimpleNamespace(
                number=number,
                content_type=ContentType.WESTERN,
            ),
        )

        async def fake_existing_disk_path(path: Path) -> Path:
            return video

        async def fake_compute_oshash(path: Path) -> str:
            return "same-hash"

        monkeypatch.setattr(
            "amane.handlers._common.existing_disk_path",
            fake_existing_disk_path,
        )
        monkeypatch.setattr(
            "amane.handlers._common.compute_oshash",
            fake_compute_oshash,
        )

        assert await refresh_media_association(repo, media) is False

        stored = await repo.get_media_file(media.id)
        assert stored is not None
        assert stored.metadata_id is None
        assert stored.oshash is None

    @pytest.mark.asyncio
    async def test_strm_matches_115_id_without_computing_oshash(
        self, repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        number = "Lancelotstylesevolution.26.10.16"
        metadata = await repo.upsert_western_metadata("scene-strm", number)
        assert metadata.id is not None

        await repo.register_media_identity(metadata.id, MediaIdentityType.FILE_115_ID, "115-file-id")

        strm = tmp_path / "Lancelot-styles-evolution.26.10.16.strm"
        strm.write_text("https://example.invalid/video", encoding="utf-8")
        media = await repo.create_media_file(library_id=1, path=str(strm))
        assert media.id is not None

        monkeypatch.setattr(
            "amane.handlers._common.parse_file_info",
            lambda path: SimpleNamespace(
                number=number,
                content_type=ContentType.WESTERN,
            ),
        )

        async def fake_read_115_id(path: Path) -> str:
            return "115-file-id"

        def unexpected_oshash(*args, **kwargs):
            raise AssertionError("STRM 不应计算 OSHASH")

        monkeypatch.setattr(
            "amane.handlers._common.read_litepan_115_file_id",
            fake_read_115_id,
        )
        monkeypatch.setattr(
            "amane.handlers._common.compute_oshash",
            unexpected_oshash,
        )

        assert await refresh_media_association(repo, media) is True

        stored = await repo.get_media_file(media.id)
        assert stored is not None
        assert stored.metadata_id == metadata.id
        assert stored.status == MediaFileStatus.SCRAPED
        assert stored.oshash is None


class TestExternalSubtitle:
    """登记与改 path 时的同目录字幕检查."""

    @pytest.mark.asyncio
    async def test_register_media_file_reads_library_extensions(self, repo: Repository, tmp_path: Path):
        (tmp_path / "MIDV-001.mp4").touch()
        (tmp_path / "MIDV-001.chs.srt").touch()
        library = await repo.create_library(name="t", path=str(tmp_path), subtitle_extensions=[".srt"])
        assert library.id is not None

        media = await register_media_file(repo, library.id, tmp_path / "MIDV-001.mp4", library=library)
        assert media is not None
        assert media.has_subtitle is True
        assert media.has_external_subtitle is True
        assert media.has_subtitle_in_name is False
        assert media.id is not None
        stored = await repo.get_media_file(media.id)
        assert stored is not None
        assert stored.has_external_subtitle is True

    @pytest.mark.asyncio
    async def test_register_media_file_without_library_leaves_flag(self, repo: Repository, tmp_path: Path):
        (tmp_path / "MIDV-002.mp4").touch()
        (tmp_path / "MIDV-002.srt").touch()

        media = await register_media_file(repo, 1, tmp_path / "MIDV-002.mp4")
        assert media is not None
        assert media.has_external_subtitle is False

    @pytest.mark.asyncio
    async def test_refresh_clears_stale_flag(self, repo: Repository, tmp_path: Path):
        video = tmp_path / "MIDV-003.mp4"
        video.touch()
        subtitle = tmp_path / "MIDV-003.srt"
        subtitle.touch()
        library = await repo.create_library(name="t", path=str(tmp_path), subtitle_extensions=[".srt"])
        assert library.id is not None

        media = await register_media_file(repo, library.id, video, library=library)
        assert media is not None
        assert media.id is not None
        assert media.has_external_subtitle is True

        await refresh_external_subtitle(repo, media, library)
        assert media.has_external_subtitle is True

        subtitle.unlink()
        await refresh_external_subtitle(repo, media, library)
        assert media.has_external_subtitle is False
        stored = await repo.get_media_file(media.id)
        assert stored is not None
        assert stored.has_subtitle is False

    @pytest.mark.asyncio
    async def test_refresh_without_path_change_keeps_name_marker(self, repo: Repository, tmp_path: Path):
        video = tmp_path / "MIDV-004-C.mp4"
        video.touch()
        library = await repo.create_library(name="t", path=str(tmp_path), subtitle_extensions=[])
        assert library.id is not None

        media = await register_media_file(repo, library.id, video, library=library)
        assert media is not None
        assert media.has_subtitle_in_name is True
        assert media.has_external_subtitle is False
        assert media.has_subtitle is True
