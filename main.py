import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import Base, engine, get_db

from app.routers import (
    admin,
    auth,
    checkout,
    dashboard,
    merchandise,
    mpesa_callback,
    music,
    pages,
)

from app.services.search import run_search

from app.utils.deps import (
    get_optional_user,
    require_creator,
    require_admin,
)


# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger("beathub")


# ============================================================
# PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
APP_DIR = BASE_DIR / "app"

TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"


# ============================================================
# APPLICATION
# ============================================================

app = FastAPI(
    title=getattr(
        settings,
        "APP_NAME",
        "BeatHub",
    ),
    description=(
        "BeatHub — music, beats, sessions and creator merchandise."
    ),
)


# ============================================================
# SESSION SECURITY
# ============================================================
#
# Keep the existing authentication/session architecture.
#
# SESSION_SECRET:
#   Configure this in Render environment variables.
#
# SESSION_HTTPS_ONLY:
#   Set to true in production on HTTPS.
#
# SESSION_MAX_AGE:
#   Defaults to 30 days.
#
# ============================================================

session_secret = (
    os.getenv(
        "SESSION_SECRET",
        "",
    ).strip()
)

if not session_secret:
    logger.warning(
        "SESSION_SECRET is not configured. "
        "Set SESSION_SECRET in the deployment environment."
    )

session_https_only = (
    os.getenv(
        "SESSION_HTTPS_ONLY",
        "true",
    )
    .strip()
    .lower()
    == "true"
)

try:
    session_max_age = int(
        os.getenv(
            "SESSION_MAX_AGE",
            str(60 * 60 * 24 * 30),
        )
    )
except (
    TypeError,
    ValueError,
):
    session_max_age = 60 * 60 * 24 * 30


app.add_middleware(
    SessionMiddleware,
    secret_key=(
        session_secret
        or "CHANGE_THIS_SESSION_SECRET_IN_RENDER"
    ),
    same_site="lax",
    https_only=session_https_only,
    max_age=session_max_age,
)


# ============================================================
# STATIC FILES
# ============================================================

if STATIC_DIR.exists():
    app.mount(
        "/static",
        StaticFiles(
            directory=str(STATIC_DIR),
        ),
        name="static",
    )


# ============================================================
# TEMPLATES
# ============================================================

templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR),
)


# ============================================================
# DATABASE
# ============================================================
#
# Keep the existing SQLAlchemy model initialization.
#
# The merchandise feature creates its own additive table when
# the merchandise routes are first used. This avoids changing
# the existing music/order schema.
#
# ============================================================

Base.metadata.create_all(
    bind=engine,
)


# ============================================================
# ROUTERS
# ============================================================

# Authentication
app.include_router(
    auth.router,
)

# Public pages/home/search
app.include_router(
    pages.router,
)

# Beats, tracks, albums, profiles and downloads
app.include_router(
    music.router,
)

# Beat/session checkout
app.include_router(
    checkout.router,
)

# M-Pesa callback processing
app.include_router(
    mpesa_callback.router,
)

# Producer dashboard
app.include_router(
    dashboard.router,
)

# Creator merchandise
app.include_router(
    merchandise.router,
)

# Administration
app.include_router(
    admin.router,
)


# ============================================================
# PUBLIC SEARCH COMPATIBILITY ROUTES
# ============================================================

@app.get(
    "/beats",
    include_in_schema=False,
)
def beats_compat(
    request: Request,
    current_user=Depends(
        get_optional_user,
    ),
    db=Depends(
        get_db,
    ),
):
    """
    Legacy /beats compatibility route.

    If the music router already owns /beats, FastAPI will use
    the first registered matching route. This remains here for
    compatibility with older deployments/templates.
    """

    found = run_search(
        db,
        "beats",
    )

    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "request": request,
            "current_user": current_user,
            "user": current_user,
            "current_year": 2026,
            "query": "beats",
            "results": found.get(
                "results",
                {},
            ),
            "total_results": found.get(
                "total",
                0,
            ),
        },
    )


@app.get(
    "/sessions",
    include_in_schema=False,
)
def sessions_compat(
    request: Request,
    current_user=Depends(
        get_optional_user,
    ),
    db=Depends(
        get_db,
    ),
):
    """
    Legacy /sessions compatibility route.
    """

    found = run_search(
        db,
        "sessions",
    )

    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "request": request,
            "current_user": current_user,
            "user": current_user,
            "current_year": 2026,
            "query": "sessions",
            "results": found.get(
                "results",
                {},
            ),
            "total_results": found.get(
                "total",
                0,
            ),
        },
    )


@app.get(
    "/hot-picks",
    include_in_schema=False,
)
def hot_picks_compat(
    request: Request,
    current_user=Depends(
        get_optional_user,
    ),
    db=Depends(
        get_db,
    ),
):
    """
    Legacy /hot-picks compatibility route.
    """

    found = run_search(
        db,
        "hot",
    )

    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "request": request,
            "current_user": current_user,
            "user": current_user,
            "current_year": 2026,
            "query": "hot",
            "results": found.get(
                "results",
                {},
            ),
            "total_results": found.get(
                "total",
                0,
            ),
        },
    )


