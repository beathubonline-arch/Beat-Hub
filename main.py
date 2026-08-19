"""
BeatHub — main application entrypoint.

Run locally:
    uvicorn main:app --reload

Production / Render:
    uvicorn main:app --host 0.0.0.0 --port $PORT
"""

import logging
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

# ---------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
APP_DIR = BASE_DIR / "app"

TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"
MEDIA_DIR = BASE_DIR / settings.MEDIA_ROOT

MEDIA_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------

app = FastAPI(title=settings.APP_NAME)

templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR)
)

# ---------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------

if STATIC_DIR.exists():
    app.mount(
        "/static",
        StaticFiles(directory=str(STATIC_DIR)),
        name="static",
    )

app.mount(
    "/media",
    StaticFiles(directory=str(MEDIA_DIR)),
    name="media",
)

# ---------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------

Base.metadata.create_all(bind=engine)

# ---------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------

app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(music.router)
app.include_router(checkout.router)
app.include_router(mpesa_callback.router)

# Dashboard router MUST be mounted.
app.include_router(dashboard.router)

# Admin router MUST remain mounted.
app.include_router(admin.router)

# ---------------------------------------------------------------------
# Dashboard safety routes
# ---------------------------------------------------------------------
#
# The real dashboard route is already supplied by dashboard.router.
# These aliases protect against an older navigation link using
# /artist/dashboard or /creator/dashboard.
#

@app.get("/artist/dashboard", include_in_schema=False)
def artist_dashboard_alias(
    user=Depends(require_creator),
):
    return RedirectResponse(
        url="/dashboard",
        status_code=307,
    )


@app.get("/creator/dashboard", include_in_schema=False)
def creator_dashboard_alias(
    user=Depends(require_creator),
):
    return RedirectResponse(
        url="/dashboard",
        status_code=307,
    )


# ---------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------

@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "env": settings.APP_ENV,
    }


# ---------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------

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
