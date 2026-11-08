import pytest

from amane.crawlers import registry
from amane.crawlers.sites.caribbeancom import CaribbeancomCrawler, _detail, _number, _runtime_minutes
from amane.enums import SiteName


def test_caribbeancom_registered() -> None:
    assert registry.get(SiteName.CARIBBEANCOM) is CaribbeancomCrawler


def test_number_is_exact_and_separator_sensitive() -> None:
    assert _number("103126-001") == "103126-001"
    assert _number("003126-001") == "003126-001"
    assert _number("103126_001") is None
    assert _number("103126-01") is None
    assert _number("103126-0001") is None
    assert _number("103126-001 ") is None


def test_detail_parses_metadata_and_all_public_gallery_links() -> None:
    gallery_html = "".join(
        f'<div><a class="gallery-item" data-is_sample="1" href="/moviepages/072926-001/images/l/{i:03}.jpg"></a></div>'
        for i in range(1, 6)
    )
    html = f'''<h1 itemprop="name">Sample title</h1><p itemprop="description">Sample plot</p>
    <ul><li><span class="spec-title">出演</span><span class="spec-content"><a class="spec__tag"><span>Actress A</span></a></span></li>
    <li><span class="spec-title">配信日</span><span itemprop="uploadDate" class="spec-content">2026/07/29</span></li>
    <li><span class="spec-title">再生時間</span><span class="spec-content"><span>00:40:28</span></span></li>
    <li><span class="spec-title">シリーズ</span><span class="spec-content"><a>Series A</a></span></li>
    <li><span class="spec-title">タグ</span><span class="spec-content"><a class="spec-item">Tag A</a></span></li></ul>
    {gallery_html}<a class="gallery-item" data-is_sample="0" href="javascript:void(0);"></a>'''
    data, gallery = _detail(html, "https://www.caribbeancom.com/moviepages/072926-001/index.html")
    assert data["title"] == "Sample title"
    assert data["plot"] == "Sample plot"
    assert data["actors"] == ["Actress A"]
    assert data["release"] == "2026-07-29"
    assert data["runtime"] == 40
    assert data["series"] == "Series A"
    assert data["tags"] == ["Tag A"]
    assert len(gallery) == 5
    assert gallery[0].endswith("/images/l/001.jpg")


def test_runtime_rounding() -> None:
    assert _runtime_minutes("00:40:28") == 40
    assert _runtime_minutes("01:01:40") == 62
    assert _runtime_minutes("bad") is None


class _FakeClient:
    async def get_html(self, url: str, **kwargs: object) -> str:
        assert kwargs.get("encoding") == "euc_jp"
        return '''<h1 itemprop="name">Test title</h1><p itemprop="description">Test plot</p>
        <ul><li><span class="spec-title">出演</span><span class="spec-content"><a class="spec__tag"><span>Actor A</span></a></span></li>
        <li><span class="spec-title">配信日</span><span itemprop="uploadDate" class="spec-content">2026/11/05</span></li>
        <li><span class="spec-title">再生時間</span><span class="spec-content"><span>00:30:00</span></span></li>
        <li><span class="spec-title">シリーズ</span><span class="spec-content"><a>Series A</a></span></li>
        <li><span class="spec-title">タグ</span><span class="spec-content"><a class="spec-item">Tag A</a></span></li></ul>
        <a class="gallery-item" data-is_sample="1" href="/moviepages/103126-001/images/l/001.jpg"></a>'''


@pytest.mark.asyncio
async def test_scrape_uses_dedicated_player_poster_and_metadata() -> None:
    crawler = CaribbeancomCrawler(_FakeClient())  # type: ignore[arg-type]
    result = await crawler._scrape("https://www.caribbeancom.com/moviepages/103126-001/index.html")
    assert result is not None
    poster = "https://www.caribbeancom.com/moviepages/103126-001/images/l_l.jpg"
    assert result.poster_urls == [poster]
    assert result.thumb_urls == [poster]
    assert result.number == "103126-001"
    assert result.studio == "カリビアンコム"
    assert result.publisher == "Caribbeancom"
    assert result.actors[0].name == "Actor A"
    assert result.runtime == 30
    assert result.extrafanart == ["https://www.caribbeancom.com/moviepages/103126-001/images/l/001.jpg"]


@pytest.mark.asyncio
async def test_connectivity_probes_actual_detail_page() -> None:
    from amane.net.connectivity import ConnectivityOutcome

    class Web:
        async def request(self, method: str, url: str, **kwargs: object):
            assert method == "GET"
            assert url == "https://www.caribbeancom.com/moviepages/072926-001/index.html"
            assert kwargs["max_attempts"] == 1

            class Response:
                status_code = 200
                text = "<html><h1>Movie detail</h1></html>"

            return Response()

    class Client:
        web_client = Web()
        cookies = {}
        headers = {}

    crawler = CaribbeancomCrawler(Client())  # type: ignore[arg-type]
    outcome = await crawler.check_connectivity()
    assert isinstance(outcome, ConnectivityOutcome)
    assert outcome.status.value == "ok"
