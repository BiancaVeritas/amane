"""theporndb 爬虫单元测试: 检索与指纹的命中判据、应答投影.

应答按 TPDB 上的真实字段值构造: ``code`` 是 kin8 站内 4 位 ``movie_id``, 他厂会用同一个数字当番号.
命中判据只留分支代表, 番号前缀与补零宽度的其余形态见 ``tests/crawlers/test_kin8.py``.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from amane.config import SiteConfig
from amane.crawlers.http import HttpClient
from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.theporndb import ThePornDBCrawler, pick_fingerprint_scene, pick_scene
from amane.parsing import ContentType

_TOKEN = "test-token"
_OSHASH = "859a35d864c0184b"


def _scene(code: str, *, title: str, date: str, studio: str, scene_id: str = "scene-1") -> dict[str, Any]:
    return {"id": scene_id, "code": code, "title": title, "date": date, "studio": {"name": studio}}


_KIN8_CUTIE = _scene(
    "0236",
    title="Kin8 Cutie",
    date="2010-05-27",
    studio="Kin8tengoku",
    scene_id="96842b9f-0405-481b-a44a-1c7fb5b0370c",
)
_KIN8_SHARED_HOUSE = _scene("4072", title="KIN8 Shared House", date="2025-07-31", studio="Kin8tengoku")
_KIN8_0001 = _scene("0001", title="KIN8 First", date="2009-01-14", studio="Kin8tengoku")
_OTHER_4072 = _scene("4072", title="Aiden Starr and Danny Wylde", date="2013-02-04", studio="Men In Pain")
_SSIS_497 = _scene("SSIS-497", title="Sample Title", date="2024-01-01", studio="S1")
_BLACKED = _scene("blacked:first-anal", title="First Anal", date="2026-03-29", studio="Blacked", scene_id="blacked-scene-1")
_BLACKED_SAME_DAY = _scene("blacked:another", title="Another", date="2026-03-29", studio="Blacked", scene_id="blacked-scene-2")
_BLACKED_OTHER_STUDIO = _scene("blackedraw:first-anal", title="First Anal", date="2026-03-29", studio="BlackedRaw", scene_id="blacked-scene-3")

PICK_SCENE_CASES: list[tuple[str, list[dict[str, Any]], dict[str, Any] | None]] = [
    # kin8 番号带站前缀, TPDB 的 code 是站内 4 位补零 id
    ("KIN8-0236", [_KIN8_CUTIE], _KIN8_CUTIE),
    ("KIN8TENGOKU-0236", [_KIN8_CUTIE], _KIN8_CUTIE),
    # 大小写归并与去零后补零到 4 位同由该行覆盖; 越界拒绝见下一行, 其余宽度形态见 test_kin8.py
    ("kin8-001", [_KIN8_0001], _KIN8_0001),
    ("KIN8-10000", [_KIN8_0001], None),
    # 他厂同号: 候选里有 kin8 条目时取 kin8 条目, 只有他厂条目不回退首条
    ("KIN8-4072", [_OTHER_4072, _KIN8_SHARED_HOUSE], _KIN8_SHARED_HOUSE),
    ("KIN8-4072", [_OTHER_4072], None),
    # 非 kin8 番号: code 相等仍是唯一判据
    ("SSIS-497", [_SSIS_497], _SSIS_497),
    # 欧美日期号按片商与发布日期确认: 同日的他厂候选不命中, 只认唯一命中
    ("Blacked.26.03.29", [_BLACKED_OTHER_STUDIO, _BLACKED], _BLACKED),
    ("Blacked.26.03.29", [_BLACKED, _BLACKED_SAME_DAY], None),
]


@pytest.mark.parametrize(("number", "results", "expected"), PICK_SCENE_CASES)
def test_pick_scene(number: str, results: list[dict[str, Any]], expected: dict[str, Any] | None) -> None:
    assert pick_scene(results, number) == expected


@pytest.mark.parametrize("results", [None, [], [None, {"code": None}]])
def test_pick_scene_rejects_unusable_results(results: object) -> None:
    """应答结构不符 (不是列表、空表、元素不是字典或缺 ``code``) 时不命中, 也不抛异常."""
    assert pick_scene(results, "KIN8-0236") is None


def _crawler(web: AsyncMock) -> ThePornDBCrawler:
    return ThePornDBCrawler(client=HttpClient(web=web), config=SiteConfig(api_token=_TOKEN))


FETCH_CASES: list[tuple[str, list[dict[str, Any]], tuple[str, str, str, str] | None]] = [
    # 命中后按 code / title / date / studio 投影
    ("KIN8-0236", [_KIN8_CUTIE], ("0236", "Kin8 Cutie", "2010-05-27", "Kin8tengoku")),
    # 只有他厂同号候选: 链路走通但不命中
    ("KIN8-4072", [_OTHER_4072], None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("number", "scenes", "expected"), FETCH_CASES)
async def test_fetch(number: str, scenes: list[dict[str, Any]], expected: tuple[str, str, str, str] | None) -> None:
    """检索词保持番号原样 (裸 movie_id 在 TPDB 上检索不到), 命中后投影为元数据."""
    web = AsyncMock()
    web.post_json.return_value = {"data": {"searchScene": scenes}}
    crawler = _crawler(web)

    metadata = await crawler.fetch(SearchQuery(number=number))

    assert web.post_json.await_args.kwargs["json"]["variables"] == {"term": number}
    if expected is None:
        assert metadata is None
        return
    assert metadata is not None
    assert (metadata.number, metadata.title, metadata.release, metadata.studio) == expected


@pytest.mark.asyncio
async def test_western_multiple_number_results_match_unique_studio_and_date_without_hash() -> None:
    """多条番号搜索结果中, 唯一片商 + 日期匹配可直接命中, 不要求 OSHASH."""
    studio = "Lancelot Styles evolution"
    other_date_a = _scene("lancelot:other-a", title="Other A", date="2026-09-10", studio=studio)
    other_date_b = _scene("lancelot:other-b", title="Other B", date="2026-09-10", studio=studio)
    exact = _scene("lancelot:exact", title="Exact", date="2026-10-09", studio=studio)
    web = AsyncMock()
    web.post_json.return_value = {"data": {"searchScene": [other_date_a, exact, other_date_b]}}
    crawler = _crawler(web)

    metadata = await crawler.fetch(
        SearchQuery(number="LancelotStylesEvolution.26.10.09", content_type=ContentType.WESTERN)
    )

    assert metadata is not None
    assert metadata.external_id == exact["id"]
    assert web.post_json.await_count == 1


@pytest.mark.asyncio
async def test_western_multiple_same_date_results_without_hash_are_rejected() -> None:
    """同片商同日期仍有多个候选时, 没有 OSHASH 不猜测."""
    web = AsyncMock()
    web.post_json.return_value = {"data": {"searchScene": [_BLACKED, _BLACKED_SAME_DAY]}}
    crawler = _crawler(web)

    metadata = await crawler.fetch(SearchQuery(number="Blacked.26.03.29", content_type=ContentType.WESTERN))

    assert metadata is None
    assert web.post_json.await_count == 1


@pytest.mark.asyncio
async def test_fetch_by_scene_id() -> None:
    """指定 Scene ID 直接通过 findScene 获取, 不调用指纹或番号搜索."""
    web = AsyncMock()
    web.post_json.return_value = {"data": {"findScene": _KIN8_CUTIE}}
    crawler = _crawler(web)

    metadata = await crawler.fetch(
        SearchQuery(number="KIN8-0236", content_type=ContentType.WESTERN, theporndb_uuid=_KIN8_CUTIE["id"].upper())
    )

    assert metadata is not None
    assert metadata.external_id == _KIN8_CUTIE["id"]
    assert web.post_json.await_count == 1
    assert web.post_json.await_args.kwargs["json"]["variables"] == {"id": _KIN8_CUTIE["id"].upper()}
    assert web.post_json.await_args.args[0] == "https://theporndb.net/graphql?type=Scene"


FINGERPRINT_SHAPES: list[tuple[object, dict[str, Any] | None]] = [
    ([[_BLACKED]], _BLACKED),
    # 未提供番号时保留非 WESTERN 旧行为; WESTERN 调用会额外传入番号并校验唯一性.
    ([[_BLACKED, _BLACKED_SAME_DAY]], _BLACKED),
    ([[]], None),
    # 外层按入参逐条对应, 只发一个指纹, 第二组不参与判定
    ([[], [_BLACKED]], None),
    # 上游两层非空, TPDB 的兼容实现都可空: 缺值不能抛, 也不能当成命中
    ([None], None),
    ([[None]], None),
    (None, None),
    ({}, None),
]


@pytest.mark.parametrize(("results", "expected"), FINGERPRINT_SHAPES)
def test_pick_fingerprint_scene(results: object, expected: dict[str, Any] | None) -> None:
    """批量指纹应答逐层按可空展开, 只看第一组."""
    assert pick_fingerprint_scene(results) == expected


def test_pick_western_fingerprint_scene_filters_by_number_and_requires_unique_scene() -> None:
    """OSHASH 可能返回多个 Scene; 先用番号的片商与日期过滤, 再按 Scene ID 判唯一."""
    other_studio = _BLACKED_OTHER_STUDIO
    same_number = _BLACKED
    duplicate_same_scene = dict(_BLACKED)
    multiple_same_number = _BLACKED_SAME_DAY

    assert pick_fingerprint_scene([[other_studio, same_number]], "Blacked.26.03.29") == same_number
    assert pick_fingerprint_scene([[other_studio]], "Blacked.26.03.29") is None
    assert pick_fingerprint_scene([[same_number, duplicate_same_scene]], "Blacked.26.03.29") == same_number
    assert pick_fingerprint_scene([[same_number, multiple_same_number]], "Blacked.26.03.29") is None


@pytest.mark.asyncio
async def test_western_unique_number_result_does_not_require_oshash() -> None:
    """番号检索恰好一条时直接刮削, 不要求 OSHASH."""
    web = AsyncMock()
    web.post_json.return_value = {"data": {"searchScene": [_BLACKED]}}
    crawler = _crawler(web)

    metadata = await crawler.fetch(SearchQuery(number="Blacked.26.03.29", content_type=ContentType.WESTERN))

    assert metadata is not None
    assert metadata.external_id == _BLACKED["id"]
    assert web.post_json.await_count == 1
    assert web.post_json.await_args.kwargs["json"]["variables"] == {"term": "Blacked.26.03.29"}


@pytest.mark.asyncio
async def test_western_multiple_number_results_use_oshash_to_disambiguate() -> None:
    """番号有多个候选时, 仅 OSHASH 命中其中一条才允许刮削."""
    web = AsyncMock()
    web.post_json.side_effect = [
        {"data": {"searchScene": [_BLACKED, _BLACKED_SAME_DAY]}},
        {"data": {"findScenesBySceneFingerprints": [[_BLACKED]]}},
    ]
    crawler = _crawler(web)

    metadata = await crawler.fetch(
        SearchQuery(number="Blacked.26.03.29", file_hash=_OSHASH, content_type=ContentType.WESTERN)
    )

    assert metadata is not None
    assert metadata.external_id == _BLACKED["id"]
    assert web.post_json.await_count == 2
    assert web.post_json.await_args_list[0].kwargs["json"]["variables"] == {"term": "Blacked.26.03.29"}
    assert web.post_json.await_args_list[1].kwargs["json"]["variables"] == {
        "fingerprints": [[{"hash": _OSHASH, "algorithm": "OSHASH"}]]
    }


@pytest.mark.asyncio
async def test_western_oshash_disambiguates_same_number_candidates_by_scene_id() -> None:
    web = AsyncMock()
    web.post_json.side_effect = [
        {"data": {"searchScene": [_BLACKED, _BLACKED_SAME_DAY]}},
        {"data": {"findScenesBySceneFingerprints": [[_BLACKED]]}},
    ]
    crawler = _crawler(web)

    metadata = await crawler.fetch(
        SearchQuery(number="Blacked.26.03.29", file_hash=_OSHASH, content_type=ContentType.WESTERN)
    )

    assert metadata is not None
    assert metadata.external_id == _BLACKED["id"]


@pytest.mark.asyncio
async def test_western_zero_number_results_returns_not_found() -> None:
    web = AsyncMock()
    web.post_json.return_value = {"data": {"searchScene": []}}
    crawler = _crawler(web)

    metadata = await crawler.fetch(SearchQuery(number="Blacked.26.03.29", content_type=ContentType.WESTERN))

    assert metadata is None
    assert web.post_json.await_count == 1


@pytest.mark.asyncio
async def test_western_multiple_number_results_without_oshash_are_rejected() -> None:
    web = AsyncMock()
    web.post_json.return_value = {"data": {"searchScene": [_BLACKED, _BLACKED_SAME_DAY]}}
    crawler = _crawler(web)

    metadata = await crawler.fetch(SearchQuery(number="Blacked.26.03.29", content_type=ContentType.WESTERN))

    assert metadata is None
    assert web.post_json.await_count == 1


FINGERPRINT_MISS_CASES: list[dict[str, Any]] = [
    {"data": {"findScenesBySceneFingerprints": [[]]}},
    {"data": {"findScenesBySceneFingerprints": [None]}},
    {"data": None},
    # HTTP 200 但 GraphQL 层报错: 不能中断整次刮削
    {"errors": [{"message": 'Cannot query field "findScenesBySceneFingerprints"'}]},
]


@pytest.mark.asyncio
@pytest.mark.parametrize("fingerprint_payload", FINGERPRINT_MISS_CASES)
async def test_fingerprint_miss_falls_back_to_search(fingerprint_payload: dict[str, Any]) -> None:
    """指纹没命中或应答不可用时按番号检索, 结果照常投影."""
    web = AsyncMock()
    web.post_json.side_effect = [fingerprint_payload, {"data": {"searchScene": [_KIN8_CUTIE]}}]
    crawler = _crawler(web)

    metadata = await crawler.fetch(SearchQuery(number="KIN8-0236", file_hash=_OSHASH))

    assert metadata is not None
    assert metadata.number == "0236"
    assert web.post_json.await_count == 2


