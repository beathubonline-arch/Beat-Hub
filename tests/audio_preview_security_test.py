import tempfile
import unittest
from pathlib import Path

import soundfile as sf

from app.routers.audio_preview import (
    PREVIEW_SECONDS,
    _generate_preview_bytes,
    _make_preview_from_source,
    _safe_preview_path,
)


class AudioPreviewSecurityTests(unittest.TestCase):
    def test_external_sources_are_never_fetched_for_preview_generation(self):
        with self.assertRaises(RuntimeError):
            _generate_preview_bytes("https://example.invalid/master.mp3")

    def test_preview_path_only_accepts_preview_storage(self):
        self.assertTrue(_safe_preview_path("previews/example.mp3"))
        self.assertTrue(_safe_preview_path("media/previews/example.mp3"))
        self.assertFalse(_safe_preview_path("audio/master.mp3"))
        self.assertFalse(_safe_preview_path("media/audio/master.mp3"))
        self.assertFalse(_safe_preview_path("https://example.invalid/master.mp3"))

    def test_generated_preview_is_bounded_and_is_not_the_master(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "master.wav"
            # Five seconds is enough to verify conversion while keeping CI fast.
            import numpy as np

            samples = np.zeros(22050 * 5, dtype="float32")
            sf.write(source, samples, 22050, format="WAV", subtype="PCM_16")

            preview = _make_preview_from_source(source)
            self.assertGreater(len(preview), 0)
            self.assertLess(len(preview), 8 * 1024 * 1024)

            preview_path = Path(tmp) / "preview.mp3"
            preview_path.write_bytes(preview)
            info = sf.info(preview_path)
            self.assertLessEqual(info.duration, PREVIEW_SECONDS + 1)
            self.assertEqual(info.channels, 1)

    def test_secure_preview_route_is_registered_before_legacy_music_route(self):
        from main import app

        preview_routes = [
            route
            for route in app.routes
            if getattr(route, "path", None) == "/track/{slug}/preview"
        ]
        self.assertGreaterEqual(len(preview_routes), 2)
        self.assertEqual(
            getattr(preview_routes[0].endpoint, "__name__", ""),
            "secure_track_preview",
        )


if __name__ == "__main__":
    unittest.main()
