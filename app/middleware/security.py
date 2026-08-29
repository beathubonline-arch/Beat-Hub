"""Small production security middleware that is intentionally route-aware.

BeatHub authenticates browser requests with an HttpOnly cookie. For those
requests, state-changing cross-site requests must be rejected. Provider
webhooks and non-browser bearer-token clients are explicitly left alone.

This middleware also preserves a safe in-site purchase destination when a
visitor goes from a product page -> login -> create account. That flow is
implemented here so the existing checkout, marketplace and authentication
routes do not need to be duplicated or reordered.
"""

from http.cookies import SimpleCookie
from urllib.parse import quote, unquote, urlparse

from fastapi.responses import JSONResponse

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

            if path == "/login" and not safe_next:
                # Do not let an old purchase destination affect a normal login.
                safe_next = ""

            if safe_next:
                captured_start = None

                async def capture(message):
                    nonlocal captured_start
                    if message.get("type") == "http.response.start":
                        captured_start = dict(message)
                        captured_start["headers"] = list(message.get("headers", []))
                    else:
                        await send(message)

                await self.app(scope, receive, capture)
                if captured_start is not None:
                    response_headers = list(captured_start.get("headers", []))
                    response_headers.append(_set_cookie_header(safe_next, max_age=RETURN_TO_MAX_AGE))
                    captured_start["headers"] = response_headers
                    await send(captured_start)
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
                return

        # After account creation, redirect to the exact safe destination that
        # brought the visitor into authentication. The signup endpoint itself
        # remains unchanged and still owns account creation/session issuance.
        if method == "POST" and path in {"/signup", "/artist/signup"}:
            stored_next = _safe_return_path(_request_cookie(headers, RETURN_TO_COOKIE))
            if stored_next:
                captured_start = None

                async def capture(message):
                    nonlocal captured_start
                    if message.get("type") == "http.response.start":
                        captured_start = dict(message)
                        captured_start["headers"] = list(message.get("headers", []))
                    else:
                        await send(message)

                await self.app(scope, receive, capture)
                if captured_start is not None:
                    response_headers = []
                    for key, value in captured_start.get("headers", []):
                        if key.lower() == b"location" and 300 <= int(captured_start.get("status", 200)) < 400:
                            value = stored_next.encode("latin-1")
                        response_headers.append((key, value))
                    response_headers.append(_set_cookie_header("", max_age=0, delete=True))
                    captured_start["headers"] = response_headers
                    await send(captured_start)
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
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

        if (origin and origin in allowed) or (referer and referer in allowed):
            await self.app(scope, receive, send)
            return

        response = JSONResponse(
            {"detail": "Cross-site request blocked."},
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
        await response(scope, receive, send)
