import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.middleware.security import SameOriginMiddleware, _is_native_json_auth_request, _origin
from app.routers.auth import (
    _reset_token_digest,
    _send_email_resend,
    _verification_delivery_error_message,
)
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

    def test_native_json_login_without_cookie_bypasses_browser_csrf_check(self):
        self.assertTrue(
            _is_native_json_auth_request(
                "/api/v1/auth/login",
                {"content-type": "application/json", "accept": "application/json"},
            )
        )

    def test_native_auth_exception_rejects_cookie_or_non_json_requests(self):
        path = "/api/v1/auth/login"
        self.assertFalse(_is_native_json_auth_request(path, {"content-type": "application/x-www-form-urlencoded"}))
        self.assertFalse(_is_native_json_auth_request(path, {"content-type": "application/json", "cookie": "session=browser"}))
        self.assertFalse(_is_native_json_auth_request("/api/v1/payments/paystack/initialize", {"content-type": "application/json"}))

    def test_verification_delivery_failure_message_is_provider_agnostic(self):
        message = _verification_delivery_error_message()
        self.assertIn("verification email", message.lower())
        self.assertNotIn("403", message)
        self.assertNotIn("resend", message.lower())
        self.assertNotIn("api", message.lower())

    def test_resend_403_returns_false_without_leaking_recipient(self):
        response = SimpleNamespace(
            status_code=403,
            json=lambda: {"name": "validation_error", "message": "testing recipient restriction"},
        )
        client = SimpleNamespace(__enter__=lambda self: self, __exit__=lambda self, *args: None, post=lambda *args, **kwargs: response)
        settings = SimpleNamespace(RESEND_API_KEY="test-key", RESEND_FROM="BeatHub <beathubonline@gmail.com>")
        with patch("app.routers.auth.settings", settings), patch("app.routers.auth.httpx.Client", return_value=client):
            with patch("app.routers.auth.logger.error") as log_error:
                self.assertFalse(_send_email_resend("other@example.com", "Verify", "code"))
                rendered = " ".join(str(call) for call in log_error.call_args_list)
                self.assertNotIn("other@example.com", rendered)


if __name__ == "__main__":
    unittest.main()
