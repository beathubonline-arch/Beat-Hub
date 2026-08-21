import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import inspect, text
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
# DATABASE COMPATIBILITY
# ----------------------------------------------------------------------

def ensure_username_column():
    """
    Add the username column to existing BeatHub databases.

    create_all() does not modify existing tables, so this check is
    required when upgrading an existing installation.
    """

    try:
        inspector = inspect(engine)

        columns = {
            column["name"]
            for column in inspector.get_columns("users")
        }

        if "username" in columns:
            return

        logger.info(
            "Adding username column to users table."
        )

        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE users "
                    "ADD COLUMN username VARCHAR(100)"
                )
            )

        logger.info(
            "Username column added successfully."
        )

    except Exception:
        logger.exception(
            "Failed to add username column."
        )
        raise


ensure_username_column()


# ----------------------------------------------------------------------
# ADMIN ACCOUNT INITIALIZATION
# ----------------------------------------------------------------------

def initialize_admin_account():
    """
    Creates the BeatHub administrator from Render environment variables.

    Required:

        ADMIN_USERNAME
        ADMIN_PASSWORD

    Optional:

        ADMIN_EMAIL

    ADMIN_USERNAME is the login identity for the administrator.

    ADMIN_EMAIL is stored as the admin's email address if supplied.

    If ADMIN_EMAIL is not supplied, a private internal placeholder
    address is generated from the username because the existing User
    model requires email to be non-null.

    Existing users are never automatically promoted to admin.
    """

    admin_username = (
        os.getenv("ADMIN_USERNAME")
        or ""
    ).strip().lower()

    admin_password = (
        os.getenv("ADMIN_PASSWORD")
        or ""
    )

    admin_email = (
        os.getenv("ADMIN_EMAIL")
        or ""
    ).strip().lower()

    if not admin_username or not admin_password:
        logger.info(
            "Admin initialization skipped: "
            "ADMIN_USERNAME and ADMIN_PASSWORD are not both set."
        )
        return

    if len(admin_password) < 8:
        logger.error(
            "ADMIN_PASSWORD must contain at least 8 characters. "
            "Admin initialization skipped."
        )
        return

    db = SessionLocal()

    try:
        # ----------------------------------------------------------
        # Look for admin by username.
        # ----------------------------------------------------------

        existing_admin = (
            db.query(User)
            .filter(
                User.username == admin_username
            )
            .first()
        )

        if existing_admin:

            role = getattr(
                existing_admin.role,
                "value",
                existing_admin.role,
            )

            role = str(
                role
            ).strip().lower()

            if role == "admin":
                logger.info(
                    "BeatHub admin already exists: %s",
                    admin_username,
                )
                return

            logger.warning(
                "Username %s belongs to a non-admin account. "
                "No automatic promotion performed.",
                admin_username,
            )
            return

        # ----------------------------------------------------------
        # Check whether ADMIN_EMAIL already belongs to an account.
        # ----------------------------------------------------------

        if admin_email:
            email_owner = (
                db.query(User)
                .filter(
                    User.email == admin_email
                )
                .first()
            )

            if email_owner:
                role = getattr(
                    email_owner.role,
                    "value",
                    email_owner.role,
                )

                role = str(
                    role
                ).strip().lower()

                if role == "admin":
                    # Give an existing admin the configured username
                    # if it doesn't conflict.
                    if not email_owner.username:
                        email_owner.username = admin_username
                        db.commit()

                    logger.info(
                        "Existing admin linked to username: %s",
                        admin_username,
                    )
                    return

                logger.warning(
                    "ADMIN_EMAIL %s already belongs to a "
                    "non-admin account. Admin was not created.",
                    admin_email,
                )
                return

        # ----------------------------------------------------------
        # If no real admin email was supplied, use an internal
        # placeholder because User.email is currently required.
        # ----------------------------------------------------------

        if not admin_email:
            admin_email = (
                f"{admin_username}@admin.beathub.local"
            )

        # ----------------------------------------------------------
        # Final email uniqueness check.
        # ----------------------------------------------------------

        email_owner = (
            db.query(User)
            .filter(
                User.email == admin_email
            )
            .first()
        )

        if email_owner:
            logger.warning(
                "Cannot create admin because email %s "
                "already belongs to another account.",
                admin_email,
            )
            return

        # ----------------------------------------------------------
        # Create administrator.
        # ----------------------------------------------------------

        admin_user = User(
            id=None,
            email=admin_email,
            username=admin_username,
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
            admin_username,
        )

    except Exception:
        db.rollback()

        logger.exception(
            "Failed to initialize BeatHub administrator."
        )

    finally:
        db.close()


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
