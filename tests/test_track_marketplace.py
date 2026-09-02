import ast
from pathlib import Path
import unittest

from app.models.music import TrackContentType
from app.routers import marketplace, track_catalog

ROOT = Path(__file__).resolve().parents[1]


class TrackMarketplaceTests(unittest.TestCase):
    def test_track_content_type_is_explicit(self):
        self.assertEqual(TrackContentType.TRACK.value, "track")

    def test_tracks_route_exists(self):
        routes = [getattr(route, "path", "") for route in track_catalog.router.routes]
        self.assertIn("/tracks", routes)

    def test_catalog_filters_only_finished_tracks(self):
        source = (ROOT / "app/routers/track_catalog.py").read_text(encoding="utf-8")
        self.assertIn('content_type", "beat")', source)
        self.assertIn('== "track"', source)
        self.assertIn("_track_is_public", source)

    def test_dedicated_beat_catalog_is_under_marketplace(self):
        source = (ROOT / "app/routers/beat_catalog.py").read_text(encoding="utf-8")
        self.assertIn('@router.get("/marketplace/beats")', source)
        self.assertIn('== "beat"', source)
        self.assertIn("_track_is_public", source)

    def test_marketplace_has_discovery_and_category_routes(self):
        routes = [getattr(route, "path", "") for route in marketplace.router.routes]
        for expected in (
            "/marketplace",
            "/marketplace/producers",
            "/marketplace/albums",
            "/marketplace/merch",
            "/beats",
        ):
            self.assertIn(expected, routes)

    def test_marketplace_landing_is_discovery_first(self):
        source = (ROOT / "app/templates/marketplace.html").read_text(encoding="utf-8")
        for expected in (
            "Hot picks",
            "Featured producers",
            "Tracks worth hearing",
            "Creator Tees",
            "Creator Collection",
            "Latest tees",
            "/marketplace/beats",
            "/tracks",
            "/marketplace/albums",
            "/merch",
            "/marketplace/producers",
        ):
            self.assertIn(expected, source)
        self.assertNotIn("One marketplace. Three ways to discover.", source)

    def test_marketplace_backend_keeps_product_types_separate(self):
        source = (ROOT / "app/routers/marketplace.py").read_text(encoding="utf-8")
        self.assertIn("_producer_cards", source)
        self.assertIn("_album_cards", source)
        self.assertIn("content_type", source)
        self.assertIn('== "beat"', source)
        self.assertIn('== "track"', source)
        self.assertIn("beathub_merchandise", source)
        self.assertIn("/store/", source)

    def test_creator_merch_is_grouped_and_singletons_remain_products(self):
        source = (ROOT / "app/routers/marketplace.py").read_text(encoding="utf-8")
        self.assertIn("def _merch_collections", source)
        self.assertIn("len(items) >= 2", source)
        self.assertIn('"merch_collections"', source)
        self.assertIn('"standalone_merch"', source)
        template = (ROOT / "app/templates/marketplace.html").read_text(encoding="utf-8")
        self.assertIn("collection-grid", template)
        self.assertIn("standalone_merch", template)
        self.assertIn("/store/{{", template)

    def test_dedicated_category_templates_exist(self):
        for relative in (
            "app/templates/marketplace_producers.html",
            "app/templates/marketplace_albums.html",
            "app/templates/album_detail.html",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_album_and_producer_pages_have_back_navigation(self):
        producer = (ROOT / "app/templates/marketplace_producers.html").read_text(encoding="utf-8")
        albums = (ROOT / "app/templates/marketplace_albums.html").read_text(encoding="utf-8")
        detail = (ROOT / "app/templates/album_detail.html").read_text(encoding="utf-8")
        self.assertIn("/marketplace", producer)
        self.assertIn("/marketplace", albums)
        self.assertIn("/marketplace/albums", detail)
        self.assertIn("Open producer store", producer)
        self.assertIn("Open release", albums)

    def test_album_detail_uses_one_release_cover_and_no_track_art(self):
        source = (ROOT / "app/templates/album_detail.html").read_text(encoding="utf-8")
        self.assertIn('class="album-art"', source)
        self.assertIn('class="track-row"', source)
        self.assertIn('class="track-list"', source)
        self.assertNotIn("track-mini-art", source)
        self.assertNotIn("at.track.cover_art_url", source)
        self.assertNotIn("at.track.cover_url", source)
        self.assertNotIn("at.track.artwork_url", source)

    def test_album_detail_keeps_tracks_as_vertical_rows(self):
        source = (ROOT / "app/templates/album_detail.html").read_text(encoding="utf-8")
        self.assertIn("/track/{{ track.slug }}", source)
        self.assertIn("{{ track.title }}", source)
        self.assertIn("Inside this release", source)
        self.assertIn("TRACKLIST", source)

    def test_album_detail_route_exists(self):
        source = (ROOT / "app/routers/album.py").read_text(encoding="utf-8")
        self.assertIn('@router.get("/album/{slug}")', source)
        self.assertIn('"album_detail.html"', source)

    def test_main_route_still_exists_and_marketplace_is_reachable_from_legacy_entry(self):
        pages_source = (ROOT / "app/routers/pages.py").read_text(encoding="utf-8")
        marketplace_source = (ROOT / "app/routers/marketplace.py").read_text(encoding="utf-8")
        self.assertIn('@router.get("/")', pages_source)
        self.assertIn('@router.get("/marketplace")', marketplace_source)
        self.assertIn('RedirectResponse(url="/marketplace", status_code=307)', marketplace_source)

    def test_track_marketplace_template_has_purchase_flow(self):
        source = (ROOT / "app/templates/tracks.html").read_text(encoding="utf-8")
        self.assertIn("Buy <span>Tracks.</span>", source)
        self.assertIn("View &amp; Buy", source)
        self.assertIn("/tracks", source)
        self.assertIn("Preview", source)

    def test_track_detail_already_exposes_checkout_and_download(self):
        source = (ROOT / "app/templates/track_detail.html").read_text(encoding="utf-8")
        self.assertIn("/checkout/track/{{ track.slug }}", source)
        self.assertIn("/account/download/{{ track.id }}", source)

    def test_checkout_supports_track_slug(self):
        source = (ROOT / "app/routers/checkout.py").read_text(encoding="utf-8")
        self.assertIn('@router.get("/checkout/track/{slug}")', source)
        self.assertIn("track_is_available", source)

    def test_content_type_migration_exists_and_classifies_artist_rows(self):
        source = (ROOT / "alembic/versions/0014_music_content_types.py").read_text(encoding="utf-8")
        self.assertIn("content_type", source)
        self.assertIn("SET content_type = 'track'", source)
        self.assertIn("p.is_artist", source)

    def test_startup_runs_migrations_before_fastapi(self):
        source = (ROOT / "start.sh").read_text(encoding="utf-8")
        migration_pos = source.index("alembic upgrade head")
        uvicorn_pos = source.index("exec python -m uvicorn")
        self.assertLess(migration_pos, uvicorn_pos)

    def test_python_files_parse(self):
        for relative in (
            "app/models/music.py",
            "app/routers/marketplace.py",
            "app/routers/album.py",
            "app/routers/track_catalog.py",
            "app/routers/beat_catalog.py",
            "app/routers/music_publish.py",
            "app/routers/checkout.py",
            "main.py",
        ):
            path = ROOT / relative
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


if __name__ == "__main__":
    unittest.main()
