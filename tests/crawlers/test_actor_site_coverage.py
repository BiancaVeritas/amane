"""演员站点性别覆盖 — 女-only / 双向 / 非演员站 / 保序过滤."""

from __future__ import annotations

import pytest

from amane.crawlers.actor import filter_sites_for_gender, site_allows_actor_gender
from amane.enums import ActorGender, SiteName


@pytest.mark.parametrize(
    ("site", "gender", "allowed"),
    [
        (SiteName.MINNANO, ActorGender.FEMALE, True),
        (SiteName.MINNANO, ActorGender.MALE, False),
        (SiteName.MINNANO, ActorGender.UNKNOWN, False),
        (SiteName.GFRIENDS, ActorGender.FEMALE, True),
        (SiteName.GFRIENDS, ActorGender.MALE, False),
        (SiteName.JAVDB, ActorGender.FEMALE, True),
        (SiteName.JAVDB, ActorGender.MALE, True),
        (SiteName.JAVDB, ActorGender.UNKNOWN, True),
        (SiteName.WIKIPEDIA, ActorGender.UNKNOWN, True),
        (SiteName.THEPORNDB, ActorGender.TRANSGENDER_FEMALE, True),
        (SiteName.MINNANO, ActorGender.TRANSGENDER_FEMALE, False),
        (SiteName.JAVDB, ActorGender.TRANSGENDER_FEMALE, False),
        (SiteName.DMM, ActorGender.FEMALE, False),
        (SiteName.DMM, ActorGender.UNKNOWN, False),
    ],
)
def test_site_allows_actor_gender(site: SiteName, gender: ActorGender, allowed: bool) -> None:
    assert site_allows_actor_gender(site, gender) is allowed


def test_filter_sites_preserves_order_and_splits() -> None:
    configured = [SiteName.MINNANO, SiteName.JAVDB, SiteName.GFRIENDS, SiteName.DMM]
    allowed, skipped = filter_sites_for_gender(configured, ActorGender.MALE)
    assert allowed == [SiteName.JAVDB]
    assert skipped == [SiteName.MINNANO, SiteName.GFRIENDS, SiteName.DMM]
