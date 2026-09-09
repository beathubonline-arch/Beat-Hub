from app.services.growth_acquisition_v6 import _host_allowed, _parse_results, _unwrap_ddg_url


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
