"""Small production security middleware that is intentionally route-aware.

BeatHub authenticates browser requests with an HttpOnly cookie. For those
requests, state-changing cross-site requests must be rejected. Provider
webhooks and non-browser bearer-token clients are explicitly left alone.

This middleware also preserves a safe in-site purchase destination when a
visitor goes from a product page -> login -> create account. That flow is
implemented here so the existing checkout, marketplace and authentication
routes do not need to be duplicated or reordered.
"""

from html import escape
from http.cookies import SimpleCookie
from urllib.parse import quote, unquote, urlparse

from fastapi.responses import HTMLResponse, JSONResponse

from app.config import settings


EXEMPT_POST_PATHS = {
    "/paystack/webhook",
    "/paystack/transfer/webhook",
    "/mpesa/callback",
    "/mpesa/b2c/callback",
}

RETURN_TO_COOKIE = "beathub_return_to"
RETURN_TO_MAX_AGE = 10 * 60


def _origin(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlparse(value.strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _allowed_origins(host: str, forwarded_proto: str) -> set[str]:
    allowed: set[str] = set()
    base_origin = _origin(getattr(settings, "BASE_URL", ""))
    if base_origin:
        allowed.add(base_origin)

    if host:
        scheme = forwarded_proto.split(",", 1)[0].strip().lower() or "https"
        allowed.add(f"{scheme}://{host.lower()}")

    return allowed


def _safe_return_path(value: str | None) -> str:
    """Allow only an in-site relative path for post-signup continuation."""
    raw = unquote((value or "").strip())
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc or not raw.startswith("/") or raw.startswith("//"):
        return ""
    return raw


def _request_cookie(headers: dict[str, str], name: str) -> str:
    raw_cookie = headers.get("cookie", "")
    if not raw_cookie:
        return ""
    cookie = SimpleCookie()
    try:
        cookie.load(raw_cookie)
    except Exception:
        return ""
    morsel = cookie.get(name)
    return unquote(morsel.value) if morsel and morsel.value else ""


def _set_cookie_header(value: str, *, max_age: int, delete: bool = False) -> tuple[bytes, bytes]:
    encoded = quote(value, safe="/?:&=%,.-_~") if value else ""
    parts = [f"{RETURN_TO_COOKIE}={encoded}", "Path=/", "HttpOnly", "SameSite=Lax"]
    parts.append(f"Max-Age={max_age}")
    if settings.is_production:
        parts.append("Secure")
    if delete:
        parts.append("Expires=Thu, 01 Jan 1970 00:00:00 GMT")
    return b"set-cookie", "; ".join(parts).encode("latin-1")


class SameOriginMiddleware:
    """Reject cross-site browser POST/PUT/PATCH/DELETE requests in production.

    For the browser purchase flow, this middleware additionally remembers a
    validated local destination while the visitor creates an account. This
    fixes the common sequence:

        product -> login -> create account -> product/checkout

    without changing the existing checkout or payment routes.
    """

    def __init__(self, app):
        self.app = app

    async def _capture_response(self, scope, receive):
        start_message = None
        body_messages = []

        async def capture(message):
            nonlocal start_message
            message_type = message.get("type")
            if message_type == "http.response.start":
                start_message = dict(message)
                start_message["headers"] = list(message.get("headers", []))
            elif message_type == "http.response.body":
                body_messages.append(dict(message))
            else:
                body_messages.append(dict(message))

        await self.app(scope, receive, capture)
        return start_message, body_messages

    async def _send_captured(self, send, start_message, body_messages):
        if start_message is None:
            return
        await send(start_message)
        for message in body_messages:
            await send(message)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "")).upper()
        path = str(scope.get("path", ""))
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }

        # Preserve a safe destination when login/signup is entered from a
        # product purchase flow. Only relative same-site paths are accepted.
        if method == "GET" and path in {"/login", "/signup", "/artist/signup"}:
            query = scope.get("query_string", b"").decode("latin-1", errors="ignore")
            next_value = ""
            if query:
                for item in query.split("&"):
                    key, _, value = item.partition("=")
                    if key == "next":
                        next_value = value
                        break
            safe_next = _safe_return_path(next_value)

            if safe_next:
                start_message, body_messages = await self._capture_response(scope, receive)
                if start_message is not None:
                    response_headers = list(start_message.get("headers", []))
                    response_headers.append(_set_cookie_header(safe_next, max_age=RETURN_TO_MAX_AGE))
                    start_message["headers"] = response_headers
                await self._send_captured(send, start_message, body_messages)
                return

        # After account creation, redirect to the exact safe destination that
        # brought the visitor into authentication. The signup endpoint itself
        # remains unchanged and still owns account creation/session issuance.
        if method == "POST" and path in {"/signup", "/artist/signup"}:
            stored_next = _safe_return_path(_request_cookie(headers, RETURN_TO_COOKIE))
            if stored_next:
                start_message, body_messages = await self._capture_response(scope, receive)
                if start_message is not None:
                    response_headers = []
                    status_code = int(start_message.get("status", 200))
                    for key, value in start_message.get("headers", []):
                        if key.lower() == b"location" and 300 <= status_code < 400:
                            value = stored_next.encode("latin-1")
                        response_headers.append((key, value))
                    response_headers.append(_set_cookie_header("", max_age=0, delete=True))
                    start_message["headers"] = response_headers
                await self._send_captured(send, start_message, body_messages)
                return

        if (
            not settings.is_production
            or method not in {"POST", "PUT", "PATCH", "DELETE"}
            or path in EXEMPT_POST_PATHS
        ):
            await self.app(scope, receive, send)
            return

        # Bearer-authenticated API clients are not cookie-authenticated.
        authorization = headers.get("authorization", "")
        if authorization.lower().startswith("bearer ") and authorization[7:].strip():
            await self.app(scope, receive, send)
            return

        origin = _origin(headers.get("origin"))
        referer = _origin(headers.get("referer"))
        allowed = _allowed_origins(
            headers.get("host", "").strip(),
            headers.get("x-forwarded-proto", "https"),
        )

        # Modern browsers send Sec-Fetch-Site on ordinary browser requests.
        # Treat same-origin/same-site as an additional signal when Origin and
        # Referer are absent (some legitimate form submissions omit both).
        fetch_site = headers.get("sec-fetch-site", "").strip().lower()
        browser_same_site = fetch_site in {"same-origin", "same-site"}

        if (origin and origin in allowed) or (referer and referer in allowed) or browser_same_site:
            await self.app(scope, receive, send)
            return

        # Keep API clients on the machine-readable response, while browser
        # requests receive a simple, branded error instead of raw JSON.
        accepts = headers.get("accept", "").lower()
        if "text/html" in accepts:
            response = HTMLResponse(
                """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Request blocked · BeatHub</title>
<style>body{margin:0;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#0d0d12;color:#fff;display:grid;place-items:center;min-height:100vh}.card{max-width:520px;margin:24px;padding:36px;border:1px solid #292936;border-radius:20px;background:#15151d;text-align:center;box-shadow:0 20px 60px #0006}h1{margin:0 0 12px;font-size:28px}p{color:#b9b9c7;line-height:1.6}.brand{font-weight:800;letter-spacing:.04em;margin-bottom:22px}.btn{display:inline-block;margin-top:12px;padding:11px 18px;border-radius:10px;background:#fff;color:#111;text-decoration:none;font-weight:700}</style>
</head><body><main class="card"><div class="brand">BeatHub</div><h1>Request blocked</h1><p>This request could not be verified as coming from BeatHub. Please go back and try again.</p><a class="btn" href="/">Return to BeatHub</a></main></body></html>""",
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        else:
            response = JSONResponse(
                {"detail": "Cross-site request blocked."},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        await response(scope, receive, send)
