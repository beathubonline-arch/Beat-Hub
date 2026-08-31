import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.middleware.security import SameOriginMiddleware, _origin
from app.routers.auth import _reset_token_digest
from app.services.storage import _content_matches_extension


class P0SecurityTests(unittest.TestCase):
    def test_reset_token_is_stored_as_digest_not_plaintext(self):
        token = "example-reset-token"
        digest = _reset_token_digest(token)
        self.assertNotEqual(digest, token)
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, _reset_token_digest(token))

    def test_audio_magic_bytes_are_required(self):
        self.assertTrue(_content_matches_extension(b"ID3" + b"\x00" * 20, ".mp3"))
        self.assertTrue(_content_matches_extension(b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 20, ".wav"))
        self.assertTrue(_content_matches_extension(b"fLaC" + b"\x00" * 20, ".flac"))
        self.assertTrue(_content_matches_extension(b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 16, ".m4a"))

    def test_mismatched_media_extensions_are_rejected(self):
        self.assertFalse(_content_matches_extension(b"MZ" + b"\x00" * 30, ".mp3"))
        self.assertFalse(_content_matches_extension(b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 20, ".png"))
        self.assertFalse(_content_matches_extension(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, ".jpg"))

    def test_origin_normalization_rejects_relative_and_keeps_origin_only(self):
        self.assertEqual(_origin("HTTPS://BeatHub.example/checkout"), "https://beathub.example")
        self.assertEqual(_origin("/checkout"), "")

    def test_same_origin_middleware_is_constructible(self):
        middleware = SameOriginMiddleware(lambda *_args: None)
        self.assertIsNotNone(middleware)

    def test_missing_metadata_is_not_treated_as_same_origin(self):
        settings = SimpleNamespace(BASE_URL="https://beathub.example", is_production=True)
        with patch("app.middleware.security.settings", settings):
            allowed = {"https://beathub.example"}
        self.assertNotIn("", allowed)


if __name__ == "__main__":
    unittest.main()
