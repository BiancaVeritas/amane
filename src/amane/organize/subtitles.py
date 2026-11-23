from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ..enums import ActorGender, MoveMode
from ..parsing import FileInfo, parse_file_info
from ..utils.threads import in_thread
from .file import PlaceOutcome, execute_organize
from .path_templates import resolve_subtitle_path

if TYPE_CHECKING:
    from ..db.models import Library, Metadata

logger = structlog.get_logger()


@in_thread
def discover_subtitles(video_path: Path, extensions: Sequence[str], video: FileInfo) -> list[Path]:
    """只检查直接父目录, 不递归; 扩展名大小写不敏感. 空扩展名列表不发现. 多个字幕全部返回, 不挑主字幕.

    字幕只解析文件名 (`parse_file_info(text=...)`, 不依据目录、不使用路径回退).
    解析出番号时必须与当前视频番号相同 (忽略大小写), 再按分集配对;
    解析不出番号时回退独立目录规则: 有分集标记的跟当前视频同号, 解析不出的跟无分集或 CD1.
    """
    exts = {e.lower() for e in extensions}
    if not exts:
        return []
    parent = video_path.parent
    if not parent.is_dir():
        return []

    found: list[Path] = []
    for child in parent.iterdir():
        if not child.is_file() or child == video_path:
            continue
        if child.suffix.lower() not in exts:
            continue
        if _belongs(video, parse_file_info(text=child.name)):
            found.append(child)
    found.sort(key=lambda p: p.name.casefold())
    return found


@dataclass(frozen=True, slots=True)
class SubtitleConflict:
    """一条未落盘的字幕: 源路径 + 被占用的目标路径."""

    source: Path
    target: Path


@in_thread
def place_subtitles(
    sources: Sequence[Path],
    video_source: Path,
    video_dest: Path,
    library: Library,
    metadata: Metadata,
    file_info: FileInfo,
    mode: MoveMode,
    safe_dirs: Sequence[Path] | None = (),
    link_dir: Path | None = None,
    link_name: str | None = None,
    actor_genders: Mapping[str, ActorGender] | None = None,
) -> list[SubtitleConflict]:
    """失败与冲突只记日志, 不抛异常; 返回被占用而跳过的字幕源与其目标."""
    conflicted: list[SubtitleConflict] = []
    for sub in sources:
        dest = resolve_subtitle_path(
            library,
            metadata,
            sub,
            video_dir=video_dest.parent,
            link_dir=link_dir,
            video_name=video_dest.stem,
            video_dest=video_dest,
            link_name=link_name,
            source_path=video_source,
            file_info=file_info,
            safe_dirs=safe_dirs,
            actor_genders=actor_genders,
        )
        result = execute_organize.sync(
            source=sub,
            target_dir=dest.parent,
            target_stem=dest.stem,
            mode=mode,
            suffix=dest.suffix,
        )
        match result.outcome:
            case PlaceOutcome.PLACED:
                pass
            case PlaceOutcome.CONFLICT:
                logger.warning("subtitle target occupied", source=str(sub), dest=str(dest))
                conflicted.append(SubtitleConflict(source=sub, target=dest))
            case PlaceOutcome.FAILED:
                logger.warning("subtitle organize failed", source=str(sub), dest=str(dest), error=result.error)
    return conflicted


def _belongs(video: FileInfo, sub: FileInfo) -> bool:
    if sub.number is not None and (video.number is None or sub.number.casefold() != video.number.casefold()):
        return False
    return _cd_belongs(video.cd, sub.cd)


@in_thread
def discover_sidecars(video_path: Path, extensions: Sequence[str]) -> list[Path]:
    """视频同目录下的同名附属文件 (不递归).

    与字幕不同, 这里**不解析番号**: 附属文件的文件名就是视频主名加各自的参数后缀,
    它们天然跟着那一个视频, 解析反而不准. 空扩展名列表返空.
    """
    exts = {e.lower() for e in extensions}
    if not exts:
        return []
    parent = video_path.parent
    if not parent.is_dir():
        return []
    stem = video_path.stem
    found: list[Path] = []
    for child in parent.iterdir():
        if not child.is_file() or child == video_path:
            continue
        if child.suffix.lower() not in exts:
            continue
        # 只收「视频主名 + 后缀」这一种形状: `xxx-320-10.bif` 的主名以视频主名开头,
        # 后面直接跟 `-` 或什么都不跟. 其它视频的附属文件不能被顺手带走.
        rest = child.stem[len(stem) :] if child.stem.startswith(stem) else None
        if rest is None:
            continue
        if rest and not rest.startswith(("-", "_", ".", " ")):
            continue
        found.append(child)
    found.sort(key=lambda p: p.name.casefold())
    return found


@in_thread
def place_sidecars(
    sources: Sequence[Path],
    video_dest: Path,
    mode: MoveMode,
    *,
    video_source_stem: str,
) -> list[Path]:
    """把附属文件搬到视频旁边, 保留它们在**原视频主名之后**的那一段.

    目标名是「新视频主名 + 原尾巴」: 尾巴里是 `-320-10` 这类缩略图尺寸与取帧间隔参数,
    媒体服务器按原样读, 改了就对不上视频. 因此这里不套任何模板, 只换主名与目录.

    源视频主名必须由调用方给出: 只凭附属文件名无法反推它是从哪个主名接出来的
    (`abc` 与 `abc-320-10` 都能前缀匹配). 失败只记日志, 返回未能搬运的源.
    """
    failed: list[Path] = []
    target_dir = video_dest.parent
    video_stem = video_dest.stem
    for src in sources:
        # 附属文件名 = 原视频主名 + 尾巴; 去掉原主名就得到尾巴.
        tail = src.stem[len(video_source_stem) :] if src.stem.startswith(video_source_stem) else ""
        result = execute_organize.sync(
            source=src,
            target_dir=target_dir,
            target_stem=f"{video_stem}{tail}",
            mode=mode,
            suffix=src.suffix,
        )
        match result.outcome:
            case PlaceOutcome.PLACED:
                pass
            case PlaceOutcome.CONFLICT:
                logger.warning("sidecar target occupied", source=str(src))
                failed.append(src)
            case PlaceOutcome.FAILED:
                logger.warning("sidecar organize failed", source=str(src), error=result.error)
                failed.append(src)
    return failed


def _cd_belongs(video_cd: int | None, sub_cd: int | None) -> bool:
    if sub_cd == video_cd:
        return True
    return video_cd == 1 and sub_cd is None
