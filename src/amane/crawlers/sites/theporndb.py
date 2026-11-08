from typing import Any, override

from ...enums import ActorGender, SiteName
from ...net.connectivity import ConnectivityOutcome, SkipReason, assess_response
from ...net.errors import FailureReason, RequestError
from ...plugins.models import SourceTrait
from ...parsing.file_info import ContentType
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, FilmActor, MediaMetadata, SearchQuery
from ..parsing import fold_number, fold_studio, is_same_number, parse_western_number
from .kin8 import movie_id as kin8_movie_id

_PERFORMER_GENDER: dict[str, ActorGender] = {
    "FEMALE": ActorGender.FEMALE,
    "MALE": ActorGender.MALE,
}

# kin8 条目的 ``code`` 是站内 4 位 id, 补零规则只在 ``sites/kin8.py::movie_id`` 维护;
# 他厂会用同一数字当番号, 因此该形态的命中必须同时限定片商.
_KIN8_STUDIO = fold_studio("Kin8tengoku")

_SCENE_FIELDS = """
    id title code date duration director details
    studio { name }
    tags { name }
    performers { as performer { name gender } }
    images { url }
"""

_SEARCH_QUERY = f"""
query Search($term: String!) {{
  searchScene(term: $term) {{{_SCENE_FIELDS}
  }}
}}"""

# stash-box 上游已删除单条指纹查询, 批量形态是唯一入口: 外层按入参逐条对应, 内层可空.
# TPDB 是自研兼容层, 可空标注比上游宽松, 因此参数按上游最严的形态传, 应答按可空解析.
_FINGERPRINT_QUERY = f"""
query Find($fingerprints: [[FingerprintQueryInput!]!]!) {{
  findScenesBySceneFingerprints(fingerprints: $fingerprints) {{{_SCENE_FIELDS}
  }}
}}"""

_FIND_BY_ID_QUERY = f"""
query FindByID($id: ID!) {{
  findScene(id: $id) {{{_SCENE_FIELDS}
  }}
}}"""

_TYPE_FILTER: dict[str, str] = {
    "censored": "JAV",
    "uncensored": "Scene",
    "western": "Scene",
    "fc2": "Scene",
    "amateur": "Scene",
    "hentai": "JAV",
}


