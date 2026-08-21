"""
BeatHub authentication routes.

One shared login is used for:
    - Buyers / artists
    - Creators / producers
    - Administrator

Administrator credentials are stored in Render environment variables:
    ADMIN_USERNAME
    ADMIN_PASSWORD
"""

import hmac
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.profile import Profile
from app.models.user import User, UserRole
from app.utils.deps import (
    ADMIN_SESSION_SUBJECT,
    SESSION_COOKIE_NAME,
    get_role_name,
)
from app.utils.security import (
    create_access_token,
    hash_password,
    verify_password,
)


router = APIRouter(tags=["auth"])

templates = Jinja2Templates(directory="app/templates")

COOKIE_MAX_AGE = 60 * 60 * 24 * 7


# ----------------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------------

def slugify(value: str) -> str:
    value = re.sub(
        r"[^a-zA-Z0-9]+",
        "-",
        (value or "").strip(),
    ).strip("-").lower()

    return value or f"producer-{secrets.token_hex(4)}"


def dashboard_url_for_user(user: User) -> str:
    """
    Single source of truth for normal user post-login routing.
    """

    role = get_role_name(user)

    if role == "admin":
        return "/admin"

    if role == "creator":
        return "/dashboard"

    return "/artist/dashboard"


def base_context(
    request: Request,
    current_user=None,
    **extra,
):
    """
    Shared template context.

    Important:
    Never access request.form from Jinja templates.
    Form values are explicitly supplied here.
    """

    context = {
        "request": request,
        "current_user": current_user,
        "current_year": datetime.utcnow().year,

        "error": None,
        "success": None,

        # Signup values
        "stage_name": "",
        "email": "",
        "password": "",
        "confirm_password": "",
        "role": "creator",
        "agree_terms": False,

        # Login values
        "identifier": "",

        # Password reset
        "token": "",
        "valid": False,
    }

    context.update(extra)

    return context


def get_admin_credentials():
    """
    Read administrator credentials from environment variables.

    Render:
        ADMIN_USERNAME
        ADMIN_PASSWORD
    """

    username = (
        os.getenv("ADMIN_USERNAME")
        or ""
    ).strip()

    password = os.getenv("ADMIN_PASSWORD") or ""

    return username, password


def verify_admin_credentials(
    identifier: str,
    password: str,
) -> bool:
    """
    Securely compare the submitted admin credentials
    with the credentials stored in Render.
    """

    configured_username, configured_password = (
        get_admin_credentials()
    )

    if not configured_username or not configured_password:
        return False

    submitted_username = (
        identifier or ""
    ).strip()

    submitted_password = password or ""

    username_match = hmac.compare_digest(
        submitted_username,
        configured_username,
    )

    password_match = hmac.compare_digest(
        submitted_password,
        configured_password,
    )

    return username_match and password_match


def create_admin_session_token() -> str:
    """
    Creates a session token specifically for the
    Render-configured administrator.
    """

    return create_access_token(
        subject=ADMIN_SESSION_SUBJECT,
        extra_claims={
            "role": "admin",
            "admin": True,
        },
    )


# ----------------------------------------------------------------------
# SIGNUP
# ----------------------------------------------------------------------

@router.get("/signup")
def signup_page(
    request: Request,
):
    """
    Display signup page.

    All form defaults are explicitly supplied to Jinja.
    This prevents the common Starlette/Jinja error:

        'method object' has no attribute 'get'
    """

    return templates.TemplateResponse(
        request,
        "signup.html",
        base_context(
            request,
            current_user=None,

            stage_name="",
            email="",
            password="",
            confirm_password="",
            role="creator",
            agree_terms=False,

            error=None,
            success=None,
        ),
    )


