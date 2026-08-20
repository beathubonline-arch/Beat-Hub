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
from app.services.search import run_search
from app.utils.deps import get_optional_user, require_creator

logger = logging.getLogger("beathub")


BASE_DIR = Path(__file__).resolve().parent
APP_DIR = BASE_DIR / "app"

TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"


app = FastAPI(
    title=settings.APP_NAME
)

templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR)
)


if STATIC_DIR.exists():
    app.mount(
        "/static",
        StaticFiles(directory=str(STATIC_DIR)),
        name="static",
    )


Base.metadata.create_all(
    bind=engine
)


app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(music.router)
app.include_router(checkout.router)
app.include_router(mpesa_callback.router)
app.include_router(dashboard.router)
app.include_router(admin.router)


# ----------------------------------------------------------------------
# HOMEPAGE COMPATIBILITY ROUTES
# These are intentionally defined here so the public homepage links
# always exist even if an older pages.py is deployed.
# ----------------------------------------------------------------------

@app.get(
    "/beats",
    include_in_schema=False,
)
def beats_compat(
    request: Request,
    db=Depends(__import__("app.database", fromlist=["get_db"]).get_db),
    current_user=Depends(get_optional_user),
):
    found = run_search(db, "beats")

    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "request": request,
            "current_user": current_user,
            "current_year": 2026,
            "query": "beats",
            "results": found["results"],
            "total_results": found["total"],
        },
    )


@app.get(
    "/sessions",
    include_in_schema=False,
)
def sessions_compat(
    request: Request,
    db=Depends(__import__("app.database", fromlist=["get_db"]).get_db),
    current_user=Depends(get_optional_user),
):
    found = run_search(db, "sessions")

    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "request": request,
            "current_user": current_user,
            "current_year": 2026,
            "query": "sessions",
            "results": found["results"],
            "total_results": found["total"],
        },
    )


@app.get(
    "/hot-picks",
    include_in_schema=False,
)
def hot_picks_compat(
    request: Request,
    db=Depends(__import__("app.database", fromlist=["get_db"]).get_db),
    current_user=Depends(get_optional_user),
):
    found = run_search(db, "hot")

    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "request": request,
            "current_user": current_user,
            "current_year": 2026,
            "query": "hot",
            "results": found["results"],
            "total_results": found["total"],
        },
    )


# ----------------------------------------------------------------------
# DASHBOARD COMPATIBILITY
# ----------------------------------------------------------------------

@app.get(
    "/artist/dashboard",
    include_in_schema=False,
)
@app.get(
    "/creator/dashboard",
    include_in_schema=False,
)
@app.get(
    "/producer/dashboard",
    include_in_schema=False,
)
@app.get(
    "/dashboard/home",
    include_in_schema=False,
)
@app.get(
    "/dashboard/index",
    include_in_schema=False,
)
def dashboard_alias(
    user=Depends(require_creator),
):
    return RedirectResponse(
        url="/dashboard",
        status_code=303,
    )


# ----------------------------------------------------------------------
# HEALTH
# ----------------------------------------------------------------------

@app.api_route(
    "/healthz",
    methods=["GET", "HEAD"],
)
def healthz():
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "env": settings.APP_ENV,
        "storage": settings.MEDIA_STORAGE,
        "r2_enabled": settings.r2_enabled,
        "r2_bucket_configured": bool(
            settings.R2_BUCKET_NAME
        ),
        "r2_endpoint_configured": bool(
            settings.r2_endpoint_url
        ),
    }


# ----------------------------------------------------------------------
# ERRORS
# ----------------------------------------------------------------------

@app.exception_handler(
    StarletteHTTPException
)
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
            url=(
                "/login?"
                "error=Please%20log%20in%20to%20continue."
            ),
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


@app.exception_handler(
    RequestValidationError
)
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