class ThePornDBCrawler(Crawler):
    """Stash-box GraphQL. ``content_type`` 映射到 ``?type=``; 缺省不加 filter."""

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(
            name=SiteName.THEPORNDB,
            base_url="https://theporndb.net/graphql",
            traits=frozenset({SourceTrait.USES_FILE_HASH}),
        )

    @override
    async def check_connectivity(self) -> ConnectivityOutcome:
        """真发一次 GraphQL 请求: 本源只有带 token 才会请求, 探测必须同样验证凭据与连通性.

        查一个不存在的 id: 应答便宜, 且能区分「token 被拒」与「站点不可达」. 地址经 ``_gql_url``
        取: ``?type=`` 是每次请求的过滤条件, 探测验证的是可达性与凭据, 不加该条件.
        """
        token = self.config.api_token if self.config else None
        if not token:
            return ConnectivityOutcome.skipped(SkipReason.MISSING_CREDENTIAL, "api_token")
        url = self._gql_url(None)
        try:
            resp = await self.client.web_client.request(
                "POST",
                url,
                json={"query": _FIND_BY_ID_QUERY, "variables": {"id": "0"}},
                headers={"Authorization": f"Bearer {token}"},
                max_attempts=1,
            )
        except RequestError as exc:
            return ConnectivityOutcome.failed(exc.reason, url=url, http_status=exc.http_status)

        try:
            payload = resp.json()
        except Exception:
            payload = None
        if isinstance(payload, dict) and payload.get("errors") and not payload.get("data"):
            # HTTP 状态正常而在 GraphQL 层失败: 原因不写在状态码上. 上游的错误文本是英文原文, 截断后作为
            # 可行动信息报给用户 (不含 Authorization 头), 完整应答写入日志.
            errors = payload["errors"]
            self.logger.warning("theporndb graphql errors", errors=errors)
            message = ""
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                message = str(errors[0].get("message") or "")
            return ConnectivityOutcome.failed(FailureReason.API_ERROR, url=url, detail=message[:200] or None)
        return assess_response(url, resp)

    async def fetch(self, query: SearchQuery, options: FetchOptions | None = None) -> MediaMetadata | None:
        """检索应答已含详情字段, 因此不必再按 id 取一次."""
        token = self.config.api_token if self.config else None
        if not token:
            return None

        scene = await self._search_scene(query, token)
        return self._scene_to_metadata(scene) if scene is not None else None

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        """与 ``fetch`` 共用检索流程与命中判据."""
        token = self.config.api_token if self.config else None
        if not token:
            return None

        return _scene_url(await self._search_scene(query, token))

    async def _search_scene(self, query: SearchQuery, token: str) -> dict[str, Any] | None:
        """优先复用明确 UUID; WESTERN 多结果先按片商 + 日期匹配, 再用 OSHASH 消歧."""
        headers = {"Authorization": f"Bearer {token}"}
        gql_url = self._gql_url(query.content_type)

        # 手动指定 / 重新刮削的 Scene ID 是确定身份, 不再经过指纹或番号猜测.
        if query.theporndb_uuid:
            data = await self.client.post_json(
                gql_url,
                json={"query": _FIND_BY_ID_QUERY, "variables": {"id": query.theporndb_uuid}},
                headers=headers,
            )
            scene = _gql_field(data, "findScene")
            if isinstance(scene, dict) and str(scene.get("id") or "").casefold() == query.theporndb_uuid.casefold():
                return scene
            return None

        if query.content_type == ContentType.WESTERN:
            # 恢复原有番号检索: 唯一结果本身足以确定作品, 不强制要求 OSHASH.
            search_data = await self.client.post_json(
                gql_url,
                json={"query": _SEARCH_QUERY, "variables": {"term": query.number}},
                headers=headers,
            )
            search_results = _gql_field(search_data, "searchScene")
            scenes = [item for item in search_results if isinstance(item, dict)] if isinstance(search_results, list) else []
            if len(scenes) == 1:
                return scenes[0]
            if len(scenes) > 1:
                # 搜索词可能返回同片商的多部影片: 先用日期号中的片商 + 日期
                # 筛出唯一候选, 只有仍无法确定时才尝试 OSHASH 消歧.
                scene = pick_scene(scenes, query.number)
                if scene is not None:
                    return scene
            if len(scenes) > 1 and query.file_hash:
                fingerprint_data = await self.client.post_json(
                    gql_url,
                    json={
                        "query": _FINGERPRINT_QUERY,
                        "variables": {"fingerprints": [[{"hash": query.file_hash, "algorithm": "OSHASH"}]]},
                    },
                    headers=headers,
                )
                fingerprint_results = _gql_field(fingerprint_data, "findScenesBySceneFingerprints")
                groups = _as_list(fingerprint_results)
                fingerprint_scenes = [item for item in _as_list(groups[0])] if groups else []
                candidate_ids = {str(scene.get("id") or "").casefold() for scene in scenes}
                matched_by_id: dict[str, dict[str, Any]] = {}
                for candidate in fingerprint_scenes:
                    if not isinstance(candidate, dict):
                        continue
                    candidate_id = str(candidate.get("id") or "").casefold()
                    if candidate_id and candidate_id in candidate_ids:
                        matched_by_id.setdefault(candidate_id, candidate)
                if len(matched_by_id) == 1:
                    return next(iter(matched_by_id.values()))
            return None

        if query.file_hash:
            data = await self.client.post_json(
                gql_url,
                json={
                    "query": _FINGERPRINT_QUERY,
                    "variables": {"fingerprints": [[{"hash": query.file_hash, "algorithm": "OSHASH"}]]},
                },
                headers=headers,
            )
            fingerprint_results = _gql_field(data, "findScenesBySceneFingerprints")
            scene = pick_fingerprint_scene(fingerprint_results)
            if scene is not None:
                return scene

        # 非 WESTERN 沿用原有番号检索流程.
        data = await self.client.post_json(
            gql_url,
            # 检索词保留番号原样: kin8 的站前缀才是命中它的条件, 换成裸 ``movie_id`` 后 TPDB 返回 0 条.
            json={"query": _SEARCH_QUERY, "variables": {"term": query.number}},
            headers=headers,
        )
        return pick_scene(_gql_field(data, "searchScene"), query.number)

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        token = self.config.api_token if self.config else None
        if not token or not url.startswith("gql://scene/"):
            return None

        scene_id = url.removeprefix("gql://scene/")
        headers = {"Authorization": f"Bearer {token}"}

        data = await self.client.post_json(
            self.base_url,
            json={
                "query": _FIND_BY_ID_QUERY,
                "variables": {"id": scene_id},
            },
            headers=headers,
        )

        scene = _gql_field(data, "findScene")
        if not isinstance(scene, dict):
            return None
        return self._scene_to_metadata(scene)

    def _gql_url(self, content_type: ContentType | None) -> str:
        if content_type:
            gql_type = _TYPE_FILTER.get(str(content_type))
            if gql_type:
                return f"{self.base_url}?type={gql_type}"
        return self.base_url

    @staticmethod
    def _scene_to_metadata(scene: dict[str, Any]) -> MediaMetadata:
        number = scene.get("code") or scene.get("title", "")

        studio = None
        if isinstance(scene.get("studio"), dict):
            studio = scene["studio"].get("name")

        actors: list[FilmActor] = []
        for pa in scene.get("performers", []) or []:
            perf = (pa or {}).get("performer", {}) or {}
            name = perf.get("name") or pa.get("as", "")
            if not name:
                continue
            gender = _PERFORMER_GENDER.get(str(perf.get("gender") or ""))
            actors.append(FilmActor(name=name, gender=gender or ActorGender.UNKNOWN))

        tags = [t.get("name", "") for t in (scene.get("tags") or []) if t.get("name")]

        images = scene.get("images") or []
        thumb_url = images[0]["url"] if images else None

        return MediaMetadata(
            number=number,
            title=scene.get("title") or None,
            actors=actors,
            studio=studio,
            release=scene.get("date") or None,
            runtime=_runtime_minutes(scene.get("duration")),
            tags=tags,
            directors=[scene["director"]] if scene.get("director") else [],
            plot=scene.get("details") or None,
            thumb_urls=[thumb_url] if thumb_url else [],
            external_id=scene.get("id") or None,
            theporndb_uuid=scene.get("id") or None,
            source_url=f"https://theporndb.net/scenes/{scene['id']}" if scene.get("id") else None,
        )