# ============================================================
# DASHBOARD COMPATIBILITY
# ============================================================

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
    user=Depends(
        require_creator,
    ),
):
    """
    Keep all older dashboard URLs working.
    """

    return RedirectResponse(
        url="/dashboard",
        status_code=303,
    )


# ============================================================
# MERCHANDISE COMPATIBILITY ROUTES
# ============================================================
#
# These aliases make the merchandise area easier to reach from
# older/newer templates without changing the main merchandise
# router.
#
# Primary merchandise routes remain:
#
#   /merch
#   /merch/{slug}
#   /dashboard/merch
#   /dashboard/merch/new
#   /store/{slug}/merch
#
# ============================================================

@app.get(
    "/merchandise",
    include_in_schema=False,
)
def merchandise_alias():
    return RedirectResponse(
        url="/merch",
        status_code=303,
    )


@app.get(
    "/shop",
    include_in_schema=False,
)
def shop_alias():
    return RedirectResponse(
        url="/merch",
        status_code=303,
    )


@app.get(
    "/dashboard/merchandise",
    include_in_schema=False,
)
def dashboard_merchandise_alias(
    user=Depends(
        require_creator,
    ),
):
    return RedirectResponse(
        url="/dashboard/merch",
        status_code=303,
    )


@app.get(
    "/dashboard/shop",
    include_in_schema=False,
)
def dashboard_shop_alias(
    user=Depends(
        require_creator,
    ),
):
    return RedirectResponse(
        url="/dashboard/merch",
        status_code=303,
    )


# ============================================================
# WITHDRAWAL COMPATIBILITY
# ============================================================

@app.get(
    "/creator/withdraw",
    include_in_schema=False,
)
def creator_withdraw_alias(
    user=Depends(
        require_creator,
    ),
):
    return RedirectResponse(
        url="/dashboard/withdraw",
        status_code=303,
    )


@app.get(
    "/producer/withdraw",
    include_in_schema=False,
)
def producer_withdraw_alias(
    user=Depends(
        require_creator,
    ),
):
    return RedirectResponse(
        url="/dashboard/withdraw",
        status_code=303,
    )


@app.get(
    "/admin/withdrawal",
    include_in_schema=False,
)
def admin_withdraw_alias(
    user=Depends(
        require_admin,
    ),
):
    return RedirectResponse(
        url="/admin/withdraw",
        status_code=303,
    )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.api_route(
    "/healthz",
    methods=[
        "GET",
        "HEAD",
    ],
)
def healthz():
    """
    Render health check.

    Does not perform database writes.
    """

    return {
        "status": "ok",
        "app": getattr(
            settings,
            "APP_NAME",
            "BeatHub",
        ),
        "env": getattr(
            settings,
            "APP_ENV",
            "production",
        ),
        "storage": getattr(
            settings,
            "MEDIA_STORAGE",
            "unknown",
        ),
        "r2_enabled": getattr(
            settings,
            "r2_enabled",
            False,
        ),
        "r2_bucket_configured": bool(
            getattr(
                settings,
                "R2_BUCKET_NAME",
                None,
            ),
        ),
        "r2_endpoint_configured": bool(
            getattr(
                settings,
                "r2_endpoint_url",
                None,
            ),
        ),
    }


@app.api_route(
    "/health",
    methods=[
        "GET",
        "HEAD",
    ],
    include_in_schema=False,
)
def health_compat():
    """
    Additional compatibility health endpoint.
    """

    return {
        "status": "ok",
    }


# ============================================================
# ERROR HELPERS
# ============================================================

def _error_context(
    request: Request,
    current_user=None,
    **extra,
):
    context = {
        "request": request,
        "current_user": current_user,
        "user": current_user,
        "current_year": 2026,
    }

    context.update(extra)

    return context


# ============================================================
# HTTP EXCEPTIONS
# ============================================================

@app.exception_handler(
    StarletteHTTPException,
)
async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
):
    """
    Central HTTP error handling.

    404 -> custom 404 page
    401 -> login
    403 -> custom forbidden page
    other -> custom 500/error page
    """

    if exc.status_code == 404:
        return templates.TemplateResponse(
            request,
            "errors/404.html",
            _error_context(
                request,
                detail=exc.detail,
            ),
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
            _error_context(
                request,
                detail=exc.detail,
            ),
            status_code=403,
        )

    return templates.TemplateResponse(
        request,
        "errors/500.html",
        _error_context(
            request,
            detail=exc.detail,
        ),
        status_code=exc.status_code,
    )


# ============================================================
# VALIDATION ERRORS
# ============================================================

@app.exception_handler(
    RequestValidationError,
)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
):
    """
    Handle invalid form/query/path parameters cleanly.
    """

    return templates.TemplateResponse(
        request,
        "errors/400.html",
        _error_context(
            request,
            errors=exc.errors(),
        ),
        status_code=422,
    )


# ============================================================
# UNHANDLED ERRORS
# ============================================================

@app.exception_handler(
    Exception,
)
async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
):
    """
    Last-resort application error handler.

    Full exception is logged server-side.
    Sensitive exception details are not exposed to visitors.
    """

    logger.exception(
        "Unhandled BeatHub error: %s",
        exc,
    )

    return templates.TemplateResponse(
        request,
        "errors/500.html",
        _error_context(
            request,
            detail=None,
        ),
        status_code=500,
    )
