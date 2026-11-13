from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator

from ...db import MediaFile, MediaFileStatus
from ...parsing import ContentType, Mosaic, MosaicNature, mosaic_nature, shows_uncensored
from ...utils.model import create_partial_model, to_resp
from ...utils.path import to_forward_slash


class MediaFileResponse(BaseModel):
    id: int
    library_id: int
    path: str
    oshash: str | None = None
    size: int | None = None
    duration: float | None = None
    codec: str | None = None
    number: str | None = None
    status: MediaFileStatus
    mosaic: Mosaic | None = None
    censored: bool = False
    uncensored: bool = False
    has_subtitle: bool = False
    definition: str | None = None
    ltx: bool = False
    metadata_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("path", mode="after")
    @classmethod
    def _forward_slash(cls, v: str) -> str:
        """路径一律按 ``/`` 下发.

        索引入库时写的是扫描得到的真实路径 (Windows 上是反斜杠), 而库根统一存 ``/``;
        两者在同一页面上并列显示会像写错了, 前端也只按 ``/`` 拼展示.
        """
        return to_forward_slash(v) or v


def to_media_file_response(media: MediaFile, content_type: ContentType | None = None) -> MediaFileResponse:
    """``censored`` / ``uncensored`` 是这一份文件的马赛克性质, 与 ``mosaic`` 及条目类型合成.

    ``censored`` 只看性质 (带无码标记的副本不算有码), ``uncensored`` 与列表的 ``uncensored`` 筛选同口径:
    条目类型为无码, 或这一份带无码标记. ``content_type`` 为 None (未绑定条目) 时只看自身标记.
    """
    response = to_resp(MediaFileResponse, media)
    return response.model_copy(
        update={
            "censored": mosaic_nature(media.mosaic, content_type) is MosaicNature.CENSORED,
            "uncensored": shows_uncensored(media.mosaic, content_type),
        }
    )


class MediaListResponse(BaseModel):
    items: list[MediaFileResponse]
    total: int


class MediaBatchDeleteRequest(BaseModel):
    """批量删除媒体文件记录; 不存在的 id 计入 missing."""

    ids: list[int] = Field(min_length=1, max_length=1000, description="MediaFile ID 列表; 上限防 SQLite 绑定变量超限")


class MediaBatchDeleteResponse(BaseModel):
    deleted: int = Field(description="成功删除的数量")
    missing: int = Field(description="不存在的 id 数量")


if TYPE_CHECKING:
    type MediaFileUpdateRequest = MediaFile

MediaFileUpdateRequest = create_partial_model(
    MediaFile, fields=("status", "number", "path", "metadata_id"), partial_cls_name="MediaFileUpdateRequest"
)
