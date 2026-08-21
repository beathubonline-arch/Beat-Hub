import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models.user import User, UserRole
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
from app.utils.security import hash_password


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


# ----------------------------------------------------------------------
# STATIC FILES
# ----------------------------------------------------------------------

if STATIC_DIR.exists():
    app.mount(
        "/static",
        StaticFiles(directory=str(STATIC_DIR)),
        name="static",
    )


# ----------------------------------------------------------------------
# DATABASE
# ----------------------------------------------------------------------

Base.metadata.create_all(
    bind=engine
)


# ----------------------------------------------------------------------
# ADMIN ACCOUNT INITIALIZATION
# ----------------------------------------------------------------------

def initialize_admin_account():
    """
    Create the BeatHub administrator from Render environment variables.

    Supported environment variables:

        ADMIN_EMAIL
        ADMIN_USERNAME
        ADMIN_PASSWORD

    ADMIN_EMAIL is preferred.

    ADMIN_USERNAME is supported as a fallback so an existing Render
    variable named ADMIN_USERNAME can also be used.

    The admin is created only when an account with that email does
    not already exist.

    Existing users are never overwritten automatically.
    """

    admin_email = (
        os.getenv("ADMIN_EMAIL")
        or os.getenv("ADMIN_USERNAME")
        or ""
    ).strip().lower()

    admin_password = (
        os.getenv("ADMIN_PASSWORD")
        or ""
    )

    # --------------------------------------------------------------
    # Nothing to do if admin credentials are not configured.
    # --------------------------------------------------------------

    if not admin_email or not admin_password:
        logger.info(
            "Admin initialization skipped: "
            "ADMIN_EMAIL/ADMIN_USERNAME and ADMIN_PASSWORD "
            "are not both configured."
        )
        return

    # --------------------------------------------------------------
    # Basic validation.
    # --------------------------------------------------------------

    if len(admin_password) < 8:
        logger.error(
            "ADMIN_PASSWORD must contain at least 8 characters. "
            "Admin initialization was skipped."
        )
        return

    db = SessionLocal()

    try:
        # ----------------------------------------------------------
        # Look for an existing account.
        # ----------------------------------------------------------

        existing_user = (
            db.query(User)
            .filter(User.email == admin_email)
            .first()
        )

        if existing_user:

            existing_role = getattr(
                existing_user.role,
                "value",
                existing_user.role,
            )

            existing_role = str(
                existing_role
            ).strip().lower()

            # ------------------------------------------------------
            # Already an admin.
            # ------------------------------------------------------

            if existing_role == "admin":
                logger.info(
                    "BeatHub admin account already exists: %s",
                    admin_email,
                )
                return

            # ------------------------------------------------------
            # An ordinary account already owns this email.
            #
            # We deliberately DO NOT promote it automatically.
            # ------------------------------------------------------

            logger.warning(
                "ADMIN_EMAIL %s already belongs to a non-admin "
                "account. Admin was NOT created or promoted.",
                admin_email,
            )
            return

        # ----------------------------------------------------------
        # Create administrator.
        # ----------------------------------------------------------

        admin_user = User(
            email=admin_email,
            hashed_password=hash_password(
                admin_password
            ),
            role=UserRole.ADMIN,
            is_active=True,
            is_verified=True,
        )

        db.add(admin_user)
        db.commit()
        db.refresh(admin_user)

        logger.info(
            "BeatHub administrator created successfully: %s",
            admin_email,
        )

    except Exception:
        db.rollback()

        logger.exception(
            "Failed to initialize BeatHub administrator."
        )

    finally:
        db.close()


# ----------------------------------------------------------------------
# INITIALIZE ADMIN
# ----------------------------------------------------------------------

initialize_admin_account()


# ----------------------------------------------------------------------
# ROUTERS
# ----------------------------------------------------------------------

app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(music.router)
app.include_router(checkout.router)
app.include_router(mpesa_callback.router)
app.include_router(dashboard.router)
app.include_router(admin.router)


# ----------------------------------------------------------------------
# HOMEPAGE COMPATIBILITY ROUTES
# ----------------------------------------------------------------------
# These are intentionally defined here so the public homepage links
# always exist even if an older pages.py is deployed.
# ----------------------------------------------------------------------

@app.get(
    "/beats",
    include_in_schema=False,
)
def beats_compat(
    request: Request,
    db=Depends(
        __import__(
            "app.database",
            fromlist=["get_db"],
        ).get_db
    ),
    current_user=Depends(get_optional_user),
):
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
    db=Depends(
        __import__(
            "app.database",
            fromlist=["get_db"],
        ).get_db
    ),
    current_user=Depends(get_optional_user),
):
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
    db=Depends(
        __import__(
            "app.database",
            fromlist=["get_db"],
        ).get_db
    ),
    current_user=Depends(get_optional_user),
):
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
