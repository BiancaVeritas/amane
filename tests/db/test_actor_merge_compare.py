"""演员实体自动合并的判定 (`db.actor_merge.compare`).

刮削会把同一个人的不同艺名建成多个 Actor, 判定合并靠两道闸门: 名字有交集, 且双方都有的
身体数据一致. 第二道不是"必须齐全" —— 演员常常只刮到其中一两项, 强求生日身高俱全等于
永不合并; 而任一项对不上就足以说明不是同一人.
"""

from __future__ import annotations

import time

import pytest

from amane.db.actor_merge import MergeVerdict, compare
from amane.db.models import Actor, ActorAlias


def _actor(**fields: object) -> Actor:
    return Actor(name=str(fields.pop("name", "同名")), **fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("desc", "left", "right", "expected"),
    [
        (
            "只有身高, 身高相同即可合并",
            _actor(name="あかり", height=160),
            _actor(name="あかり", height=160),
            True,
        ),
        (
            "只有身高, 身高不同不合并",
            _actor(name="あかり", height=160),
            _actor(name="あかり", height=165),
            False,
        ),
        (
            "只有生日, 生日相同即可合并",
            _actor(name="あかり", birthday="1995-03-12"),
            _actor(name="あかり", birthday="1995/03/12"),
            True,
        ),
        (
            "只有生日, 生日不同不合并",
            _actor(name="あかり", birthday="1995-03-12"),
            _actor(name="あかり", birthday="1996-03-12"),
            False,
        ),
        (
            "一边只有生日另一边只有身高: 没有共同可比项, 不合并",
            _actor(name="あかり", birthday="1995-03-12"),
            _actor(name="あかり", height=160),
            False,
        ),
        (
            "一边有生日另一边没有, 但有共同的身高可比且一致: 可合并",
            _actor(name="あかり", birthday="1995-03-12", height=160),
            _actor(name="あかり", height=160),
            True,
        ),
        (
            "一边有身高另一边没有: 该项不参与比较",
            _actor(name="あかり", height=160, bust=88),
            _actor(name="あかり", bust=88),
            True,
        ),
        (
            "双方都有生日与身高且都一致",
            _actor(name="あかり", birthday="1995-03-12", height=160),
            _actor(name="あかり", birthday="1995-03-12", height=160),
            True,
        ),
        (
            "生日一致但身高不同",
            _actor(name="あかり", birthday="1995-03-12", height=160),
            _actor(name="あかり", birthday="1995-03-12", height=170),
            False,
        ),
        (
            "三围任一项不同即不合并",
            _actor(name="あかり", height=160, bust=88),
            _actor(name="あかり", height=160, bust=90),
            False,
        ),
        (
            "罩杯不同不合并",
            _actor(name="あかり", height=160, cup="C"),
            _actor(name="あかり", height=160, cup="D"),
            False,
        ),
        (
            "名字无交集: 资料再一致也不合并",
            _actor(name="あかり", height=160),
            _actor(name="ゆい", height=160),
            False,
        ),
        (
            "别名命中即可视为名字有交集",
            _actor(name="あかり", height=160),
            _actor(name="ゆい", height=160),
            True,
        ),
        (
            "一项可比资料都没有: 不合并",
            _actor(name="あかり"),
            _actor(name="あかり"),
            False,
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_compare(desc: str, left: Actor, right: Actor, expected: bool) -> None:
    # 最后一条用例靠别名产生交集, 其余用展示名.
    left_aliases = ["ゆい"] if desc == "别名命中即可视为名字有交集" else []
    right_aliases: list[str] = []

    verdict = compare(left, left_aliases, right, right_aliases)

    assert isinstance(verdict, MergeVerdict)
    assert verdict.merge is expected, f"{desc}: {verdict.reason}"


def test_compare_reports_reason_when_rejected() -> None:
    """拒绝时要说明原因, 便于日志排查与人工复核."""
    verdict = compare(_actor(name="あかり", height=160), [], _actor(name="あかり", height=170), [])

    assert verdict.merge is False
    assert "身高不同" in verdict.reason


def test_compare_without_comparable_fields_says_so() -> None:
    """没有任何可比资料时要明确指出, 不要与「资料不一致」混为一谈."""
    verdict = compare(_actor(name="あかり"), [], _actor(name="あかり"), [])

    assert verdict.merge is False
    assert verdict.reason == "无可比的资料"


@pytest.mark.asyncio
async def test_actors_sharing_name_does_not_full_scan(repo: Repository) -> None:
    """同名粗筛走 SQL, 不把整张演员表读进内存.

    演员表上万行后, 每次写演员都全表扫描会把一部片的刮削拖到分钟级 ——
    实测判据: 加大量无关演员后, 本函数耗时不应随之线性增长.
    """
    from amane.db.repos.facets import _actors_sharing_name

    session_factory = repo._session

    async def seed(count: int, *, with_target: bool) -> Actor | None:
        async with session_factory() as session:
            batch = [Actor(name=f"无关演员{count}_{i:05d}") for i in range(count)]
            session.add_all(batch)
            await session.flush()
            session.add_all(
                ActorAlias(actor_id=a.id, name=f"无关别名{count}_{i:05d}", position=0)
                for i, a in enumerate(batch)
            )
            target = Actor(name="目标演员") if with_target else None
            if target is not None:
                session.add(target)
            await session.flush()
            await session.commit()
            return target

    small = await seed(10, with_target=True)
    assert small is not None
    await seed(30000, with_target=False)
    async with session_factory() as session:
        refreshed = await session.get(Actor, small.id)
        assert refreshed is not None
        t0 = time.monotonic()
        await _actors_sharing_name(session, refreshed)
        large = time.monotonic() - t0

    # 三万条无关数据下仍应是毫秒级; 全表扫描会把两张表读进内存并按条归一化.
    assert large < 0.5, f"耗时 {large:.3f}s, 疑似仍在全表扫描"