def pick_scene(results: object, number: str) -> dict[str, Any] | None:
    """检索结果 → 命中的条目; 确认不了返回 None, 不回退首条.

    ``code`` 与入参按同一番号判定, 欧美日期号的两种年份写法等价, 命中取应答里的首条.
    欧美条目的 ``code`` 是 ``studio:title-slug``, 与文件名番号不同构, 这类日期号按
    片商 + 发布日期确认, 只认唯一命中. ``KIN8-####`` 按站内 4 位 id 判定.
    """
    scenes = [item for item in results if isinstance(item, dict)] if isinstance(results, list) else []
    western = parse_western_number(number)
    if western is not None:
        hits = [
            scene
            for scene in scenes
            if _scene_date(scene) == western.date and fold_studio(_scene_studio(scene)) == western.studio
        ]
        return hits[0] if len(hits) == 1 else None

    kin8_id = kin8_movie_id(number)
    for scene in scenes:
        code = scene.get("code")
        if not isinstance(code, str) or not code:
            continue
        if is_same_number(code, number):
            return scene
        if kin8_id is not None and _is_kin8_scene(scene, code, kin8_id):
            return scene

    return None


def pick_fingerprint_scene(results: object, number: str | None = None) -> dict[str, Any] | None:
    """指纹应答只看本次唯一 OSHASH 对应的第一组.

    WESTERN 还必须用文件番号校验候选, 并且只接受唯一 Scene ID. 同一 Scene 的重复应答去重;
    不同 Scene 即使番号相同也不取首条. 非 WESTERN 保留既有首条指纹命中行为.
    """
    groups = _as_list(results)
    if not groups:
        return None
    scenes = [scene for scene in _as_list(groups[0]) if isinstance(scene, dict)]
    if number is None:
        return scenes[0] if scenes else None

    western = parse_western_number(number)
    if western is None:
        hits = [
            scene
            for scene in scenes
            if isinstance(scene.get("code"), str) and is_same_number(scene["code"], number)
        ]
    else:
        hits = [
            scene
            for scene in scenes
            if _scene_date(scene) == western.date and fold_studio(_scene_studio(scene)) == western.studio
        ]

    by_id: dict[str, dict[str, Any]] = {}
    for scene in hits:
        scene_id = scene.get("id")
        if isinstance(scene_id, str) and scene_id.strip():
            by_id.setdefault(scene_id.casefold(), scene)
    return next(iter(by_id.values())) if len(by_id) == 1 else None


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _is_kin8_scene(scene: dict[str, Any], code: str, kin8_id: str) -> bool:
    return fold_number(code) == kin8_id and fold_studio(_scene_studio(scene)) == _KIN8_STUDIO


def _scene_studio(scene: dict[str, Any]) -> str:
    studio = scene.get("studio")
    name = studio.get("name") if isinstance(studio, dict) else None
    return name if isinstance(name, str) else ""


def _scene_date(scene: dict[str, Any]) -> str:
    date = scene.get("date")
    return date if isinstance(date, str) else ""


def _runtime_minutes(duration: object) -> int | None:
    """秒 → 分钟; 缺省与 0 视为没有时长."""
    if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
        return None
    return duration // 60


def _scene_url(scene: dict[str, Any] | None) -> str | None:
    scene_id = scene.get("id") if scene is not None else None
    return f"gql://scene/{scene_id}" if isinstance(scene_id, str) and scene_id else None


def _gql_field(payload: object, key: str) -> object:
    """取 GraphQL 应答的 ``data.<key>``; 结构不符返回 None."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    return data.get(key) if isinstance(data, dict) else None
