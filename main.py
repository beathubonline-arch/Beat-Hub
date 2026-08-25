import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import Base, engine
from app.models import *  # noqa: F401,F403
from app.routers import (
    admin,
    auth,
    checkout,
    creator_store,
    dashboard,
    merchandise,
    merchandise_account,
    music,
    pages,
    paystack_checkout,
)

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

    # The vinyl animation is also embedded as a fallback. This means the ring
    # does not depend on a second network request or a cached stylesheet.
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


app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret(),
    session_cookie="beathub_session",
    max_age=_session_max_age(),
    same_site="lax",
    https_only=_session_https_only(),
)
app.add_middleware(HomepageMotionMiddleware)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# SQLAlchemy owns application-table creation for local SQLite development.
# Production PostgreSQL schema changes are handled by Alembic before deploy.
try:
    Base.metadata.create_all(bind=engine)
except Exception:
    logger.exception("Database table initialization failed.")
    raise


@app.get("/healthz", include_in_schema=False)
async def healthz_get():
    return JSONResponse({"status": "ok"})


@app.head("/healthz", include_in_schema=False)
async def healthz_head():
    return JSONResponse({"status": "ok"})


@app.get("/merchandise", include_in_schema=False)
async def merchandise_legacy_alias():
    return RedirectResponse(url="/merch", status_code=307)


# Alembic is intentionally NOT executed by the web process. Render must run:
#     alembic upgrade head
# as its Pre-Deploy Command, followed by:
#     uvicorn main:app --host 0.0.0.0 --port $PORT
# This keeps PostgreSQL schema work out of the request path and prevents the
# Render "No open ports" timeout.

# Merchandise schema is now owned by Alembic migration 0010. The historical
# request-time ensure_merch_tables() helper remains in the legacy router for
# compatibility, but is disabled here so /merch never performs CREATE/ALTER/
# INDEX/inspection work during a customer request.
merchandise.ensure_merch_tables = lambda db: None

# One canonical route implementation per feature.
app.include_router(auth.router)
app.include_router(creator_store.router)
app.include_router(pages.router)
app.include_router(music.router)
app.include_router(checkout.router)
app.include_router(paystack_checkout.router)
app.include_router(dashboard.router)
app.include_router(admin.router)
app.include_router(merchandise.router)
app.include_router(merchandise_account.router)
