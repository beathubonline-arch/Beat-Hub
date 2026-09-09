from app.services.growth_acquisition_v6 import (
    _host_allowed,
    _parse_bing_results,
    _parse_lite_results,
    _parse_results,
    _unwrap_ddg_url,
)


def test_unwraps_duckduckgo_result_url():
    raw = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.instagram.com%2Fexampleartist%2F"
    assert _unwrap_ddg_url(raw) == "https://www.instagram.com/exampleartist/"


def test_allows_creator_profiles_and_blocks_search_pages():
    assert _host_allowed("https://www.instagram.com/exampleartist")
    assert _host_allowed("https://www.tiktok.com/@exampleartist")
    assert _host_allowed("https://www.youtube.com/@exampleartist")
    assert not _host_allowed("https://www.instagram.com/explore/tags/music")
    assert not _host_allowed("https://www.youtube.com/results?search_query=artist")
    assert not _host_allowed("https://example.com/private-profile")


def test_parser_returns_only_public_music_profiles():
    markup = '''
    <div class="result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.instagram.com%2Fnellyartist%2F">Nelly Artist - Instagram</a>
      <a class="result__snippet">Kenya independent artist, rapper, new music 2026.</a>
    </div>
    <div class="result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fsecret">Private result</a>
      <a class="result__snippet">not a supported public creator platform</a>
    </div>
    '''
    rows = _parse_results(markup, "Kenya")
    assert len(rows) == 1
    assert rows[0]["platform"] == "instagram"
    assert rows[0]["fit_score"] >= 45
    assert "Kenya" in rows[0]["location"]


def test_bing_fallback_parser_returns_creator_profile():
    markup = '''
    <ol id="b_results">
      <li class="b_algo">
        <h2><a href="https://www.tiktok.com/@testartist">Test Artist | TikTok</a></h2>
        <div class="b_caption"><p>Kenya singer and independent artist releasing new music in 2026.</p></div>
      </li>
    </ol>
    '''
    rows = _parse_bing_results(markup, "Kenya")
    assert len(rows) == 1
    assert rows[0]["platform"] == "tiktok"
    assert rows[0]["fit_score"] >= 45


def test_lite_fallback_filters_unsupported_urls():
    markup = '''
      <a href="https://www.youtube.com/@publicartist">Public Artist</a>
      <a href="https://example.com/private">Unsupported</a>
    '''
    rows = _parse_lite_results(markup, "Kenya")
    assert len(rows) == 1
    assert rows[0]["platform"] == "youtube"
