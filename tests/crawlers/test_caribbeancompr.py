import pytest

from amane.crawlers import registry
from amane.crawlers.sites.caribbeancompr import (
    CaribbeancomprCrawler,
    _clean_search_title,
    _cluster_search_term,
    _detail,
    _javbus_title_release,
    _number,
    _search_candidates,
    _search_query,
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
    # javbus 侧把 Vol 写成 Vo, 且标题以「艺人名【读音】」开头; 站点页写全 Vol 且不含艺人名.
    # 两侧必须归一成同一个主体与序号, 否则比对必然失败 —— 剥掉艺人前缀正是为此.
    javbus_title = "姫川ゆうな【ひめかわゆうな】 メイどーる Vo.4~ご主人様のいいなり性人形~"
    site_title = "メイどーる Vol.4~ご主人様のいいなり性人形~"
    assert _split_title(javbus_title) == _split_title(site_title) == ("メイどーる ~ご主人様のいいなり性人形~", 4)
    # 无点的写法同样要认.
    assert _split_title("メイどーる Vol12 何か") == ("メイどーる 何か", 12)


def test_search_title_normalizes_volume_spelling() -> None:
    """发往搜索框的序号统一成站点写法 ``Vol.``; javbus 侧常写作 ``Vo.``."""
    assert _clean_search_title("メイどーる Vo.4~ご主人様のいいなり性人形~") == (
        "メイどーる Vol.4~ご主人様のいいなり性人形~"
    )
    assert _clean_search_title("メイどーる vol.4 x") == "メイどーる Vol.4 x"
    assert _clean_search_title("メイどーる VOL.4 x") == "メイどーる Vol.4 x"
    # 已经是全写的保持不变.
    assert _clean_search_title("メイどーる Vol.18 x") == "メイどーる Vol.18 x"
    # 站点数据用半角 ~, 全角 ～ 要归一.
    assert _clean_search_title("x Vol.4～副題～") == "x Vol.4~副題~"


def test_search_query_encodes_tilde() -> None:
    """``~`` 必须编成 ``%A1%C1``: 不编码时站点按模糊匹配, 同系列全部分卷一起返回."""
    encoded = _search_query("Vol.4~x~")
    assert "%A1%C1" in encoded
    assert "~" not in encoded


def test_cluster_search_term_keeps_series_name() -> None:
    """搜索词保留系列名: 站点按它划索引范围, 去掉后候选集残缺."""
    subject, vol = _split_title(
        "姫川ゆうな【ひめかわゆうな】 メイどーる Vo.4～ご主人様のいいなり性人形～", "HEYZO-1395"
    )
    assert vol == 4
    # 序号不进搜索词, 系列名保留.
    assert _cluster_search_term(subject, vol) == "メイどーる ~ご主人様のいいなり性人形~"


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
