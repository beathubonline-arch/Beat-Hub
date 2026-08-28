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

    def test_canonical_marketplace_routes_exist(self):
        routes = [getattr(route, "path", "") for route in marketplace.router.routes]
        self.assertIn("/marketplace", routes)
        self.assertIn("/beats", routes)

    def test_marketplace_landing_has_required_sections_and_producer_flow(self):
        source = (ROOT / "app/templates/marketplace.html").read_text(encoding="utf-8")
        self.assertIn("Beat Producers", source)
        self.assertIn("Tracks / Songs", source)
        self.assertIn("Creator Merchandise", source)
        self.assertIn("producer.store_url", source)
        self.assertIn("/tracks", source)
        self.assertIn("/merch", source)
        self.assertIn("Browse all beats", source)

    def test_marketplace_backend_builds_producer_cards_from_beats(self):
        source = (ROOT / "app/routers/marketplace.py").read_text(encoding="utf-8")
        self.assertIn("_producer_cards", source)
        self.assertIn("content_type", source)
        self.assertIn('== "beat"', source)
        self.assertIn('== "track"', source)
        self.assertIn("beathub_merchandise", source)
        self.assertIn("/store/", source)

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
