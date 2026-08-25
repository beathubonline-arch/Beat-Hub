import logging
import os
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI
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
    logger.warning("SESSION_SECRET is not configured. Using a temporary development secret.")
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
    """Safely inject homepage motion CSS at the ASGI send layer.

    This deliberately avoids Starlette BaseHTTPMiddleware response-body
    iteration. Consuming a BaseHTTPMiddleware response.body_iterator from a
    replacement async generator can race with Starlette's response task and
    produce: `anext(): asynchronous generator is already running`.
    """

    def __init__(self, app):
        self.app = app
        self.css_path = STATIC_DIR / "css" / "home-animation.css"

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") != "/":
            await self.app(scope, receive, send)
            return

        try:
            css = self.css_path.read_bytes()
        except OSError:
            logger.exception("Homepage animation stylesheet could not be read: %s", self.css_path)
            await self.app(scope, receive, send)
            return

        marker = b'<style id="beathub-home-motion">'
        injection = marker + css + b"</style>"
        started = False
        content_type = ""
        response_headers = []
        body_chunks = []

        async def send_wrapper(message):
            nonlocal started, content_type, response_headers

            message_type = message.get("type")

            if message_type == "http.response.start":
                started = True
                response_headers = list(message.get("headers", []))
                for key, value in response_headers:
                    if key.lower() == b"content-type":
                        content_type = value.decode("latin-1").lower()
                        break
                return

            if message_type == "http.response.body":
                body_chunks.append(bytes(message.get("body", b"")))
                if message.get("more_body", False):
                    return

                body = b"".join(body_chunks)
                if content_type.startswith("text/html") and b"</head>" in body and marker not in body:
                    body = body.replace(b"</head>", injection + b"</head>", 1)

                    new_headers = []
                    for key, value in response_headers:
                        if key.lower() != b"content-length":
                            new_headers.append((key, value))
                    new_headers.append((b"content-length", str(len(body)).encode("ascii")))
                    new_headers.append((b"x-beathub-home-motion", b"loaded"))
                    response_headers = new_headers

                if not started:
                    return

                await send({
                    "type": "http.response.start",
                    "status": message.get("status", 200),
                    "headers": response_headers,
                })
                await send({
                    "type": "http.response.body",
                    "body": body,
                    "more_body": False,
                })
                return

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
        logger.error("Database migration failed with exit code %s. Application startup aborted.", result.returncode)
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
