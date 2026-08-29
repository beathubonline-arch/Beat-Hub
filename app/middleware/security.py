"""Small production security middleware that is intentionally route-aware.

BeatHub authenticates browser requests with an HttpOnly cookie. For those
requests, state-changing cross-site requests must be rejected. Provider
webhooks and non-browser bearer-token clients are explicitly left alone.
"""

from urllib.parse import urlparse

from fastapi.responses import JSONResponse


EXEMPT_POST_PATHS = {
    "/paystack/webhook",
    "/paystack/transfer/webhook",
    "/mpesa/callback",
    "/mpesa/b2c/callback",
}


def _origin(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlparse(value.strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _allowed_origins(app, host: str, forwarded_proto: str) -> set[str]:
    allowed: set[str] = set()
    base_url = getattr(getattr(app, "state", None), "beathub_base_url", "") or ""
    base_origin = _origin(base_url)
    if base_origin:
        allowed.add(base_origin)

    if host:
        scheme = forwarded_proto.split(",", 1)[0].strip().lower() or "https"
        allowed.add(f"{scheme}://{host.lower()}")

    return allowed


class SameOriginMiddleware:
    """Reject cross-site browser POST/PUT/PATCH/DELETE requests in production."""

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

        app_state = getattr(self.app, "state", None)
        is_production = bool(getattr(app_state, "beathub_production", False))
        if not is_production or method not in {"POST", "PUT", "PATCH", "DELETE"} or path in EXEMPT_POST_PATHS:
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
            self.app,
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
