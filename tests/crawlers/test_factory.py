"""爬虫工厂测试"""

from typing import TYPE_CHECKING, Any, cast

import pytest

from amane.crawlers.base import Crawler, CrawlerProfile
from amane.crawlers.factory import CrawlerFactory
from amane.crawlers.registry import registry

if TYPE_CHECKING:
    from amane.enums import SiteName


class _FakeCrawler(Crawler):
    """用于测试的假爬虫 - 只记录构造次数, 不做实际获取."""

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=cast("SiteName", "fake_test_site"), base_url="https://fake.example.com")

    def __init__(self, client, config=None):
        super().__init__(client, config=config)
        self._constructed = True

    async def _search(self, query, options=None):
        return None

    async def _scrape(self, url, options=None):
        return None


@pytest.fixture(autouse=True)
def _register_fake_crawler():
    """注册假爬虫并在测试后清理."""
    registry.register(_FakeCrawler)
    yield
    registry._crawlers.pop("fake_test_site", None)


class TestCrawlerFactoryGet:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_returns_cached_instance_on_second_call(self, http_client):
        factory = CrawlerFactory(http_client)
        c1 = await factory.get("fake_test_site")
        c2 = await factory.get("fake_test_site")
        assert c1 is c2

    @pytest.mark.asyncio(loop_scope="function")
    async def test_returns_none_for_unregistered_name(self, http_client):
        factory = CrawlerFactory(http_client)
        result = await factory.get("nonexistent_site")
        assert result is None


class TestCrawlerFactoryGetCrawlers:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_returns_only_registered_crawlers(self, http_client):
        factory = CrawlerFactory(http_client)
        result = await factory.get_crawlers(["fake_test_site", "nonexistent"])
        assert len(result) == 1
        assert "fake_test_site" in result
        assert "nonexistent" not in result


@pytest.mark.asyncio(loop_scope="function")
async def test_javdb_film_and_actor_are_distinct_instances(http_client):
    from amane.crawlers.actor.sites.javdb import JavDBActorCrawler
    from amane.crawlers.sites.javdb import JavDBCrawler

    factory = CrawlerFactory(http_client)
    film = await factory.get("javdb")
    actor = await factory.get_actor("javdb")
    assert isinstance(film, JavDBCrawler)
    assert isinstance(actor, JavDBActorCrawler)
    assert film is not actor


@pytest.mark.asyncio(loop_scope="function")
async def test_theporndb_film_and_actor_are_distinct_instances(http_client):
    from amane.crawlers.actor.sites.theporndb import ThePornDBActorCrawler
    from amane.crawlers.sites.theporndb import ThePornDBCrawler

    factory = CrawlerFactory(http_client)
    film = await factory.get("theporndb")
    actor = await factory.get_actor("theporndb")
    assert isinstance(film, ThePornDBCrawler)
    assert isinstance(actor, ThePornDBActorCrawler)
    assert film is not actor


@pytest.mark.parametrize(
    ("name", "filename"),
    [
        ("caribbeancompr", "caribbeancompr_heyzo_index.json"),
        ("d2pass", "d2pass_heyzo_index.json"),
        ("caribbeancom_index", "caribbeancom_index.json"),
    ],
)
@pytest.mark.asyncio(loop_scope="function")
async def test_number_mapping_crawlers_receive_mapping_path(http_client, tmp_path, name, filename):
    factory = CrawlerFactory(http_client, data_dir=tmp_path)

    crawler = await factory.get(name)

    assert crawler is not None
    mapping_path = cast(Any, crawler)._mapping_path
    assert mapping_path == tmp_path / "mappings" / filename
    assert mapping_path.is_file()
    assert mapping_path.read_text(encoding="utf-8") == "{}\n"

    cached = await factory.get(name)
    assert cached is crawler


@pytest.mark.asyncio(loop_scope="function")
async def test_minnano_actor_crawler_receives_data_dir(http_client, tmp_path):
    factory = CrawlerFactory(http_client, data_dir=tmp_path)

    crawler = await factory.get_actor("minnano")

    assert crawler is not None
    actor_mappings = cast(Any, crawler)._actor_mappings
    assert actor_mappings is not None
    assert actor_mappings.directory == tmp_path / "mappings"
    assert actor_mappings.file == tmp_path / "mappings" / "minnano_actress_map.json"
    assert actor_mappings.file.is_file()

    cached = await factory.get_actor("minnano")
    assert cached is crawler


@pytest.mark.asyncio(loop_scope="function")
async def test_theporndb_actor_receives_actor_translator(http_client):
    from amane.crawlers.actor.sites.theporndb import ThePornDBActorCrawler
    from amane.enums import Language, MetadataField

    class StubTranslator:
        async def translate(
            self, text: str, target: Language, field: MetadataField, *, use_cache: bool = True
        ) -> str | None:
            return None

        async def translate_actor(self, text: str, field: MetadataField, *, use_cache: bool = True) -> str | None:
            return None

    factory = CrawlerFactory(http_client)
    translator = StubTranslator()

    factory.set_actor_translator(translator)

    actor = await factory.get_actor("theporndb")

    assert isinstance(actor, ThePornDBActorCrawler)
    assert actor._translator is translator
