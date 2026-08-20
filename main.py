"""
BeatHub — main application entrypoint.
"""

import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Request
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

# --------------------------------------------------------------
# PERSISTENT MEDIA DIRECTORY
# --------------------------------------------------------------

MEDIA_DIR = Path(settings.MEDIA_ROOT)

if not MEDIA_DIR.is_absolute():
    MEDIA_DIR = BASE_DIR / MEDIA_DIR

MEDIA_DIR = MEDIA_DIR.resolve()

AUDIO_DIR = MEDIA_DIR / "audio"
COVERS_DIR = MEDIA_DIR / "covers"
PREVIEWS_DIR = MEDIA_DIR / "previews"

for directory in (
    MEDIA_DIR,
    AUDIO_DIR,
    COVERS_DIR,
    PREVIEWS_DIR,
):
    directory.mkdir(
        parents=True,
        exist_ok=True,
    )

logger.info("BeatHub MEDIA_ROOT: %s", MEDIA_DIR)
logger.info("BeatHub AUDIO_DIR: %s", AUDIO_DIR)
logger.info("BeatHub COVERS_DIR: %s", COVERS_DIR)
logger.info("BeatHub PREVIEWS_DIR: %s", PREVIEWS_DIR)

app = FastAPI(title=settings.APP_NAME)

templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR)
)

# --------------------------------------------------------------
# STATIC
# --------------------------------------------------------------

if STATIC_DIR.exists():
    app.mount(
        "/static",
        StaticFiles(directory=str(STATIC_DIR)),
        name="static",
    )

# --------------------------------------------------------------
# PUBLIC COVER ART
# --------------------------------------------------------------

app.mount(
    "/media/covers",
    StaticFiles(directory=str(COVERS_DIR)),
    name="media-covers",
)

# --------------------------------------------------------------
# PUBLIC PREVIEWS
# --------------------------------------------------------------

app.mount(
    "/media/previews",
    StaticFiles(directory=str(PREVIEWS_DIR)),
    name="media-previews",
)

# --------------------------------------------------------------
# IMPORTANT:
# DO NOT mount /media/audio.
#
# Full purchased audio is protected by music.py and is delivered
# only after checking the user's License + completed Order.
# --------------------------------------------------------------

# --------------------------------------------------------------
# DATABASE
# --------------------------------------------------------------

Base.metadata.create_all(bind=engine)

# --------------------------------------------------------------
# ROUTERS
# --------------------------------------------------------------

app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(music.router)
app.include_router(checkout.router)
app.include_router(mpesa_callback.router)
app.include_router(dashboard.router)
app.include_router(admin.router)

# --------------------------------------------------------------
# DASHBOARD COMPATIBILITY
# --------------------------------------------------------------

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

# --------------------------------------------------------------
# HEALTH
# --------------------------------------------------------------

@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "env": settings.APP_ENV,
        "media_root": str(MEDIA_DIR),
        "audio_directory_exists": AUDIO_DIR.exists(),
        "covers_directory_exists": COVERS_DIR.exists(),
        "previews_directory_exists": PREVIEWS_DIR.exists(),
    }

# --------------------------------------------------------------
# ERRORS
# --------------------------------------------------------------

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
