import unittest

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


if __name__ == "__main__":
    unittest.main()
