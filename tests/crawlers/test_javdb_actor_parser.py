from amane.crawlers.actor.sites.javdb import parse_javdb_actor_detail


def test_javdb_actor_section_does_not_join_distinct_actor_links() -> None:
    html = """\
    <html><head><link rel="canonical" href="https://javdb.com/actors/abc" /></head>
    <body>
      <span class="actor-section-name">
        <a href="/actors/abc">田边莉子</a>
        <a href="/actors/def">神力カラル</a>
        <a href="/actors/ghi">ありさ</a>
      </span>
      <span class="section-meta">女优</span>
    </body></html>
    """
    result = parse_javdb_actor_detail(html, page_url="https://javdb.com/actors/abc", base_url="https://javdb.com")
    assert result is not None
    assert result.name == "田边莉子"
    assert result.aliases == []


def test_javdb_actor_section_keeps_single_name_with_comma_aliases() -> None:
    html = """\
    <html><head><link rel="canonical" href="https://javdb.com/actors/abc" /></head>
    <body>
      <span class="actor-section-name">田边莉子, Riko Tanabe</span>
      <span class="section-meta">女优</span>
    </body></html>
    """
    result = parse_javdb_actor_detail(html, page_url="https://javdb.com/actors/abc", base_url="https://javdb.com")
    assert result is not None
    assert result.name == "田边莉子"
    assert result.aliases == ["Riko Tanabe"]


def test_javdb_actor_section_splits_japanese_list_separator() -> None:
    html = """\
    <html><head><link rel="canonical" href="https://javdb.com/actors/abc" /></head>
    <body>
      <span class="actor-section-name">田边莉子、Riko Tanabe</span>
      <span class="section-meta">女优</span>
    </body></html>
    """
    result = parse_javdb_actor_detail(html, page_url="https://javdb.com/actors/abc", base_url="https://javdb.com")
    assert result is not None
    assert result.name == "田边莉子"
    assert result.aliases == ["Riko Tanabe"]


def test_javdb_actor_canonical_and_avatar_use_configured_mirror_origin() -> None:
    html = """\\
    <html><head><link rel="canonical" href="https://javdb.com/actors/abc?locale=zh" /></head>
    <body>
      <span class="actor-section-name">Riko Tanabe</span>
      <div class="actor-avatar"><span class="avatar" style="background-image: url(https://javdb.com/actors/abc.jpg)"></span></div>
      <span class="section-meta">女优</span>
    </body></html>
    """
    result = parse_javdb_actor_detail(
        html,
        page_url="https://mirror.example/actors/abc",
        base_url="https://mirror.example",
    )
    assert result is not None
    assert result.source_url == "https://mirror.example/actors/abc?locale=zh"
    assert result.image_urls == ["https://mirror.example/actors/abc.jpg"]
