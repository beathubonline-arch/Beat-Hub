"""
BeatHub — main application entrypoint.
"""

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.database import Base, engine
from app.routers import (
    admin,
    auth,
    checkout,
    dashboard,
    mpesa_callback,
    music,
    pages,
)
from app.utils.deps import require_creator

logger = logging.getLogger("beathub")

BASE_DIR = Path(__file__).resolve().parent
APP_DIR = BASE_DIR / "app"

TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"


# ------------------------------------------------------------------
# MEDIA STORAGE
#
# Render may provide MEDIA_ROOT=/var/data/... when a persistent disk
# is configured. If that location is unavailable/unwritable, fall
# back safely to the application's local media directory.
# ------------------------------------------------------------------

def get_writable_media_dir() -> Path:
    configured = str(getattr(settings, "MEDIA_ROOT", "") or "").strip()

    candidates = []

    if configured:
        configured_path = Path(configured)

        if not configured_path.is_absolute():
            configured_path = BASE_DIR / configured_path

        candidates.append(configured_path)

    # Normal application storage.
    candidates.append(BASE_DIR / "media")

    # Last-resort writable temporary storage.
    candidates.append(Path("/tmp/beathub-media"))

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)

            # Verify that the directory is actually writable.
            test_file = candidate / ".beathub_write_test"

            with open(test_file, "a", encoding="utf-8"):
                pass

            try:
                test_file.unlink()
            except OSError:
                pass

            logger.info("BeatHub media directory: %s", candidate)
            return candidate.resolve()

        except (PermissionError, OSError) as exc:
            logger.warning(
                "Media directory unavailable: %s (%s)",
                candidate,
                exc,
            )

    raise RuntimeError(
        "No writable media directory is available."
    )


MEDIA_DIR = get_writable_media_dir()


# ------------------------------------------------------------------
# APPLICATION
# ------------------------------------------------------------------

app = FastAPI(title=settings.APP_NAME)

templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR)
)


# ------------------------------------------------------------------
# STATIC FILES
# ------------------------------------------------------------------

if STATIC_DIR.exists():
    app.mount(
        "/static",
        StaticFiles(directory=str(STATIC_DIR)),
        name="static",
    )


# ------------------------------------------------------------------
# PUBLIC MEDIA
#
# Covers and previews are public.
# Purchased master audio is NOT publicly mounted.
# ------------------------------------------------------------------

COVERS_DIR = MEDIA_DIR / "covers"
PREVIEWS_DIR = MEDIA_DIR / "previews"
AUDIO_DIR = MEDIA_DIR / "audio"

COVERS_DIR.mkdir(parents=True, exist_ok=True)
PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_DIR.mkdir(parents=True, exist_ok=True)


app.mount(
    "/media/covers",
    StaticFiles(directory=str(COVERS_DIR)),
    name="media-covers",
)

app.mount(
    "/media/previews",
    StaticFiles(directory=str(PREVIEWS_DIR)),
    name="media-previews",
)


# ------------------------------------------------------------------
# DATABASE
# ------------------------------------------------------------------

Base.metadata.create_all(bind=engine)


# ------------------------------------------------------------------
# ROUTERS
# ------------------------------------------------------------------

app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(music.router)
app.include_router(checkout.router)
app.include_router(mpesa_callback.router)
app.include_router(dashboard.router)
app.include_router(admin.router)


# ------------------------------------------------------------------
# DASHBOARD COMPATIBILITY
# ------------------------------------------------------------------

@app.get("/artist/dashboard", include_in_schema=False)
@app.get("/creator/dashboard", include_in_schema=False)
@app.get("/producer/dashboard", include_in_schema=False)
@app.get("/dashboard/home", include_in_schema=False)
@app.get("/dashboard/index", include_in_schema=False)
def dashboard_alias(
    user=Depends(require_creator),
):
    return RedirectResponse(
        url="/dashboard",
        status_code=303,
    )


# ------------------------------------------------------------------
# HEALTH
# ------------------------------------------------------------------

@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "env": settings.APP_ENV,
        "media_root": str(MEDIA_DIR),
    }


# ------------------------------------------------------------------
# ERRORS
# ------------------------------------------------------------------

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
):
    if exc.status_code == 404:
        return templates.TemplateResponse(
            request,
            "errors/404.html",
            {
                "request": request,
                "current_user": None,
                "current_year": 2026,
            },
            status_code=404,
        )

    if exc.status_code == 401:
        return RedirectResponse(
            url="/login?error=Please%20log%20in%20to%20continue.",
            status_code=303,
        )

    if exc.status_code == 403:
        return templates.TemplateResponse(
            request,
            "errors/403.html",
            {
                "request": request,
                "current_user": None,
                "current_year": 2026,
            },
            status_code=403,
        )

    return templates.TemplateResponse(
        request,
        "errors/500.html",
        {
            "request": request,
            "current_user": None,
            "current_year": 2026,
            "detail": exc.detail,
        },
        status_code=exc.status_code,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
):
    return templates.TemplateResponse(
        request,
        "errors/400.html",
        {
            "request": request,
            "current_user": None,
            "current_year": 2026,
            "errors": exc.errors(),
        },
        status_code=422,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
):
    logger.exception(
        "Unhandled BeatHub error: %s",
        exc,
    )

    return templates.TemplateResponse(
        request,
        "errors/500.html",
        {
            "request": request,
            "current_user": None,
            "current_year": 2026,
            "detail": None,
        },
        status_code=500,
    )
