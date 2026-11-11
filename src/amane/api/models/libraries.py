from collections.abc import Mapping
from typing import TYPE_CHECKING, Self

from pydantic import BaseModel, Field, model_validator

from ...db import Library
from ...enums import DownloadableResource, LibraryAutomation, LibraryIngest, LinkMode, MoveMode
from ...library import (
    DEFAULT_SUBTITLE_EXTENSIONS,
    DEFAULT_TRAILER_PATTERN,
    BlacklistPattern,
    MinFileSize,
    SubtitleExtensions,
    TrailerPattern,
    resolve_ingest_cloud_path,
)
from ...organize.path_templates import (
    EXTRAFANART_NAME_TEMPLATE_DEFAULT,
    EXTRAFANART_TEMPLATE_DEFAULT,
    FANART_TEMPLATE_DEFAULT,
    JINJA_VIDEO_TEMPLATE_DEFAULT,
    NFO_TEMPLATE_DEFAULT,
    PLACEHOLDER_MAP_KEYS,
    PLACEHOLDERS,
    POSTER_TEMPLATE_DEFAULT,
    SUBTITLE_TEMPLATE_DEFAULT,
    THUMB_TEMPLATE_DEFAULT,
    TRAILER_TEMPLATE_DEFAULT,
    VIDEO_TEMPLATE_DEFAULT,
    ExtrafanartNameTemplate,
    PathTemplate,
)
from ...organize.strm_content import StrmContentTemplate
from ...utils.model import create_partial_model, subset_of


def to_forward_slash_path(value: str | None) -> str | None:
    """路径统一用 ``/`` 分隔; 反斜杠（含 UNC 前缀）就地转换.

    与 ``normalize_cloud_path`` 同一取向: 从 Windows 复制的路径带反斜杠, 直接存下来会让同一
    目录出现两种写法, 比较与前缀判断都要额外兼容.
    """
    if value is None:
        return None
    converted = value.strip().replace("\\", "/")
    if not converted:
        return None
    # UNC 前缀 \\host 转完是 //host, 保留它; 其余重复斜杠收敛成一个.
    prefix = "//" if converted.startswith("//") else ""
    body = converted[len(prefix) :]
    while "//" in body:
        body = body.replace("//", "/")
    return prefix + body


class LibraryCreateRequest(BaseModel):
    name: str | None = None
    """显示名; 留空则取路径 basename."""
    path: str
    automation: LibraryAutomation = LibraryAutomation.SCRAPE
    auto_organize: bool = False
    ingest: LibraryIngest = LibraryIngest.NATIVE
    cloud_path: str | None = None
    """CloudDrive 虚拟路径 (POSIX, 如 /115open/云下载). ingest=clouddrive 时必填."""
    recursive: bool = True
    patterns: list[str] = []
    move_mode: MoveMode = MoveMode.MOVE
    watch_dir_enabled: bool = False
    """启用独立监控目录: 目标目录转为原地模式, 另由 ``watch_path`` 承接下载目录."""
    watch_path: str | None = None
    """下载目录; 留空表示只开启目标目录的原地模式. 反斜杠在保存时转为 ``/``."""
    watch_rename: bool = False
    """原地模式下是否允许按模板重命名."""
    video_template: PathTemplate = VIDEO_TEMPLATE_DEFAULT
    use_jinja_template: bool = False
    """启用 Jinja2 命名模板; 开启后 video_template 不再参与渲染."""
    jinja_video_template: str | None = None
    """Jinja2 语法下的视频命名模板."""
    custom_placeholders: dict[str, str] = Field(default_factory=dict)
    """自定义占位符: 名字 → 文件名里要匹配的字面串. 名字须是合法标识符."""
    link_template: PathTemplate | None = None
    link_mode: LinkMode = LinkMode.STRM
    strm_content_template: StrmContentTemplate | None = None
    thumb_template: PathTemplate | None = None
    poster_template: PathTemplate | None = None
    fanart_template: PathTemplate | None = None
    extrafanart_template: PathTemplate | None = None
    extrafanart_name_template: ExtrafanartNameTemplate | None = None
    nfo_template: PathTemplate | None = None
    trailer_template: PathTemplate | None = None
    subtitle_template: PathTemplate | None = None
    subtitle_extensions: SubtitleExtensions = Field(default_factory=lambda: list(DEFAULT_SUBTITLE_EXTENSIONS))
    write_nfo: bool = True
    copy_resources: list[DownloadableResource] = Field(default_factory=lambda: list(DownloadableResource))
    trailer_pattern: TrailerPattern = DEFAULT_TRAILER_PATTERN
    blacklist_patterns: list[BlacklistPattern] = []
    """文件名正则列表; 命中任一则扫描/监控跳过, 并作为无效文件进入清理清单."""
    min_file_size: MinFileSize = 0
    """最小视频大小 (字节). 小于此值的扫描视频跳过入库, 并作为无效文件进入清理清单. 0 关闭."""
    scan: bool = True

    @model_validator(mode="after")
    def _cloud_path_for_ingest(self) -> Self:
        self.cloud_path = resolve_ingest_cloud_path(self.ingest, self.cloud_path)
        return self


