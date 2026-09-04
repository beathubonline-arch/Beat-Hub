import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.middleware.security import (
    RETURN_TO_COOKIE,
    RETURN_TO_MAX_AGE,
    SameOriginMiddleware,
    _location_with_next,
    _safe_return_path,
)


class SignupReturnToProductTests(unittest.TestCase):
    PRODUCT = "/checkout/track/my-beat?license=basic"

    def test_safe_return_path_accepts_product_checkout_and_rejects_open_redirects(self):
        self.assertEqual(_safe_return_path(self.PRODUCT), self.PRODUCT)
        self.assertEqual(_safe_return_path("https://evil.example/steal"), "")
        self.assertEqual(_safe_return_path("//evil.example/steal"), "")

    def test_verification_redirect_keeps_exact_product_target(self):
        location = "/verify-email?email=buyer%40example.com&success=Code%20sent"
        result = _location_with_next(location, self.PRODUCT)
        self.assertIn("/verify-email?", result)
        self.assertIn("next=%2Fcheckout%2Ftrack%2Fmy-beat%3Flicense%3Dbasic", result)

    def test_signup_redirect_preserves_cookie_until_verification_finishes(self):
        sent = []

        async def app(scope, receive, send):
            await send(
                {
                    "type": "http.response.start",
                    "status": 303,
                    "headers": [
                        (
                            b"location",
                            b"/verify-email?email=buyer%40example.com&success=Code%20sent",
                        )
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b""})

        middleware = SameOriginMiddleware(app)
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/signup",
            "headers": [
                (
                    b"cookie",
                    f"{RETURN_TO_COOKIE}={self.PRODUCT}".encode("latin-1"),
                )
            ],
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        with patch("app.middleware.security.settings", SimpleNamespace(is_production=True)):
            asyncio.run(middleware(scope, receive, send))

        start = sent[0]
        headers = dict(start["headers"])
        location = headers[b"location"].decode("latin-1")
        self.assertEqual(start["status"], 303)
        self.assertIn("/verify-email?", location)
        self.assertIn("next=%2Fcheckout%2Ftrack%2Fmy-beat%3Flicense%3Dbasic", location)
        set_cookie_values = [
            value.decode("latin-1")
            for key, value in start["headers"]
            if key.lower() == b"set-cookie"
        ]
        self.assertTrue(set_cookie_values)
        self.assertTrue(any(f"Max-Age={RETURN_TO_MAX_AGE}" in value for value in set_cookie_values))
        self.assertFalse(any("Max-Age=0" in value for value in set_cookie_values))

    def test_signup_validation_error_keeps_product_target_for_retry(self):
        sent = []

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 400, "headers": []})
            await send({"type": "http.response.body", "body": b"form"})

        middleware = SameOriginMiddleware(app)
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/signup",
            "headers": [(b"cookie", f"{RETURN_TO_COOKIE}={self.PRODUCT}".encode("latin-1"))],
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        with patch("app.middleware.security.settings", SimpleNamespace(is_production=True)):
            asyncio.run(middleware(scope, receive, send))

        self.assertEqual(sent[0]["status"], 400)
        set_cookie_values = [
            value.decode("latin-1")
            for key, value in sent[0]["headers"]
            if key.lower() == b"set-cookie"
        ]
        self.assertTrue(any(f"Max-Age={RETURN_TO_MAX_AGE}" in value for value in set_cookie_values))
        self.assertFalse(any("Max-Age=0" in value for value in set_cookie_values))


if __name__ == "__main__":
    unittest.main()
