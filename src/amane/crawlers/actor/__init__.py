"""资料来源 / 头像来源与影片爬虫隔离.

性别覆盖读 ``profile().genders``. ``register`` 顺序即默认 ``profile_sites`` / ``image_sites`` 优先级.
"""

from .base import ActorCrawler, ActorFetcher
from .models import ActorMetadata
from .registry import actor_registry
from .site_coverage import filter_sites_for_gender, site_allows_actor_gender
from .sites import (
    AvbaseActorCrawler,
    GFriendsActorCrawler,
    JavDBActorCrawler,
    MinnanoActorCrawler,
    ThePornDBActorCrawler,
    TushyActorCrawler,
    TushyRawActorCrawler,
    WikipediaActorCrawler,
)

# 注册顺序分别决定资料来源 / 头像来源的默认优先级; 两类能力过滤后各自保持相对顺序.
actor_registry.register(TushyActorCrawler)
actor_registry.register(TushyRawActorCrawler)
actor_registry.register(GFriendsActorCrawler)
actor_registry.register(MinnanoActorCrawler)
actor_registry.register(ThePornDBActorCrawler)
actor_registry.register(WikipediaActorCrawler)
actor_registry.register(JavDBActorCrawler)
# Avbase 档案字段出现率低, 置于资料来源末尾.
actor_registry.register(AvbaseActorCrawler)

__all__ = [
    "ActorCrawler",
    "ActorFetcher",
    "ActorMetadata",
    "AvbaseActorCrawler",
    "GFriendsActorCrawler",
    "JavDBActorCrawler",
    "MinnanoActorCrawler",
    "ThePornDBActorCrawler",
    "TushyActorCrawler",
    "TushyRawActorCrawler",
    "WikipediaActorCrawler",
    "actor_registry",
    "filter_sites_for_gender",
    "site_allows_actor_gender",
]
