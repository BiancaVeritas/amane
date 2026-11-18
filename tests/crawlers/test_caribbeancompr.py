import pytest

from amane.crawlers import registry
from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.caribbeancompr import CaribbeancomprCrawler, _detail, _number
from amane.enums import SiteName


def test_caribbeancompr_registered_and_number_is_full_uppercase() -> None:
    assert registry.get(SiteName.CARIBBEANCOMPR) is CaribbeancomprCrawler
    assert _number("HEYZO-1234") == "HEYZO-1234"
    assert _number("heyzo-1234") == "HEYZO-1234"
    assert _number("1234") is None
    assert _number("HEYZO-1234-extra") is None


def test_detail_only_reads_public_gallery_and_required_metadata() -> None:
    html = """
    <div class="movie-info"><div class="heading"><h1>激烈ハードに3穴責め！！Vol.2</h1></div>
    <p>Plot text</p>
    <ul>
      <li class="movie-spec"><span class="spec-title">出演</span><span class="spec-content"><a>Actor A</a></span></li>
      <li class="movie-spec"><span class="spec-title">販売日</span><span class="spec-content">2026-07-01</span></li>
      <li class="movie-spec"><span class="spec-title">再生時間</span><span class="spec-content">01:00:38</span></li>
      <li class="movie-spec"><span class="spec-title">スタジオ</span><span class="spec-content">Wrong Studio</span></li>
      <li class="movie-spec"><span class="spec-title">タグ</span><span class="spec-content"><a class="spec-item">Tag A</a><a class="spec-item">Tag B</a></span></li>
    </ul></div>
    <div class="vjs-poster" style="background-image: url('/moviepages/122122_701/images/l_l.jpg');"></div>
    <a class="gallery-image-wrap" data-is_sample="1" href="/moviepages/122122_701/images/l/001.jpg"></a>
    <a class="gallery-image-wrap" data-is_sample="1" href="/moviepages/122122_701/images/l/002.jpg"></a>
    <a class="gallery-image-wrap" data-is_sample="1" href="/moviepages/122122_701/images/l/003.jpg"></a>
    <a class="gallery-image-wrap is-disabled" data-is_sample="0" href="/member/moviepages/122122_701/images/l/004.jpg"></a>
    """
    data, gallery, poster = _detail(html, "https://www.caribbeancompr.com/moviepages/122122_701/index.html")
    assert data["title"] == "激烈ハードに3穴責め！！Vol.2"
    assert data["plot"] == "Plot text"
    assert data["actors"] == ["Actor A"]
    assert data["runtime"] == 61
    assert data["tags"] == ["Tag A", "Tag B"]
    assert len(gallery) == 3
    assert all("/member/" not in url for url in gallery)
    assert poster == "https://www.caribbeancompr.com/moviepages/122122_701/images/l_l.jpg"


@pytest.mark.asyncio
async def test_search_uses_mapping_only(tmp_path) -> None:
    mapping_path = tmp_path / "caribbeancompr.json"
    mapping_path.write_text('{"1234": {"id": "122122_701"}}', encoding="utf-8")
    crawler = CaribbeancomprCrawler(object(), mapping_path=mapping_path)  # type: ignore[arg-type]
    assert await crawler._search(SearchQuery("HEYZO-1234")) == (
        "https://www.caribbeancompr.com/moviepages/122122_701/index.html"
    )
    assert await crawler._search(SearchQuery("HEYZO-9999")) is None


class _FakeClient:
    async def get_html(self, url: str, **kwargs: object) -> str:
        assert kwargs.get("encoding") == "euc_jp"
        return """
        <div class="movie-info"><div class="heading"><h1>Premium title Vol.2</h1></div><p>Premium plot</p>
        <ul>
          <li class="movie-spec"><span class="spec-title">出演</span><span class="spec-content"><a>Actor A</a></span></li>
          <li class="movie-spec"><span class="spec-title">販売日</span><span class="spec-content">2026-07-01</span></li>
          <li class="movie-spec"><span class="spec-title">再生時間</span><span class="spec-content">01:00:38</span></li>
          <li class="movie-spec"><span class="spec-title">スタジオ</span><span class="spec-content">Not Used</span></li>
          <li class="movie-spec"><span class="spec-title">タグ</span><span class="spec-content"><a class="spec-item">Tag A</a></span></li>
        </ul></div>
        <div class="vjs-poster" style="background-image: url('/moviepages/122122_701/images/l_l.jpg');"></div>
        <a class="gallery-image-wrap" data-is_sample="1" href="/moviepages/122122_701/images/l/001.jpg"></a>
        <a class="gallery-image-wrap" data-is_sample="1" href="/moviepages/122122_701/images/l/002.jpg"></a>
        <a class="gallery-image-wrap" data-is_sample="1" href="/moviepages/122122_701/images/l/003.jpg"></a>
        <a class="gallery-image-wrap is-disabled" data-is_sample="0" href="/member/moviepages/122122_701/images/l/004.jpg"></a>
        """


@pytest.mark.asyncio
async def test_scrape_uses_mapping_and_does_not_invent_release() -> None:
    crawler = CaribbeancomprCrawler(_FakeClient())  # type: ignore[arg-type]
    detail_url = "https://www.caribbeancompr.com/moviepages/122122_701/index.html"
    crawler._lookup_cache["HEYZO-1234"] = detail_url
    result = await crawler._scrape(detail_url)
    assert result is not None
    assert result.number == "HEYZO-1234"
    assert result.title == "Premium title Vol.2"
    assert result.release is None
    assert result.studio == "HEYZO"
    assert result.publisher == "HEYZO"
    assert result.runtime == 61
    assert result.original_poster_urls is None
    assert len(result.extrafanart) == 3
    assert all("/member/" not in url for url in result.extrafanart)


def test_caribbeancompr_actor_text_is_grouped_per_link() -> None:
    html = """<div class="movie-info"><h1>Test</h1><p>Plot</p></div>
    <ul><li class="movie-spec"><span class="spec-title">出演</span><span class="spec-content">
    <a href="/actor/1"><span>Actor</span> One</a>
    <a href="/actor/2"><span>Actor</span> Two</a>
    </span></li></ul>"""
    data, _, _ = _detail(html, "https://www.caribbeancompr.com/moviepages/122122_701/index.html")
    assert data["actors"] == ["Actor One", "Actor Two"]
