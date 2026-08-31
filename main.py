import logging
import os
import time
from collections import defaultdict, deque
from decimal import Decimal, InvalidOperation
from pathlib import Path
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, quote

from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.database import Base, engine
from app.models import *  # noqa: F401,F403
from app.middleware.security import SameOriginMiddleware
from app.routers import (
    admin,
    admin_mpesa_payout,
    admin_paystack_reconciliation,
    admin_unified_sales,
    audio_preview,
    auth,
    checkout,
    creator_store,
    dashboard,
    dashboard_analytics,
    merchandise,
    merchandise_account,
    music,
    pages,
    paystack_checkout,
    payout_admin,
    track_catalog,
)
from app.services.payout_policy import PAYOUT_MINIMUM

logger = logging.getLogger("beathub")
BASE_DIR = Path(__file__).resolve().parent
APP_DIR = BASE_DIR / "app"
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title=getattr(settings, "APP_NAME", "BeatHub"),
    description="BeatHub — beats, music, sessions, producer stores and creator merchandise.",
    version="1.0.0",
)
app.state.beathub_production = bool(settings.is_production)
app.state.beathub_base_url = str(getattr(settings, "BASE_URL", "") or "").strip().rstrip("/")


def _session_secret() -> str:
    value = os.getenv("SESSION_SECRET") or getattr(settings, "SESSION_SECRET", None)
    if value and str(value).strip():
        return str(value).strip()
    logger.warning("SESSION_SECRET is not configured. Set SESSION_SECRET in production.")
    return "beathub-development-session-secret-change-me"


def _session_max_age() -> int:
    raw = os.getenv("SESSION_MAX_AGE") or getattr(settings, "SESSION_MAX_AGE", None) or 60 * 60 * 24 * 30
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 60 * 60 * 24 * 30
    return max(300, min(value, 60 * 60 * 24 * 365))


def _session_https_only() -> bool:
    raw = os.getenv("SESSION_HTTPS_ONLY") or getattr(settings, "SESSION_HTTPS_ONLY", None)
    if raw is None:
        return True
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