@router.post("/signup")
def signup_submit(
    request: Request,
    db: Session = Depends(get_db),
    stage_name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
    role: str = Form("creator"),
    agree_terms: str = Form(None),
):
    """
    Create a BeatHub buyer or creator account.

    Public signup can NEVER create an admin account.
    """

    stage_name = (
        stage_name or ""
    ).strip()

    email_norm = (
        email or ""
    ).strip().lower()

    password = password or ""
    confirm_password = confirm_password or ""

    role = (
        role or "creator"
    ).strip().lower()

    def error(message: str):
        """
        Re-render the signup form while preserving safe
        user-entered fields.

        Password fields are intentionally cleared.
        """

        return templates.TemplateResponse(
            request,
            "signup.html",
            base_context(
                request,
                current_user=None,

                stage_name=stage_name,
                email=email_norm,

                password="",
                confirm_password="",

                role=(
                    "creator"
                    if role in {"creator", "producer"}
                    else "buyer"
                ),

                agree_terms=bool(agree_terms),

                error=message,
                success=None,
            ),
            status_code=400,
        )

    # ------------------------------------------------------------------
    # BASIC VALIDATION
    # ------------------------------------------------------------------

    if not stage_name:
        return error(
            "Stage name / artist name is required."
        )

    if len(stage_name) > 100:
        return error(
            "Stage name is too long."
        )

    if not email_norm:
        return error(
            "Email address is required."
        )

    # Basic email validation.
    if not re.fullmatch(
        r"[^@\s]+@[^@\s]+\.[^@\s]+",
        email_norm,
    ):
        return error(
            "Please enter a valid email address."
        )

    if not agree_terms:
        return error(
            "You must agree to the Terms & Conditions."
        )

    if len(password) < 8:
        return error(
            "Password must be at least 8 characters."
        )

    if password != confirm_password:
        return error(
            "Passwords do not match."
        )

    # ------------------------------------------------------------------
    # PUBLIC ROLE SELECTION
    #
    # Admin can NEVER be created through signup.
    # ------------------------------------------------------------------

    if role in {
        "creator",
        "producer",
    }:
        user_role = UserRole.CREATOR
    else:
        user_role = UserRole.BUYER

    # ------------------------------------------------------------------
    # EXISTING EMAIL
    # ------------------------------------------------------------------

    existing_user = (
        db.query(User)
        .filter(
            User.email == email_norm
        )
        .first()
    )

    if existing_user:
        return error(
            "An account with this email already exists."
        )

    # ------------------------------------------------------------------
    # CREATE USER
    # ------------------------------------------------------------------

    user = User(
        id=str(uuid.uuid4()),
        email=email_norm,
        hashed_password=hash_password(password),
        role=user_role,
        is_active=True,
        is_verified=False,
    )

    db.add(user)

    try:
        db.flush()

    except IntegrityError:
        db.rollback()

        return error(
            "An account with this email already exists."
        )

    # ------------------------------------------------------------------
    # CREATE UNIQUE PUBLIC PROFILE SLUG
    # ------------------------------------------------------------------

    base_slug = slugify(stage_name)

    slug = base_slug
    suffix = 1

    while (
        db.query(Profile)
        .filter(
            Profile.slug == slug
        )
        .first()
    ):
        suffix += 1
        slug = f"{base_slug}-{suffix}"

    # ------------------------------------------------------------------
    # CREATE PROFILE
    # ------------------------------------------------------------------

    profile = Profile(
        id=str(uuid.uuid4()),
        user_id=user.id,
        stage_name=stage_name,
        slug=slug,
        is_producer=(
            user_role == UserRole.CREATOR
        ),
    )

    db.add(profile)

    try:
        db.commit()

    except IntegrityError:
        db.rollback()

        return error(
            "Could not create account. Please try again."
        )

    except Exception:
        db.rollback()
        raise

    db.refresh(user)

    # ------------------------------------------------------------------
    # AUTOMATIC LOGIN
    # ------------------------------------------------------------------

    token = create_access_token(
        subject=user.id,
        extra_claims={
            "role": get_role_name(user),
        },
    )

    destination = dashboard_url_for_user(user)

    response = RedirectResponse(
        url=(
            f"{destination}"
            "?success=Account created. Welcome to BeatHub!"
        ),
        status_code=303,
    )

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=COOKIE_MAX_AGE,
        samesite="lax",
        secure=True,
        path="/",
    )

    return response


# ----------------------------------------------------------------------
# SHARED LOGIN
# ----------------------------------------------------------------------

@router.get("/login")
def login_page(
    request: Request,
):
    return templates.TemplateResponse(
        request,
        "login.html",
        base_context(
            request,
            current_user=None,
            identifier="",
            error=None,
            success=None,
        ),
    )


