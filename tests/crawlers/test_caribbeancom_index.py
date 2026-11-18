import pytest

from amane.crawlers import registry
from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.caribbeancom_index import CaribbeancomIndexCrawler
from amane.enums import SiteName


def test_caribbeancom_index_is_registered_as_independent_source() -> None:
    assert registry.get(SiteName.CARIBBEANCOM_INDEX) is CaribbeancomIndexCrawler


@pytest.mark.asyncio
async def test_search_matches_parsed_number_case_insensitively_and_exactly(tmp_path) -> None:
    mapping_path = tmp_path / "caribbeancom.json"
    mapping_path.write_text('{"red-0163": {"id": "RED-163"}}', encoding="utf-8")
    crawler = CaribbeancomIndexCrawler(object(), mapping_path=mapping_path)  # type: ignore[arg-type]

    # original_number 来自 Amane 解析结果; 不依赖本地完整文件名.
    query = SearchQuery("RED163", file_path="/library/unrelated-name.mp4", original_number="RED-0163")
    url = await crawler._search(query)

    assert url == "https://www.caribbeancompr.com/moviepages/RED-163/index.html"
    assert await crawler._search(SearchQuery("RED-0163", original_number="red-0163")) == url
    assert await crawler._search(SearchQuery("RED163", original_number="RED163")) is None
    assert await crawler._search(SearchQuery("RED-163", original_number="RED-163")) is None
    assert await crawler._search(SearchQuery("RED-01630", original_number="RED-01630")) is None


class _FakeClient:
    async def get_html(self, url: str, **kwargs: object) -> str:
        assert kwargs.get("encoding") == "euc_jp"
        return """
        <div class="movie-info"><h1>Index title</h1><p>Index plot</p>
        <ul><li class="movie-spec"><span class="spec-title">出演</span>
        <span class="spec-content"><a>Actor A</a></span></li></ul></div>
        <div class="vjs-poster" style="background-image: url('/moviepages/RED-163/images/l_l.jpg');"></div>
        """


@pytest.mark.asyncio
async def test_scrape_keeps_original_number_as_metadata_identity(tmp_path) -> None:
    mapping_path = tmp_path / "caribbeancom.json"
    mapping_path.write_text('{"RED-163": {"id": "RED-163"}}', encoding="utf-8")
    crawler = CaribbeancomIndexCrawler(_FakeClient(), mapping_path=mapping_path)  # type: ignore[arg-type]
    url = "https://www.caribbeancompr.com/moviepages/RED-163/index.html"
    crawler._lookup_cache[url] = ("RED-163", "RED-163")
    result = await crawler._scrape(url)

    assert result is not None
    assert result.number == "RED-163"
    assert result.title == "Index title"
    assert result.source_url == url
    assert result.external_id == "RED-163"
