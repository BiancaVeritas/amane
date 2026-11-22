from pydantic import BaseModel, Field, field_validator

from ...enums import ActorGender
from ...utils.dates import normalize_calendar_date


class ActorMetadata(BaseModel):
    name: str | None = None
    aliases: list[str] = Field(default_factory=list)
    gender: ActorGender | None = None
    birthday: str | None = None
    birthplace: str | None = None
    height: int | None = None
    bust: int | None = None
    waist: int | None = None
    hip: int | None = None
    cup: str | None = None
    blood_type: str | None = None
    overview: str | None = None
    tagline: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    provider_ids: dict[str, str] = Field(default_factory=dict)
    source_url: str | None = None
    # 源站相对路径; 存入 raw 便于溯源, 不参与聚合标量.
    content_path: str | None = None

    @field_validator("birthday", mode="before")
    @classmethod
    def _normalize_birthday(cls, value: object) -> str | None:
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            msg = "birthday must be a string"
            raise TypeError(msg)
        return normalize_calendar_date(value)
