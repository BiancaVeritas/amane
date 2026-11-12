"""名字→演员的唯一解析入口 (``resolve_actor_by_name``).

展示名精确命中优先; 否则别名唯命中; 歧义或无命中时以该名字本身 get-or-create
展示名实体, 禁止归给任一共享候选. block 判定不在此层, 由调用方在解析前后各查一次.
"""

from __future__ import annotations

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from .models import Actor, ActorAlias


async def list_actor_aliases(session: AsyncSession, actor_id: int) -> list[str]:
    """按 position 保序; 不含展示名."""
    stmt = (
        select(ActorAlias.name)
        .where(ActorAlias.actor_id == actor_id)
        .order_by(col(ActorAlias.position), col(ActorAlias.id))
    )
    return list((await session.exec(stmt)).all())


async def build_actor_lookup_names(session: AsyncSession, actor: Actor) -> list[str]:
    """展示名在前, 随后别名行按 position."""
    return [actor.name, *await list_actor_aliases(session, actor.id or 0)]


async def find_mutual_alias_pairs(session: AsyncSession, actor_id: int) -> list[Actor]:
    """找出与 ``actor_id`` 互为别名的演员: A 的别名含 B, 且 B 的别名含 A.

    这种交叉指向说明是同一个人在两处写法不同 —— 各自都把对方的展示名当别名.
    单向指向不算: 那可能只是碰巧同名, 交给用户判断.
    """
    actor = await session.get(Actor, actor_id)
    if actor is None or actor.id is None:
        return []
    own_aliases = set(await list_actor_aliases(session, actor.id))
    if not own_aliases:
        return []
    # 别名里有自己展示名的演员 (对方把本演员当别名)
    rows = (
        await session.exec(
            select(ActorAlias.actor_id, ActorAlias.name).where(col(ActorAlias.name) == actor.name)
        )
    ).all()
    candidates = {int(aid) for aid, _name in rows} - {actor.id}
    if not candidates:
        return []
    peers: list[Actor] = []
    for other_id in sorted(candidates):
        other = await session.get(Actor, other_id)
        if other is None or other.name not in own_aliases:
            continue
        peers.append(other)
    return peers


async def resolve_actor_by_name(session: AsyncSession, name: str) -> Actor | None:
    """空名返回 None. 歧义或无命中时以该名字新建展示名实体."""
    if not name:
        return None
    # 展示名精确命中
    existing = (await session.exec(select(Actor).where(Actor.name == name))).first()
    if existing is not None:
        return existing
    # 别名唯命中
    rows = (await session.exec(select(ActorAlias.actor_id).where(ActorAlias.name == name))).all()
    ids = {int(i) for i in rows}
    if len(ids) == 1:
        actor = await session.get(Actor, next(iter(ids)))
        if actor is not None:
            return actor
    # 歧义或无命中: 以该名字本身新建展示名实体
    actor = Actor(name=name)
    session.add(actor)
    await session.flush()
    return actor


async def lookup_actors_by_name(session: AsyncSession, name: str) -> list[Actor]:
    """只读候选, 不创建实体. 展示名命中在前; 多命中即共享别名歧义."""
    if not name:
        return []
    found: list[Actor] = []
    # 展示名命中
    display = (await session.exec(select(Actor).where(Actor.name == name))).first()
    if display is not None:
        found.append(display)
    # 别名行命中 (去重)
    rows = (
        await session.exec(
            select(Actor)
            .join(ActorAlias, col(ActorAlias.actor_id) == col(Actor.id))
            .where(col(ActorAlias.name) == name)
            .order_by(col(Actor.id))
        )
    ).all()
    seen = {a.id for a in found}
    for actor in rows:
        if actor.id not in seen:
            found.append(actor)
            seen.add(actor.id)
    return found
