import pytest

from amane.crawlers import registry
from amane.crawlers.sites.caribbeancompr import (
    CaribbeancomprCrawler,
    _clean_search_title,
    _detail,
    _javbus_title_release,
    _number,
    _search_candidates,
    _split_title,
)
from amane.enums import SiteName


def test_caribbeancompr_registered_and_number_is_full_uppercase() -> None:
    assert registry.get(SiteName.CARIBBEANCOMPR) is CaribbeancomprCrawler
    assert _number("HEYZO-1234") == "HEYZO-1234"
    assert _number("heyzo-1234") == "HEYZO-1234"
    assert _number("1234") is None
    assert _number("HEYZO-1234-extra") is None


def test_title_body_and_volume_normalization() -> None:
    assert _split_title("HEYZO-2766激烈ハードに3穴責め！！Vol.2", "HEYZO-2766") == ("激烈ハードに3穴責め！！", 2)
    assert _split_title("激烈ハードに3穴責め！！VOL.02") == ("激烈ハードに3穴責め！！", 2)
    assert _split_title("激烈ハードに3穴責め！！") == ("激烈ハードに3穴責め！！", None)


def test_search_candidates_reads_title_anchor_itself() -> None:
    html = """<a class="meta-title" href="/moviepages/122122_701/index.html">激烈ハードに3穴責め！！Vol.2</a>"""
    assert _search_candidates(html, "https://www.caribbeancompr.com/search/?q=x") == [
        ("https://www.caribbeancompr.com/moviepages/122122_701/index.html", "激烈ハードに3穴責め！！Vol.2")
    ]


def test_javbus_title_and_release_parser() -> None:
    title, release = _javbus_title_release(
        '<h3>HEYZO-1234 激烈ハードに3穴責め！！Vol.2</h3><p><span class="header">發行日期:</span> 2026-07-01</p>'
    )
    assert title == "HEYZO-1234 激烈ハードに3穴責め！！Vol.2"
    assert release == "2026-07-01"


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
async def test_scrape_uses_javbus_release_and_fixed_heyzo_labels() -> None:
    crawler = CaribbeancomprCrawler(_FakeClient())  # type: ignore[arg-type]
    detail_url = "https://www.caribbeancompr.com/moviepages/122122_701/index.html"
    crawler._lookup_cache["HEYZO-1234"] = ("HEYZO-1234 Japanese title Vol.2", "2026-06-30", detail_url, 2)
    result = await crawler._scrape(detail_url)
    assert result is not None
    assert result.number == "HEYZO-1234"
    assert result.title == "Premium title Vol.2"
    assert result.release == "2026-06-30"
    assert result.studio == "HEYZO"
    assert result.publisher == "HEYZO"
    assert result.runtime == 61
    assert result.original_poster_urls is None
    assert len(result.extrafanart) == 3
    assert all("/member/" not in url for url in result.extrafanart)


def test_caribbeancompr_search_title_removes_noise_and_hyphens_only_for_query() -> None:
    assert _clean_search_title("アダルト動画 HEYZO-1234 - 激しい-作品") == "1234  激しい作品".replace("  ", " ")
    assert _clean_search_title("アダルト動画HEYZO 激しい作品") == "激しい作品"


def test_caribbeancompr_actor_text_is_grouped_per_link() -> None:
    html = """<div class="movie-info"><h1>Test</h1><p>Plot</p></div>
    <ul><li class="movie-spec"><span class="spec-title">出演</span><span class="spec-content">
    <a href="/actor/1"><span>Actor</span> One</a>
    <a href="/actor/2"><span>Actor</span> Two</a>
    </span></li></ul>"""
    data, _, _ = _detail(html, "https://www.caribbeancompr.com/moviepages/122122_701/index.html")
    assert data["actors"] == ["Actor One", "Actor Two"]
