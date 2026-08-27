import ast
from pathlib import Path
import unittest

from app.models.music import AlbumContentType, TrackContentType
from app.routers import album, beat_catalog, music_publish


ROOT = Path(__file__).resolve().parents[1]


class MusicContentTypeTests(unittest.TestCase):
    def test_track_content_types_are_explicit(self):
        self.assertEqual(TrackContentType.BEAT.value, "beat")
        self.assertEqual(TrackContentType.TRACK.value, "track")

    def test_album_content_types_are_explicit(self):
        self.assertEqual(AlbumContentType.BEAT_COLLECTION.value, "beat_collection")
        self.assertEqual(AlbumContentType.ALBUM.value, "album")

    def test_upload_endpoint_requires_content_type(self):
        route = next(
            route for route in music_publish.router.routes
            if getattr(route, "path", "") == "/dashboard/upload"
        )
        dependency_names = {
            parameter.name
            for parameter in route.dependant.body_params
        }
        self.assertIn("content_types", dependency_names)

    def test_album_creation_endpoint_requires_project_type(self):
        route = next(
            route for route in album.router.routes
            if getattr(route, "path", "") == "/dashboard/albums/new"
            and "POST" in getattr(route, "methods", set())
        )
        dependency_names = {
            parameter.name
            for parameter in route.dependant.body_params
        }
        self.assertIn("content_type", dependency_names)

    def test_public_beats_route_is_classification_aware(self):
        paths = [getattr(route, "path", "") for route in beat_catalog.router.routes]
        self.assertIn("/beats", paths)
        source = (ROOT / "app/routers/beat_catalog.py").read_text(encoding="utf-8")
        self.assertIn('content_type", "beat"', source)

    def test_store_template_has_separate_sections(self):
        source = (ROOT / "app/templates/creator_store.html").read_text(encoding="utf-8")
        self.assertIn("🎧 Beats", source)
        self.assertIn("🎵 Tracks / Songs", source)
        self.assertIn("Beat Collection", source)
        self.assertIn("Album / EP", source)

    def test_upload_template_submits_content_type_per_item(self):
        source = (ROOT / "app/templates/upload_track.html").read_text(encoding="utf-8")
        self.assertIn('name="content_types"', source)
        self.assertIn('value="beat"', source)
        self.assertIn('value="track"', source)
        self.assertIn("content_type_radio_", source)

    def test_album_template_filters_tracks_by_project_type(self):
        source = (ROOT / "app/templates/upload_album.html").read_text(encoding="utf-8")
        self.assertIn('name="content_type"', source)
        self.assertIn('value="album"', source)
        self.assertIn('value="beat_collection"', source)
        self.assertIn("data-type", source)

    def test_python_files_compile_as_ast(self):
        paths = [
            ROOT / "app/models/music.py",
            ROOT / "app/routers/music_publish.py",
            ROOT / "app/routers/beat_catalog.py",
            ROOT / "app/routers/album.py",
        ]
        for path in paths:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


if __name__ == "__main__":
    unittest.main()
