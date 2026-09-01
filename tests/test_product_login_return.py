import ast
import asyncio
from pathlib import Path
import unittest

from app.middleware.security import RETURN_TO_COOKIE, SameOriginMiddleware, _safe_return_path
from app.routers.auth import _safe_next_url

ROOT = Path(__file__).resolve().parents[1]


async def _run_middleware(method, path, headers=None, query_string=b"", downstream_location=None):
    sent = []

    async def downstream(scope, receive, send):
        await send({
            "type": "http.response.start",
            "status": 303 if downstream_location else 200,
            "headers": [(b"location", downstream_location.encode())] if downstream_location else [(b"content-type", b"text/html")],
        })
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    middleware = SameOriginMiddleware(downstream)
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query_string,
        "headers": headers or [],
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return sent


class ProductLoginReturnTests(unittest.TestCase):
    def test_safe_next_url_allows_internal_product_destinations(self):
        destinations = ["/checkout/track/sample-beat", "/track/sample-track", "/merch/sample-shirt"]
        for destination in destinations:
            self.assertEqual(_safe_next_url(destination), destination)

    def test_safe_next_url_rejects_external_redirects(self):
        for destination in ["https://evil.example/steal", "//evil.example/steal", "javascript:alert(1)", "evil.example/steal"]:
            self.assertEqual(_safe_next_url(destination), "")

    def test_middleware_safe_return_path_rejects_external_destinations(self):
        self.assertEqual(_safe_return_path("/checkout/track/sample"), "/checkout/track/sample")
        self.assertEqual(_safe_return_path("https://evil.example/steal"), "")
        self.assertEqual(_safe_return_path("//evil.example/steal"), "")

    def test_login_page_supplies_encoded_next_url_for_template(self):
        source = (ROOT / "app" / "routers" / "auth.py").read_text(encoding="utf-8")
        self.assertIn('"next_url": quote(safe_next, safe="")', source)
        self.assertIn('url=safe_next or dashboard_url_for_user(user)', source)

    def test_login_template_preserves_next_in_post_action(self):
        source = (ROOT / "app" / "templates" / "login.html").read_text(encoding="utf-8")
        self.assertIn('{% if next_url %}?next={{ next_url|urlencode }}{% endif %}', source)

    def test_product_entry_points_preserve_the_correct_destination(self):
        checkout = (ROOT / "app" / "routers" / "checkout.py").read_text(encoding="utf-8")
        merch = (ROOT / "app" / "templates" / "merchandise_public.html").read_text(encoding="utf-8")
        self.assertIn('next_url = f"/checkout/track/{quote(slug, safe=\'\')}"', checkout)
        self.assertIn('quote(slug, safe=\'\')', checkout)
        self.assertIn('/login?next=/merch/{{ product.slug }}', merch)

    def test_login_sets_short_lived_return_cookie(self):
        sent = asyncio.run(_run_middleware("GET", "/login", query_string=b"next=%2Fcheckout%2Ftrack%2Fsample-beat"))
        start = sent[0]
        cookies = [value for key, value in start["headers"] if key.lower() == b"set-cookie"]
        self.assertEqual(len(cookies), 1)
        self.assertIn(f"{RETURN_TO_COOKIE}=", cookies[0].decode())
        self.assertIn("Max-Age=600", cookies[0].decode())
        self.assertIn("HttpOnly", cookies[0].decode())

    def test_signup_reuses_return_cookie_and_clears_it(self):
        login_response = asyncio.run(_run_middleware("GET", "/login", query_string=b"next=%2Fmerch%2Fsample-shirt"))
        login_cookie = next(value for key, value in login_response[0]["headers"] if key.lower() == b"set-cookie")
        cookie_pair = login_cookie.decode().split(";", 1)[0]

        signup_response = asyncio.run(
            _run_middleware(
                "POST",
                "/signup",
                headers=[(b"cookie", cookie_pair.encode())],
                downstream_location="/dashboard?success=Account%20created",
            )
        )
        start = signup_response[0]
        location = next(value.decode() for key, value in start["headers"] if key.lower() == b"location")
        cookies = [value.decode() for key, value in start["headers"] if key.lower() == b"set-cookie"]

        self.assertEqual(location, "/merch/sample-shirt")
        self.assertTrue(any(f"{RETURN_TO_COOKIE}=" in value and "Max-Age=0" in value for value in cookies))

    def test_signup_return_flow_works_for_track_checkout_too(self):
        login_response = asyncio.run(_run_middleware("GET", "/login", query_string=b"next=%2Fcheckout%2Ftrack%2Fmidnight-track"))
        login_cookie = next(value for key, value in login_response[0]["headers"] if key.lower() == b"set-cookie")
        cookie_pair = login_cookie.decode().split(";", 1)[0]
        signup_response = asyncio.run(
            _run_middleware(
                "POST",
                "/signup",
                headers=[(b"cookie", cookie_pair.encode())],
                downstream_location="/dashboard?success=Account%20created",
            )
        )
        location = next(value.decode() for key, value in signup_response[0]["headers"] if key.lower() == b"location")
        self.assertEqual(location, "/checkout/track/midnight-track")

    def test_auth_module_compiles(self):
        source = (ROOT / "app" / "routers" / "auth.py").read_text(encoding="utf-8")
        ast.parse(source)


if __name__ == "__main__":
    unittest.main()
