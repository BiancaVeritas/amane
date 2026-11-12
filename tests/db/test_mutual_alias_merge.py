"""互为别名的演员自动合并.

A 的别名里挂着 B 的展示名, B 的别名里也挂着 A 的展示名 —— 这种交叉指向说明是同一个人
在两处写法不同, 刮削时应该并成一个, 不该留两条各挂几部作品.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from amane.db.actor_lookup import find_mutual_alias_pairs, resolve_actor_by_name
from amane.db.models import Actor, Metadata
from amane.db.repos.facet_helpers import add_actor_aliases, clean_actor_names

if TYPE_CHECKING:
    from amane.db.repository import Repository


async def _actor(repo: Repository, name: str, aliases: list[str] | None = None) -> Actor:
    actor = Actor(name=name)
    async with repo._session() as session:
        session.add(actor)
        await session.commit()
        await session.refresh(actor)
        if aliases:
            await add_actor_aliases(session, actor, aliases)
            await session.commit()
    return actor


class TestMutualAliasDetection:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_detects_cross_pointing_pair(self, repo: Repository) -> None:
        """A 挂 B 的名字、B 挂 A 的名字 → 互为别名."""
        a = await _actor(repo, "白咲碧", ["山本玲奈"])
        b = await _actor(repo, "山本玲奈", ["白咲碧"])
        assert a.id is not None and b.id is not None

        async with repo._session() as session:
            peers_of_a = await find_mutual_alias_pairs(session, a.id)
            peers_of_b = await find_mutual_alias_pairs(session, b.id)

        assert [peer.name for peer in peers_of_a] == ["山本玲奈"]
        assert [peer.name for peer in peers_of_b] == ["白咲碧"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_one_way_alias_is_not_mutual(self, repo: Repository) -> None:
        """只有一方挂对方名字不算互为别名: 那可能只是碰巧同名."""
        a = await _actor(repo, "白咲碧", ["山本玲奈"])
        b = await _actor(repo, "山本玲奈", ["別的艺名"])
        assert a.id is not None and b.id is not None

        async with repo._session() as session:
            assert await find_mutual_alias_pairs(session, a.id) == []
            assert await find_mutual_alias_pairs(session, b.id) == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_no_alias_yields_no_pairs(self, repo: Repository) -> None:
        a = await _actor(repo, "白咲碧")
        assert a.id is not None
        async with repo._session() as session:
            assert await find_mutual_alias_pairs(session, a.id) == []


class TestMutualAliasMerge:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_clean_actor_names_merges_cross_pair(self, repo: Repository) -> None:
        """刮削同时遇到两个互为别名的名字时并成一个, 真值只留展示名."""
        a = await _actor(repo, "白咲碧", ["山本玲奈"])
        b = await _actor(repo, "山本玲奈", ["白咲碧"])
        assert a.id is not None and b.id is not None

        meta = Metadata(number="TEST-001", actors=["白咲碧", "山本玲奈"])
        async with repo._session() as session:
            session.add(meta)
            await session.commit()
            await session.refresh(meta)
            await clean_actor_names(session, meta)
            await session.commit()
            await session.refresh(meta)
            names = list(meta.actors)

        assert names == ["白咲碧"], "两个名字应并成一个展示名"

        # 来源演员被删, 它的名字留在目标的别名表里.
        async with repo._session() as session:
            assert await session.get(Actor, b.id) is None
            survivor = await resolve_actor_by_name(session, "山本玲奈")
        assert survivor is not None
        assert survivor.id == a.id

    @pytest.mark.asyncio(loop_scope="function")
    async def test_one_way_alias_kept_apart(self, repo: Repository) -> None:
        """单向别名不合并, 两个演员都保留."""
        a = await _actor(repo, "白咲碧", ["山本玲奈"])
        b = await _actor(repo, "山本玲奈", ["別的艺名"])
        assert a.id is not None and b.id is not None

        meta = Metadata(number="TEST-002", actors=["白咲碧", "山本玲奈"])
        async with repo._session() as session:
            session.add(meta)
            await session.commit()
            await session.refresh(meta)
            await clean_actor_names(session, meta)
            await session.commit()

        async with repo._session() as session:
            assert await session.get(Actor, a.id) is not None
            assert await session.get(Actor, b.id) is not None