class HomepageMotionMiddleware:
    """Safely inject the homepage motion CSS without replaying an ASGI generator."""

    LINK = (
        b'<link rel="stylesheet" href="/static/css/home-animation.css?v=20260825" '
        b'data-beathub-home-motion="1">'
    )

    INLINE = (
        b'<style data-beathub-home-motion-inline="1">'
        b'@keyframes beathub-vinyl-spin-inline{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}'
        b'.vinyl{animation:beathub-vinyl-spin-inline 12s linear infinite !important;'
        b'transform-origin:center center !important;will-change:transform}'
        b'</style>'
    )

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") != "/":
            await self.app(scope, receive, send)
            return

        start_message = None
        body_chunks = []

        async def capture(message):
            nonlocal start_message
            message_type = message.get("type")
            if message_type == "http.response.start":
                start_message = dict(message)
                start_message["headers"] = list(message.get("headers", []))
            elif message_type == "http.response.body":
                body_chunks.append(bytes(message.get("body", b"")))
            else:
                await send(message)

        await self.app(scope, receive, capture)
        if start_message is None:
            return

        headers = list(start_message.get("headers", []))
        content_type = ""
        for key, value in headers:
            if key.lower() == b"content-type":
                content_type = value.decode("latin-1").lower()
                break

        body = b"".join(body_chunks)
        if content_type.startswith("text/html"):
            marker = b"</head>"
            marker_index = body.lower().find(marker)
            if marker_index >= 0 and b"data-beathub-home-motion-inline=" not in body:
                body = body[:marker_index] + self.LINK + self.INLINE + body[marker_index:]
                headers = [
                    (key, value)
                    for key, value in headers
                    if key.lower() != b"content-length"
                ]
                headers.append((b"cache-control", b"no-cache, no-store, must-revalidate"))
                headers.append((b"x-beathub-home-motion", b"loaded"))

        start_message["headers"] = headers
        await send(start_message)
        await send({"type": "http.response.body", "body": body, "more_body": False})


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add safe baseline browser security headers without changing page content.

    CSP is intentionally not enabled here yet: BeatHub currently uses inline
    scripts/styles and third-party payment/audio integrations that need a
    dedicated CSP inventory. HSTS is only emitted in production because local
    HTTP development must continue to work.
    """

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response


class AbuseRateLimitMiddleware(BaseHTTPMiddleware):
    """Small dependency-free guard for high-risk unauthenticated endpoints.

    This is deliberately conservative: it only throttles authentication and
    password-reset entry points, never payment callbacks, browsing, audio,
    uploads, dashboards, or ordinary purchases. The store is process-local;
    it is a safety net, not the final distributed rate-limit architecture.
    """

    RULES = {
        ("POST", "/login"): (10, 60),
        ("POST", "/signup"): (5, 300),
        ("POST", "/forgot-password"): (5, 900),
        ("POST", "/reset-password"): (10, 900),
    }

    def __init__(self, app):
        super().__init__(app)
        self._events = defaultdict(deque)

    @staticmethod
    def _client_key(request):
        client = request.client
        return str(getattr(client, "host", "unknown") or "unknown")

    async def dispatch(self, request, call_next):
        rule = self.RULES.get((request.method.upper(), request.url.path))
        if not rule:
            return await call_next(request)

        limit, window = rule
        key = (self._client_key(request), request.method.upper(), request.url.path)
        now = time.monotonic()
        events = self._events[key]
        cutoff = now - window
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) >= limit:
            retry_after = max(1, int(window - (now - events[0])))
            response = JSONResponse(
                {"detail": "Too many attempts. Please try again later."},
                status_code=429,
                headers={"Retry-After": str(retry_after), "Cache-Control": "no-store"},
            )
            return response

        events.append(now)
        if len(self._events) > 10000:
            stale_keys = [k for k, values in self._events.items() if not values or values[-1] <= now - 3600]
            for stale_key in stale_keys[:5000]:
                self._events.pop(stale_key, None)
        return await call_next(request)


class MerchandiseLoginRedirectMiddleware:
    """Give logged-out merchandise buyers a real login flow instead of a raw 401."""

    SESSION_COOKIE = "beathub_session"

    def __init__(self, app):
        self.app = app

    @staticmethod
    def _has_session_cookie(headers) -> bool:
        raw_cookie = ""
        for key, value in headers:
            if key.lower() == b"cookie":
                raw_cookie = value.decode("latin-1", errors="ignore")
                break
        if not raw_cookie:
            return False
        cookie = SimpleCookie()
        try:
            cookie.load(raw_cookie)
        except Exception:
            return False
        morsel = cookie.get(MerchandiseLoginRedirectMiddleware.SESSION_COOKIE)
        return bool(morsel and morsel.value)

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            method = scope.get("method", "").upper()
            path = scope.get("path", "")
            if method == "POST" and path.startswith("/merch/") and path.endswith("/buy"):
                parts = [part for part in path.split("/") if part]
                if len(parts) == 3 and not self._has_session_cookie(scope.get("headers", [])):
                    slug = parts[1]
                    next_url = f"/merch/{slug}"
                    location = (
                        f"/login?next={quote(next_url, safe='')}"
                        f"&error={quote('Please sign in to continue with your merchandise purchase.') }"
                    )
                    response = RedirectResponse(url=location, status_code=303)
                    await response(scope, receive, send)
                    return

        await self.app(scope, receive, send)


class CreatorPayoutPolicyMiddleware(BaseHTTPMiddleware):
    """Enforce the creator withdrawal minimum without breaking ASGI startup."""

    async def dispatch(self, request, call_next):
        if request.method != "POST" or request.url.path != "/dashboard/withdraw":
            return await call_next(request)

        try:
            body = await request.body()
            parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
            raw_amount = (parsed.get("amount") or [""])[0].strip()
            amount = Decimal(raw_amount)
            valid_amount = amount.is_finite() and amount >= PAYOUT_MINIMUM
        except (InvalidOperation, TypeError, UnicodeDecodeError):
            valid_amount = False

        if not valid_amount:
            return RedirectResponse(
                url=(
                    "/dashboard/withdraw?error="
                    + quote(f"The minimum creator withdrawal is KSh {PAYOUT_MINIMUM:,}.")
                ),
                status_code=303,
            )

        return await call_next(request)


# Authentication is already implemented as a signed JWT in the HttpOnly
# beathub_session cookie by app.routers.auth + app.utils.deps. Do not install
# Starlette SessionMiddleware on the same cookie: doing so creates two
# incompatible owners for one cookie name and can cause session ambiguity.
app.add_middleware(SameOriginMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(AbuseRateLimitMiddleware)
app.add_middleware(MerchandiseLoginRedirectMiddleware)
app.add_middleware(CreatorPayoutPolicyMiddleware)
app.add_middleware(HomepageMotionMiddleware)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Production schema is managed exclusively by Alembic migrations.
# Never run Base.metadata.create_all() while the web process is starting.


@app.get("/healthz", include_in_schema=False)
async def healthz_get():
    return JSONResponse({"status": "ok"})


@app.head("/healthz", include_in_schema=False)
async def healthz_head():
    return JSONResponse({"status": "ok"})


@app.get("/merchandise", include_in_schema=False)
async def merchandise_legacy_alias():
    return RedirectResponse(url="/merch", status_code=307)


# One canonical route implementation per feature.
app.include_router(auth.router)
app.include_router(creator_store.router)
app.include_router(pages.router)
# Secure preview route must be registered before music.router's legacy preview
# handler. The public URL remains exactly /track/{slug}/preview.
app.include_router(audio_preview.router)
app.include_router(music.router)
app.include_router(track_catalog.router)
app.include_router(checkout.router)
app.include_router(paystack_checkout.router)
app.include_router(dashboard.router)
app.include_router(dashboard_analytics.router)
app.include_router(admin_unified_sales.router)
app.include_router(admin_mpesa_payout.router)
app.include_router(admin_paystack_reconciliation.router)
app.include_router(admin.router)
app.include_router(payout_admin.router)
app.include_router(merchandise.router)
app.include_router(merchandise_account.router)
