"""Production browser-security middleware for BeatHub.

Browser requests authenticated by the HttpOnly session cookie must carry
same-origin metadata for state-changing requests. Provider webhooks and
bearer-authenticated API clients are explicitly left alone.
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
STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


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


def _location_with_next(location: str, next_path: str) -> str:
    """Add a safe continuation target to a verification redirect."""
    separator = "&" if "?" in location else "?"
    return f"{location}{separator}next={quote(next_path, safe='')}"


class SameOriginMiddleware:
    """Reject cross-site browser state changes in production.

    A missing Origin/Referer is rejected rather than treated as safe. This
    closes the metadata-less request gap while preserving webhook and bearer
    API compatibility.

    The return-to cookie is deliberately kept alive while a new account moves
    through email verification. Consuming it on the initial /signup redirect
    would send the browser to the product before authentication exists and
    would lose the original purchase destination.
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

        if method == "POST" and path in {"/signup", "/artist/signup"}:
            stored_next = _safe_return_path(_request_cookie(headers, RETURN_TO_COOKIE))
            if stored_next:
                start_message, body_messages = await self._capture_response(scope, receive)
                if start_message is not None:
                    response_headers = []
                    status_code = int(start_message.get("status", 200))
                    redirected = False
                    is_verification_redirect = False
                    for key, value in start_message.get("headers", []):
                        if key.lower() == b"location" and 300 <= status_code < 400:
                            redirected = True
                            location = value.decode("latin-1")
                            if urlparse(location).path == "/verify-email":
                                # Signup is not complete until email verification
                                # and the subsequent login. Keep the destination
                                # in both the verification URL and the cookie.
                                value = _location_with_next(location, stored_next).encode("latin-1")
                                is_verification_redirect = True
                            else:
                                # For a true post-signup destination, consume the
                                # continuation exactly once.
                                value = stored_next.encode("latin-1")
                        response_headers.append((key, value))

                    if is_verification_redirect:
                        # Refresh the TTL while the user is entering the code.
                        response_headers.append(_set_cookie_header(stored_next, max_age=RETURN_TO_MAX_AGE))
                    elif redirected:
                        response_headers.append(_set_cookie_header("", max_age=0, delete=True))
                    else:
                        # Validation errors render the signup form again. Keep
                        # the destination so a corrected submission still returns
                        # to the exact product/checkout page.
                        response_headers.append(_set_cookie_header(stored_next, max_age=RETURN_TO_MAX_AGE))
                    start_message["headers"] = response_headers
                await self._send_captured(send, start_message, body_messages)
                return

        if (
            not settings.is_production
            or method not in STATE_CHANGING_METHODS
            or path in EXEMPT_POST_PATHS
        ):
            await self.app(scope, receive, send)
            return

        # Bearer-authenticated API clients are not cookie-authenticated and
        # therefore do not use this browser CSRF protection mechanism.
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

        # Prefer Origin. Referer is accepted only as a fallback for clients
        # that legitimately omit Origin. Neither header means the browser
        # request cannot be proven same-origin, so reject it.
        if origin:
            same_origin = origin in allowed
        elif referer:
            same_origin = referer in allowed
        else:
            same_origin = False

        if same_origin:
            await self.app(scope, receive, send)
            return

        response = JSONResponse(
            {"detail": "Cross-site request blocked."},
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
        await response(scope, receive, send)
