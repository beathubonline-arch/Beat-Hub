import logging
import os
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import Base, engine
from app.models import *  # noqa: F401,F403 - register all SQLAlchemy models
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
    logger.warning(
        "SESSION_SECRET is not configured. Using a temporary development secret. "
        "Set SESSION_SECRET in the production environment."
    )
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
    """Attach the homepage motion stylesheet without touching the response body.

    The previous implementation buffered and reconstructed the entire homepage
    response. That was unnecessary and could interfere with Starlette's async
    response streaming, producing `anext(): asynchronous generator is already
    running`. A Link response header gives the browser the stylesheet directly
    while leaving the ASGI response stream untouched.
    """

    LINK_HEADER = b'</static/css/home-animation.css>; rel="stylesheet"'

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") != "/":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers", []))

                # Avoid duplicate stylesheet headers if another layer adds one.
                has_motion_link = any(
                    key.lower() == b"link" and b"home-animation.css" in value
                    for key, value in headers
                )
                if not has_motion_link:
                    headers.append((b"link", self.LINK_HEADER))

                # Keep an observable marker for production diagnostics.
                headers.append((b"x-beathub-home-motion", b"loaded"))

                message = dict(message)
                message["headers"] = headers

            await send(message)

        await self.app(scope, receive, send_wrapper)


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
    """Compatibility URL for older navigation/bookmarks."""
    return RedirectResponse(url="/merch", status_code=307)


@app.on_event("startup")
async def run_database_migrations() -> None:
    """Apply production schema migrations before accepting requests."""
    if engine.url.get_backend_name() == "sqlite":
        logger.info("SQLite detected; create_all is sufficient for local development.")
        return

    env = os.environ.copy()
    env["PYTHONPATH"] = str(BASE_DIR) + os.pathsep + env.get("PYTHONPATH", "")

    logger.info("Running Alembic database migrations before application startup.")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(BASE_DIR),
        env=env,
        capture_output=True,
        text=True,
    )

    if result.stdout:
        logger.info("Alembic output:\n%s", result.stdout.strip())
    if result.stderr:
        logger.warning("Alembic stderr:\n%s", result.stderr.strip())

    if result.returncode != 0:
        logger.error(
            "Database migration failed with exit code %s. Application startup aborted.",
            result.returncode,
        )
        raise RuntimeError("Database migration failed during application startup.")

    logger.info("Database migrations completed successfully. Application startup may continue.")


# One canonical route implementation per feature. Payment collection is
# Paystack-only; beat and merchandise payments share the same callback/webhook.
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
