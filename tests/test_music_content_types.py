import ast
from pathlib import Path
import unittest

from app.models.music import AlbumContentType, TrackContentType
from app.routers import album, beat_catalog, marketplace, music_publish

ROOT = Path(__file__).resolve().parents[1]

class MusicContentTypeTests(unittest.TestCase):
    def test_track_content_types_are_explicit(self):
        self.assertEqual(TrackContentType.BEAT.value, "beat")
        self.assertEqual(TrackContentType.TRACK.value, "track")

    def test_album_content_types_are_explicit(self):
        self.assertEqual(AlbumContentType.BEAT_COLLECTION.value, "beat_collection")
        self.assertEqual(AlbumContentType.ALBUM.value, "album")

    def test_upload_endpoint_requires_content_type(self):
        source = (ROOT / "app/routers/music_publish.py").read_text(encoding="utf-8")
        self.assertIn("content_types", source)
        upload = (ROOT / "app/templates/upload_track.html").read_text(encoding="utf-8")
        self.assertIn('data-field="content_type"', upload)
        self.assertIn('data-kind="beat"', upload)
        self.assertIn('data-kind="track"', upload)
        self.assertIn('value="beat"', upload)
        self.assertIn('value="track"', upload)
        self.assertIn('content_type:r.querySelector', upload)

    def test_upload_router_is_registered_before_legacy_dashboard_router(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        publish_pos = source.index("app.include_router(music_publish.router)")
        dashboard_pos = source.index("app.include_router(dashboard.router)")
        self.assertLess(publish_pos, dashboard_pos)

    def test_album_creation_endpoint_requires_project_type(self):
        route = next(route for route in album.router.routes if getattr(route, "path", "") == "/dashboard/albums/new" and "POST" in getattr(route, "methods", set()))
        dependency_names = {parameter.name for parameter in route.dependant.body_params}
        self.assertIn("content_type", dependency_names)

    def test_public_marketplace_is_canonical_entry(self):
        paths = [getattr(route, "path", "") for route in marketplace.router.routes]
        self.assertIn("/marketplace", paths)
        self.assertIn("/marketplace/producers", paths)
        self.assertIn("/marketplace/albums", paths)
        self.assertIn("/marketplace/merch", paths)
        self.assertIn("/beats", paths)

    def test_dedicated_beat_catalog_is_classification_aware(self):
        paths = [getattr(route, "path", "") for route in beat_catalog.router.routes]
        self.assertIn("/marketplace/beats", paths)
        source = (ROOT / "app/routers/beat_catalog.py").read_text(encoding="utf-8")
        self.assertIn('getattr(Track, "content_type", "beat")', source)
        self.assertIn('_is_beat(track)', source)

    def test_store_template_has_separate_sections(self):
        source = (ROOT / "app/templates/creator_store.html").read_text(encoding="utf-8")
        self.assertIn("🎧 Beats", source)
        self.assertIn("🎵 Tracks / Songs", source)
        self.assertIn("Beat Collection", source)
        self.assertIn("Album / EP", source)

    def test_upload_template_submits_content_type_per_item(self):
        source = (ROOT / "app/templates/upload_track.html").read_text(encoding="utf-8")
        self.assertIn('data-field="content_type"', source)
        self.assertIn('data-kind="beat"', source)
        self.assertIn('data-kind="track"', source)
        self.assertIn("content_type:r.querySelector", source)
        self.assertIn("r.onchange=()=>sel.value=r.dataset.kind", source)

    def test_album_template_filters_tracks_by_project_type(self):
        source = (ROOT / "app/templates/upload_album.html").read_text(encoding="utf-8")
        self.assertIn('name="content_type"', source)
        self.assertIn('value="album"', source)
        self.assertIn('value="beat_collection"', source)
        self.assertIn("data-type", source)

    def test_marketplace_template_has_required_discovery_sections(self):
        source = (ROOT / "app/templates/marketplace.html").read_text(encoding="utf-8")
        for expected in ("Hot picks", "Featured producers", "Tracks worth hearing", "Creator Tees", "/marketplace/beats", "/tracks", "/marketplace/albums", "/merch", "/marketplace/producers"):
            self.assertIn(expected, source)

    def test_python_files_compile_as_ast(self):
        paths = [ROOT / "main.py", ROOT / "app/models/music.py", ROOT / "app/routers/marketplace.py", ROOT / "app/routers/music_publish.py", ROOT / "app/routers/beat_catalog.py", ROOT / "app/routers/album.py"]
        for path in paths:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

if __name__ == "__main__":
    unittest.main()
