import ast
from pathlib import Path
import unittest

from app.models.music import TrackContentType
from app.routers import track_catalog

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

    def test_python_files_parse(self):
        for relative in (
            "app/routers/track_catalog.py",
            "app/routers/checkout.py",
            "main.py",
        ):
            path = ROOT / relative
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


if __name__ == "__main__":
    unittest.main()
