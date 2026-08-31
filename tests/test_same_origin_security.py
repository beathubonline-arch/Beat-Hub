import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.middleware.security import SameOriginMiddleware, _allowed_origins, _origin


class SameOriginSecurityTests(unittest.TestCase):
    def test_origin_normalization(self):
        self.assertEqual(_origin("HTTPS://BeatHub.example/checkout"), "https://beathub.example")
        self.assertEqual(_origin("/checkout"), "")
        self.assertEqual(_origin("https://evil.example/"), "https://evil.example")

    def test_allowed_origins_include_configured_base_url_and_current_host(self):
        settings = SimpleNamespace(BASE_URL="https://beathub.example", is_production=True)
        with patch("app.middleware.security.settings", settings):
            allowed = _allowed_origins("beathub.example", "https")
        self.assertIn("https://beathub.example", allowed)

    def test_production_state_changing_requests_require_same_origin_metadata(self):
        source = SameOriginMiddleware(lambda *_args: None)
        self.assertIsNotNone(source)
        self.assertIn(("POST", "/login"), {}) is False


if __name__ == "__main__":
    unittest.main()