@router.post("/login")
def login_submit(
    request: Request,
    db: Session = Depends(get_db),
    identifier: str = Form(""),
    password: str = Form(""),
):
    def error(message: str):
        return templates.TemplateResponse(
            request,
            "login.html",
            base_context(
                request,
                current_user=None,
                identifier=identifier_raw,
                error=message,
                success=None,
            ),
            status_code=401,
        )

    identifier_raw = (
        identifier or ""
    ).strip()

    identifier_norm = identifier_raw.lower()

    password = password or ""

    if not identifier_raw:
        return error(
            "Email or username is required."
        )

    if not password:
        return error(
            "Password is required."
        )

    # ------------------------------------------------------------------
    # ADMIN
    # ------------------------------------------------------------------

    if verify_admin_credentials(
        identifier_raw,
        password,
    ):
        token = create_admin_session_token()

        response = RedirectResponse(
            url="/admin",
            status_code=303,
        )

        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            httponly=True,
            max_age=COOKIE_MAX_AGE,
            samesite="lax",
            secure=True,
            path="/",
        )

        return response

    # ------------------------------------------------------------------
    # NORMAL USER BY EMAIL
    # ------------------------------------------------------------------

    user = (
        db.query(User)
        .filter(
            User.email == identifier_norm
        )
        .first()
    )

    # ------------------------------------------------------------------
    # NORMAL USER BY PROFILE SLUG
    # ------------------------------------------------------------------

    if not user and identifier_norm:

        possible_slug = slugify(
            identifier_norm
        )

        profile = (
            db.query(Profile)
            .filter(
                Profile.slug == possible_slug
            )
            .first()
        )

        if profile:
            user = (
                db.query(User)
                .filter(
                    User.id == profile.user_id
                )
                .first()
            )

    if not user:
        return error(
            "Invalid credentials. Please try again."
        )

    if not user.hashed_password:
        return error(
            "This account cannot be logged in with a password."
        )

    if not verify_password(
        password,
        user.hashed_password,
    ):
        return error(
            "Invalid credentials. Please try again."
        )

    if not user.is_active:
        return error(
            "This account has been deactivated. Contact support."
        )

    db.refresh(user)

    token = create_access_token(
        subject=user.id,
        extra_claims={
            "role": get_role_name(user),
        },
    )

    destination = dashboard_url_for_user(user)

    response = RedirectResponse(
        url=destination,
        status_code=303,
    )

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=COOKIE_MAX_AGE,
        samesite="lax",
        secure=True,
        path="/",
    )

    return response


# ----------------------------------------------------------------------
# LOGOUT
# ----------------------------------------------------------------------

def perform_logout() -> RedirectResponse:
    response = RedirectResponse(
        url="/?success=You have been logged out.",
        status_code=303,
    )

    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
    )

    return response


@router.post("/logout")
def logout():
    return perform_logout()


@router.get("/logout")
def logout_get():
    return perform_logout()


# ----------------------------------------------------------------------
# FORGOT PASSWORD
# ----------------------------------------------------------------------

@router.get("/forgot-password")
def forgot_password_page(
    request: Request,
):
    return templates.TemplateResponse(
        request,
        "forgot_password.html",
        base_context(
            request,
            current_user=None,
            email="",
            error=None,
            success=None,
        ),
    )


@router.post("/forgot-password")
def forgot_password_submit(
    request: Request,
    db: Session = Depends(get_db),
    email: str = Form(""),
):
    email_norm = (
        email or ""
    ).strip().lower()

    user = (
        db.query(User)
        .filter(
            User.email == email_norm
        )
        .first()
    )

    if user:

        token = secrets.token_urlsafe(32)

        user.reset_token = token

        user.reset_token_expires = (
            datetime.utcnow()
            + timedelta(hours=1)
        )

        db.commit()

        reset_link = (
            f"/reset-password?token={token}"
        )

        print(
            "[BeatHub] Password reset link "
            f"for {email_norm}: {reset_link}"
        )

    return templates.TemplateResponse(
        request,
        "forgot_password.html",
        base_context(
            request,
            current_user=None,
            email=email_norm,
            success=(
                "If an account exists for "
                "that email, a reset link "
                "has been sent."
            ),
        ),
    )


# ----------------------------------------------------------------------
# RESET PASSWORD
# ----------------------------------------------------------------------

@router.get("/reset-password")
def reset_password_page(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
):
    user = (
        db.query(User)
        .filter(
            User.reset_token == token
        )
        .first()
    )

    valid = bool(
        user
        and user.reset_token_expires
        and user.reset_token_expires
        > datetime.utcnow()
    )

    return templates.TemplateResponse(
        request,
        "reset_password.html",
        base_context(
            request,
            current_user=None,
            token=token,
            valid=valid,
            error=None,
            success=None,
        ),
    )


@router.post("/reset-password")
def reset_password_submit(
    request: Request,
    db: Session = Depends(get_db),
    token: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
):
    user = (
        db.query(User)
        .filter(
            User.reset_token == token
        )
        .first()
    )

    valid = bool(
        user
        and user.reset_token_expires
        and user.reset_token_expires
        > datetime.utcnow()
    )

    if not valid:
        return templates.TemplateResponse(
            request,
            "reset_password.html",
            base_context(
                request,
                current_user=None,
                token=token,
                valid=False,
                error=(
                    "This reset link is invalid "
                    "or has expired."
                ),
            ),
            status_code=400,
        )

    if (
        len(password) < 8
        or password != confirm_password
    ):
        return templates.TemplateResponse(
            request,
            "reset_password.html",
            base_context(
                request,
                current_user=None,
                token=token,
                valid=True,
                error=(
                    "Passwords must match and "
                    "be at least 8 characters."
                ),
            ),
            status_code=400,
        )

    user.hashed_password = hash_password(
        password
    )

    user.reset_token = None
    user.reset_token_expires = None

    db.commit()

    return RedirectResponse(
        url=(
            "/login"
            "?success=Password updated. "
            "Please log in."
        ),
        status_code=303,
    )