if TYPE_CHECKING:
    type LibraryUpdateRequest = Library

# 外部可写字段: 除主键 id 外的全部库配置列.
LibraryUpdateRequest = create_partial_model(Library, ignore_fields=("id",), partial_cls_name="LibraryUpdateRequest")


class LibraryResponse(BaseModel):
    id: int
    name: str
    path: str
    automation: LibraryAutomation
    auto_organize: bool
    ingest: LibraryIngest
    cloud_path: str | None = None
    recursive: bool
    patterns: list[str] = []
    move_mode: MoveMode
    watch_dir_enabled: bool
    watch_path: str | None
    watch_rename: bool
    video_template: str
    use_jinja_template: bool
    jinja_video_template: str | None
    custom_placeholders: dict[str, str] = Field(default_factory=dict)
    link_template: str | None = None
    link_mode: LinkMode
    strm_content_template: str | None = None
    thumb_template: str | None = None
    poster_template: str | None = None
    fanart_template: str | None = None
    extrafanart_template: str | None = None
    extrafanart_name_template: str | None = None
    nfo_template: str | None = None
    trailer_template: str | None = None
    subtitle_template: str | None = None
    subtitle_extensions: list[str]
    write_nfo: bool
    copy_resources: list[DownloadableResource]
    trailer_pattern: str
    blacklist_patterns: list[str]
    min_file_size: int


class LibraryListResponse(BaseModel):
    items: list[LibraryResponse]


@subset_of(Library, covariant=True)
class OptionalPathTemplateDefaults(BaseModel):
    """附属模板缺省 (Library 对应列为 None 时 ORGANIZE 使用)."""

    thumb_template: PathTemplate
    poster_template: PathTemplate
    fanart_template: PathTemplate
    extrafanart_template: PathTemplate
    extrafanart_name_template: ExtrafanartNameTemplate
    nfo_template: PathTemplate
    trailer_template: PathTemplate
    subtitle_template: PathTemplate


OPTIONAL_TEMPLATE_DEFAULTS = OptionalPathTemplateDefaults(
    thumb_template=THUMB_TEMPLATE_DEFAULT,
    poster_template=POSTER_TEMPLATE_DEFAULT,
    fanart_template=FANART_TEMPLATE_DEFAULT,
    extrafanart_template=EXTRAFANART_TEMPLATE_DEFAULT,
    extrafanart_name_template=EXTRAFANART_NAME_TEMPLATE_DEFAULT,
    nfo_template=NFO_TEMPLATE_DEFAULT,
    trailer_template=TRAILER_TEMPLATE_DEFAULT,
    subtitle_template=SUBTITLE_TEMPLATE_DEFAULT,
)


class PathTemplatePlaceholder(BaseModel):
    name: str
    map_keys: list[str] = Field(
        default_factory=list,
        description="有闭合取值时列出规范 key, 供 `{name|k=v}` 映射校验与 UI 提示. 空则不校验映射 key.",
    )
    custom: bool = Field(default=False, description="库级自定义占位符, 可删除.")
    literal: str | None = Field(default=None, description="自定义占位符匹配的字面串.")


class PathTemplateSchemaResponse(BaseModel):
    """与 resolve_paths 同源."""

    video_default: str
    jinja_video_default: str
    optional_defaults: OptionalPathTemplateDefaults
    placeholders: list[PathTemplatePlaceholder]
    subtitle_extensions_default: list[str]


def path_template_schema(
    custom: Mapping[str, str] | None = None,
) -> PathTemplateSchemaResponse:
    """内置占位符 + 该库已配置的自定义占位符.

    自定义项在 UI 上单独成组 (``custom=True``), 以便与内置项区分并支持删除.
    """
    builtin = [
        PathTemplatePlaceholder(name=name, map_keys=list(PLACEHOLDER_MAP_KEYS.get(name, ()))) for name in PLACEHOLDERS
    ]
    extra = [
        PathTemplatePlaceholder(name=name, custom=True, literal=literal) for name, literal in (custom or {}).items()
    ]
    return PathTemplateSchemaResponse(
        video_default=VIDEO_TEMPLATE_DEFAULT,
        jinja_video_default=JINJA_VIDEO_TEMPLATE_DEFAULT,
        optional_defaults=OPTIONAL_TEMPLATE_DEFAULTS,
        placeholders=[*builtin, *extra],
        subtitle_extensions_default=list(DEFAULT_SUBTITLE_EXTENSIONS),
    )
