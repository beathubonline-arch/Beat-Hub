import logging
import os
import subprocess
import sys
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import Base, engine, get_db
from app.models.music import Track
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
    paystack_merchandise,
    stripe_checkout,
)
from app.services.storage import media_url

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


@app.middleware("http")
async def homepage_motion_assets(request: Request, call_next):
    """Reliably attach the homepage animation stylesheet to the actual HTML response."""
    response = await call_next(request)

    content_type = response.headers.get("content-type", "")
    if request.url.path == "/" and content_type.startswith("text/html"):
        css_href = b'<link rel="stylesheet" href="/static/css/home-animation.css?v=3">'

        # TemplateResponse is normally an HTMLResponse with an in-memory body.
        # Injecting the stylesheet into <head> makes the animation work even when
        # the homepage is a standalone template and does not extend base.html.
        body = getattr(response, "body", None)
        if isinstance(body, bytes) and b"</head>" in body and css_href not in body:
            response.body = body.replace(
                b"</head>",
                css_href + b"</head>",
                1,
            )
            response.headers["content-length"] = str(len(response.body))

        # Keep the HTTP Link header as a secondary loading path for browsers/CDNs.
        response.headers["Link"] = '<' + "/static/css/home-animation.css?v=3" + '>; rel="stylesheet"'

    return response


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


app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret(),
    session_cookie="beathub_session",
    max_age=_session_max_age(),
    same_site="lax",
    https_only=_session_https_only(),
)

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
        logger.error(
            "Database migration failed with exit code %s. Application startup aborted.",
            result.returncode,
        )
        raise RuntimeError("Database migration failed during application startup.")

    logger.info("Database migrations completed successfully. Application startup may continue.")


@app.get("/track/{slug}/preview", include_in_schema=False)
def public_track_preview(slug: str, db: Session = Depends(get_db)):
    track = db.query(Track).filter(Track.slug == slug).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found.")
    if not getattr(track, "is_published", False):
        raise HTTPException(status_code=404, detail="Preview not available.")

    stored = str(
        getattr(track, "preview_file_path", None)
        or getattr(track, "audio_file_path", None)
        or ""
    ).strip()
    if not stored:
        raise HTTPException(status_code=404, detail="Preview audio is not available.")
    if stored.startswith(("http://", "https://")):
        return RedirectResponse(url=stored, status_code=307)
    if stored.startswith(("r2://", "s3://")):
        url = media_url(stored, expires=3600)
        if not url:
            raise HTTPException(status_code=404, detail="Preview audio is not available.")
        return RedirectResponse(url=url, status_code=307)

    value = stored.replace("\\", "/").lstrip("/")
    media_root_value = getattr(settings, "MEDIA_ROOT", None) or "media"
    media_root = Path(media_root_value).expanduser()
    if not media_root.is_absolute():
        media_root = Path.cwd() / media_root
    media_root = media_root.resolve()

    candidates = []
    stored_path = Path(stored)
    if stored_path.is_absolute():
        candidates.append(stored_path.resolve())
    else:
        candidates.append((Path.cwd() / stored_path).resolve())
        candidates.append((media_root / stored_path).resolve())
        if value.startswith("media/"):
            candidates.append((media_root / value[6:]).resolve())

    for candidate in candidates:
        try:
            candidate.relative_to(media_root)
        except ValueError:
            continue
        if candidate.is_file():
            media_type = {
                ".mp3": "audio/mpeg",
                ".wav": "audio/wav",
                ".m4a": "audio/mp4",
                ".aac": "audio/aac",
                ".ogg": "audio/ogg",
                ".flac": "audio/flac",
            }.get(candidate.suffix.lower(), "application/octet-stream")
            return FileResponse(
                path=str(candidate),
                media_type=media_type,
                headers={
                    "Cache-Control": "public, max-age=3600",
                    "Accept-Ranges": "bytes",
                },
            )

    raise HTTPException(status_code=404, detail="Preview audio is not available.")


app.include_router(auth.router)
app.include_router(creator_store.router)
app.include_router(pages.router)
app.include_router(music.router)
app.include_router(checkout.router)
app.include_router(paystack_checkout.router)
app.include_router(paystack_merchandise.router)
app.include_router(stripe_checkout.router)
app.include_router(dashboard.router)
app.include_router(admin.router)
app.include_router(merchandise.router)
app.include_router(merchandise_account.router)
