"""把 Amane 的演员人物资料推送到 Emby / Jellyfin 的人物条目.

只写人物条目, 不碰影片元数据. 更新必须带上写回白名单中的当前值: 服务器按替换语义处理未提交字段.
单个条目失败记入结果并继续; 人物列表读不出来 (不可达 / 认证失败) 才让整个任务失败.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

from ..media.file_type import IMAGE_MIME_PREFIX, detect_media_type_bytes
from ..media_server import (
    SEARCH_LIMIT,
    MediaPerson,
    MediaServerClient,
    MediaServerClientLike,
    MediaServerError,
    find_persons,
    index_persons,
    match_persons,
)
from ..tags import MappingGroup, load_translations
from ..utils.collections import unique_ids
from ..utils.threads import in_thread
from .models import ActorPushPayload, ActorPushResult
from .protocol import TaskHandler, TaskResult

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from ..config import HotSettings, MediaServerConfig
    from ..db.models import Actor
    from ..db.repository import Repository
    from ..media import ResourceStore

logger = structlog.get_logger()

_FAILURES_LIMIT = 50
"""结果里保留的失败明细条数; ``failed`` 计数不受此上限影响."""

_ACTOR_BATCH = 500
"""全量推送时逐页读取演员的条数."""

_SEARCH_THRESHOLD = 50
"""ID 列表不超过该值时逐名检索; 更大或全量时一次读回人物表建索引."""

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class _Counts:
    requested: int = 0
    matched: int = 0
    updated: int = 0
    images: int = 0
    skipped: int = 0
    not_found: int = 0
    no_image: int = 0
    missing: int = 0
    failed: int = 0
    failures: list[str] = field(default_factory=list)

    def failure(self, name: str, reason: str) -> None:
        if len(self.failures) < _FAILURES_LIMIT:
            self.failures.append(f"{name}: {reason}")


@dataclass(frozen=True)
class _Portrait:
    """已落盘且类型可用的头像."""

    data: bytes
    mime: str


@dataclass(frozen=True)
class _UnrecognizedPortrait:
    """已落盘但不是可识别的图片: 上传只会被服务器拒绝, 不发送."""

    url: str


_PortraitSource = _Portrait | _UnrecognizedPortrait


@in_thread
def _read_portrait(path: Path) -> tuple[bytes, str | None]:
    """读出头像字节并判定媒体类型; PIL 解析同样放在线程里, 不占用事件循环."""
    data = path.read_bytes()
    return data, detect_media_type_bytes(data)


class ActorPushHandler(TaskHandler[ActorPushPayload, ActorPushResult]):
    def __init__(
        self,
        repo: Repository,
        resource_store: ResourceStore,
        config: HotSettings,
        client_factory: Callable[[MediaServerConfig], MediaServerClientLike] = MediaServerClient,
        mapping_dir: Path | None = None,
    ) -> None:
        super().__init__(payload_t=ActorPushPayload, result_t=ActorPushResult)
        self._repo = repo
        self._resource_store = resource_store
        self._config = config
        self._client_factory = client_factory
        # 与模板渲染共用同一份映射词典: 有映射就把 Emby 里的人物名换成中文, 没有就不动.
        self._actor_names = load_translations(mapping_dir, MappingGroup.ACTOR)

    async def handle(self, payload: ActorPushPayload) -> TaskResult[ActorPushResult]:
        cfg = self._config.media_server
        if not cfg.enabled:
            return TaskResult(success=False, error="媒体服务器未配置: 需要 media_server.url 与 media_server.api_key")

        counts = _Counts()
        if payload.actor_ids:
            ids = unique_ids(payload.actor_ids)
            counts.requested = len(ids)
            actors = await self._repo.get_actors(ids)
            counts.missing = counts.requested - len(actors)
        else:
            actors = await self._list_actors()
            counts.requested = len(actors)

        if not actors:
            return TaskResult(success=True, result=self._result(counts))

        actor_ids = [actor.id for actor in actors if actor.id is not None]
        aliases = await self._repo.get_actor_aliases_map(None if not payload.actor_ids else actor_ids)

        index = None
        seen_persons: set[str] = set()
        async with self._client_factory(cfg) as client:
            if not payload.actor_ids or len(actors) > _SEARCH_THRESHOLD:
                try:
                    persons = await client.list_persons()
                except MediaServerError as exc:
                    return TaskResult(
                        success=False,
                        error=f"读取人物列表失败: {exc}",
                        result=self._result(counts),
                    )
                index = index_persons(persons)

            for done, actor in enumerate(actors, start=1):
                await self._sync_actor(
                    actor,
                    client,
                    index,
                    aliases.get(actor.id or 0, []),
                    seen_persons=seen_persons,
                    force=payload.force,
                    counts=counts,
                )
                await self.report_progress(done, len(actors), actor.name)

        success = counts.failed == 0 or counts.updated + counts.images > 0
        logger.info(
            "actor push completed",
            kind=cfg.kind,
            requested=counts.requested,
            matched=counts.matched,
            updated=counts.updated,
            images=counts.images,
            skipped=counts.skipped,
            not_found=counts.not_found,
            no_image=counts.no_image,
            missing=counts.missing,
            failed=counts.failed,
        )
        return TaskResult(success=success, result=self._result(counts))

    async def _list_actors(self) -> list[Actor]:
        actors: list[Actor] = []
        offset = 0
        while True:
            page = await self._repo.list_actors(offset=offset, limit=_ACTOR_BATCH)
            actors.extend(page)
            if len(page) < _ACTOR_BATCH:
                return actors
            offset += _ACTOR_BATCH

    async def _sync_actor(
        self,
        actor: Actor,
        client: MediaServerClientLike,
        index: dict[str, list[MediaPerson]] | None,
        aliases: Sequence[str],
        *,
        seen_persons: set[str],
        force: bool,
        counts: _Counts,
    ) -> None:
        # 搜索词: 原始名 + 别名 + 映射里的中文名.
        # 中文名也参与, 是因为人物名可能已经被本工具或用户改成中文了; 只按原始名搜的话,
        # 那条记录会彻底匹配不上 (Emby 检索是模糊的, 但精确过滤只比 Name 字段).
        # 搜索词: 原始名 + 别名 + 映射里的中文名.
        # 中文名也参与, 是因为人物名可能已经被本工具或用户改成中文了; 只按原始名搜的话,
        # 那条记录会彻底匹配不上 (Emby 检索是模糊的, 但精确过滤只比 Name 字段).
        display_name = self._actor_names.get(actor.name)
        names = [actor.name, *aliases]
        if display_name and display_name not in names:
            names.append(display_name)
        try:
            persons = await self._find_persons(client, index, names)
        except MediaServerError as exc:
            counts.failed += 1
            counts.failure(actor.name, f"查找人物失败: {exc}")
            return
        if not persons:
            counts.not_found += 1
            return

        portrait = await self._portrait(actor)
        if portrait is None:
            counts.no_image += 1

        for person in persons:
            if person.id in seen_persons:
                continue
            seen_persons.add(person.id)
            counts.matched += 1
            await self._sync_person(actor, person, portrait, client, aliases=aliases, force=force, counts=counts)

    async def _find_persons(
        self,
        client: MediaServerClientLike,
        index: dict[str, list[MediaPerson]] | None,
        names: Sequence[str],
    ) -> list[MediaPerson]:
        if index is not None:
            return find_persons(index, names)
        candidates: dict[str, MediaPerson] = {}
        for name in names:
            results = await client.search_persons(name)
            if len(results) >= SEARCH_LIMIT:
                # 模糊候选已到单页上限, 精确匹配可能被截断; 回退到全量索引.
                return find_persons(index_persons(await client.list_persons()), names)
            for person in results:
                candidates.setdefault(person.id, person)
        return match_persons(names, candidates.values())

    async def _portrait(self, actor: Actor) -> _PortraitSource | None:
        """头像取 ``image_urls`` 里第一个已落盘且内容判为图片的资源; 不在推送任务里补下载.

        记录里的媒体类型不参与: 旧数据由 URL 后缀推出, 与字节无关. 文件读取失败与未落盘同样
        跳过 (最终计 ``no_image``); 内容判不出时不发送, 返回 ``_UnrecognizedPortrait`` 记失败.
        """
        unrecognized: str | None = None
        for url in actor.image_urls:
            record = await self._resource_store.get_by_url(url)
            if record is None:
                continue
            path = self._resource_store.full_path(record)
            try:
                data, mime = await _read_portrait(path)
            except OSError as exc:
                logger.warning("actor push: 头像文件读取失败", url=url, path=str(path), error=str(exc))
                continue
            if mime is None or not mime.startswith(IMAGE_MIME_PREFIX):
                logger.warning("actor push: 头像不是可识别的图片", url=url, path=str(path), mime=mime)
                unrecognized = unrecognized or url
                continue
            return _Portrait(data=data, mime=mime)
        return _UnrecognizedPortrait(url=unrecognized) if unrecognized else None

    async def _sync_person(
        self,
        actor: Actor,
        person: MediaPerson,
        portrait: _PortraitSource | None,
        client: MediaServerClientLike,
        *,
        aliases: Sequence[str],
        force: bool,
        counts: _Counts,
    ) -> None:
        updates = _profile_updates(
            actor,
            person,
            force=force,
            display_name=self._actor_names.get(actor.name),
            aliases=aliases,
        )
        needs_image = portrait is not None and (force or not person.has_primary_image)
        if not updates and not needs_image:
            counts.skipped += 1
            return

        person_failed = False
        # 只有标签变化时不算「更新了一条资料」: 标签是附带产物, 若让它计入 updated,
        # 头像失败会被误判成整体成功 (success 判定里有 ``updated > 0``).
        only_tags = bool(updates) and set(updates) <= {"Tags"}
        if updates:
            try:
                await client.update_person(person, updates)
            except MediaServerError as exc:
                counts.failed += 1
                person_failed = True
                counts.failure(f"{actor.name} / {person.name}", f"字段写入失败: {exc}")
            else:
                if not only_tags:
                    counts.updated += 1

        if needs_image:
            assert portrait is not None
            error: str | None = None
            if isinstance(portrait, _UnrecognizedPortrait):
                error = f"头像格式无法识别: {portrait.url}"
            else:
                try:
                    await client.upload_primary_image(person.id, portrait.data, content_type=portrait.mime)
                except MediaServerError as exc:
                    error = f"头像上传失败: {exc}"
                else:
                    counts.images += 1
            if error is not None:
                if not person_failed:
                    counts.failed += 1
                counts.failure(f"{actor.name} / {person.name}", error)

    @staticmethod
    def _result(counts: _Counts) -> ActorPushResult:
        return ActorPushResult(
            requested=counts.requested,
            matched=counts.matched,
            updated=counts.updated,
            images=counts.images,
            skipped=counts.skipped,
            not_found=counts.not_found,
            no_image=counts.no_image,
            missing=counts.missing,
            failed=counts.failed,
            failures=counts.failures,
        )


def _profile_updates(
    actor: Actor,
    person: MediaPerson,
    *,
    force: bool,
    display_name: str | None = None,
    aliases: Sequence[str] = (),
) -> dict[str, object]:
    """按覆盖规则给出目标字段; 不把空值写成清空动作.

    ``display_name`` 来自演员映射: 人物是按原名找到的, 找到之后才把名字换成中文.
    """
    updates: dict[str, object] = {}
    # 期望名: 有映射用映射, 没有就用原名. 两者不同才写一次, 免得每轮推送重复改名.
    # 映射被删掉之后这里自然回落到原名, 于是 Emby 里的中文名会被改回去, 不会一直停在旧值.
    desired_name = display_name or actor.name
    if desired_name and desired_name != person.name:
        updates["Name"] = desired_name
    overview = _composed_overview(actor)
    if overview and (force or not _has_text(person.overview)):
        updates["Overview"] = overview

    if actor.birthday and _ISO_DATE.fullmatch(actor.birthday):
        if force or not person.premiere_date:
            updates["PremiereDate"] = actor.birthday
        if force or person.production_year is None:
            updates["ProductionYear"] = int(actor.birthday[:4])

    if actor.birthplace and (force or not _has_text_list(person.production_locations)):
        updates["ProductionLocations"] = [actor.birthplace]

    # 名字写进 Tags: Emby 的人物条目没有专门的别名字段, Tags 是唯一能存多个的地方.
    # 顺序为「原始名 > 别名」, 中文映射不写 —— 那是显示层的东西, 不该混进服务器元数据.
    # Tags 不参与人物匹配 (搜索只看 Name), 这里纯粹是给人在服务器界面上看的;
    # 保留服务器上已有的其它标签, 只做并集, 不清空.
    wanted_tags = list(dict.fromkeys([actor.name, *aliases]))
    merged = list(dict.fromkeys([*person.tags, *wanted_tags]))
    if merged != person.tags:
        updates["Tags"] = merged
    return updates


#: 人物尺寸行与简介之间的分隔符.
#
# Emby 的人物简介只有一个纯文本字段, 换行符在多数客户端会被折叠成一空格. 这里先写 `<br>`:
# 按 HTML 渲染的客户端 (网页端) 据此断行; 后面再跟一个真正的换行, 让不解析标签的客户端也能
# 靠换行断开, 而不是把尺寸行和简介连成一句.
# 它同时是「尺寸行已经写进去了」的标记, 见 ``_SIZES_MARKER``.
_OVERVIEW_BREAK = "<br>\n"

#: 尺寸行的起始标记; 用它判断已有简介里是否已经带了一段尺寸行.
_SIZES_MARKER = "身高："


def _composed_overview(actor: Actor) -> str | None:
    """把尺寸行拼在简介前面, 尺寸行以 ``<br>`` 结尾.

    格式固定为「身高 / 三围 / 罩杯 / 血型」, 缺项跳过; 一项都没有时返回原简介, 不造空行.
    已有简介也带着尺寸行时不重复拼: 反复推送同一人物时内容必须稳定, 否则每轮都会写一次.
    """
    sizes = _sizes_line(actor)
    overview = (actor.overview or "").strip()
    if not sizes:
        return overview or None
    head = f"{sizes}{_OVERVIEW_BREAK}"
    if not overview:
        return head
    if overview.startswith(_SIZES_MARKER):
        return overview
    return f"{head}{overview}"


def _sizes_line(actor: Actor) -> str:
    """按 身高 / 三围 / 罩杯 / 血型 的顺序拼一行; 缺项跳过, 全缺返回空串."""
    parts: list[str] = []
    if actor.height is not None:
        parts.append(f"身高：{actor.height}cm")
    if actor.bust is not None and actor.waist is not None and actor.hip is not None:
        parts.append(f"三围：{actor.bust} / {actor.waist} / {actor.hip}")
    if actor.cup:
        parts.append(f"罩杯：{actor.cup}")
    if actor.blood_type:
        parts.append(f"血型：{actor.blood_type} 型")
    return "、".join(parts)


def _has_text(value: str | None) -> bool:
    return bool(value and value.strip())


def _has_text_list(values: Sequence[str]) -> bool:
    return any(value.strip() for value in values)
