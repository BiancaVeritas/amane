"""字段一律 Optional + 默认值. 列变 NULL 时降级为空, 不抛错.

列被删除或改名由导入期 schema 校验拦截. 只覆盖 repository 显式 SELECT 的列.
"""

import datetime

from pydantic import BaseModel

from ...enums import Language


class R18Person(BaseModel):
    id: int | None = None
    name_romaji: str | None = None
    name_kanji: str | None = None
    name_kana: str | None = None

    def best(self, language: Language | None = None) -> str | None:
        # JP / EN 指定时不允许回退其它语言; 未指定则日文优先再回退 romaji.
        if language is Language.JP:
            return self.name_kanji or self.name_kana
        if language is Language.EN:
            return self.name_romaji
        return self.name_kanji or self.name_kana or self.name_romaji


class R18Named(BaseModel):
    id: int | None = None
    name_en: str | None = None
    name_ja: str | None = None

    def best(self, language: Language | None = None) -> str | None:
        # JP / EN 指定时不允许回退其它语言; 未指定则日文优先再回退英文.
        if language is Language.JP:
            return self.name_ja
        if language is Language.EN:
            return self.name_en
        return self.name_ja or self.name_en


class R18Video(BaseModel):
    content_id: str
    dvd_id: str | None = None
    title_en: str | None = None
    title_ja: str | None = None
    comment_en: str | None = None
    comment_ja: str | None = None
    runtime_mins: int | None = None
    release_date: datetime.date | None = None
    sample_url: str | None = None
    jacket_full_url: str | None = None
    jacket_cover_url: str | None = None
    gallery_full_first: str | None = None
    gallery_full_last: str | None = None
    gallery_thumb_first: str | None = None
    gallery_thumb_last: str | None = None


class R18VideoDetail(BaseModel):
    video: R18Video
    maker: R18Named | None = None
    label: R18Named | None = None
    series: R18Named | None = None
    actresses: list[R18Person] = []
    actors: list[R18Person] = []
    directors: list[R18Person] = []
    categories: list[R18Named] = []
    trailer_url: str | None = None
