"""
BeatHub authentication routes.

One login is used by everyone:

    Buyer / Artist  -> email
    Creator         -> email
    Admin           -> username or email

The user's role determines the destination after login.
"""

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
    SESSION_COOKIE_NAME,
    get_role_name,
)
from app.utils.security import (
    create_access_token,
    hash_password,
    verify_password,
)


router = APIRouter(
    tags=["auth"]
)

templates = Jinja2Templates(
    directory="app/templates"
)


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

    return value or (
        f"producer-{secrets.token_hex(4)}"
    )


def dashboard_url_for_user(
    user: User,
) -> str:
    """
    Single source of truth for post-login routing.
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
    context = {
        "request": request,
        "current_user": current_user,
        "current_year": datetime.utcnow().year,
    }

    context.update(extra)

    return context


# ----------------------------------------------------------------------
# SIGNUP
# ----------------------------------------------------------------------

@router.get("/signup")
def signup_page(
    request: Request,
):
    return templates.TemplateResponse(
        request,
        "signup.html",
        base_context(request),
    )


@router.post("/signup")
def signup_submit(
    request: Request,
    db: Session = Depends(get_db),
    stage_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    role: str = Form("buyer"),
    agree_terms: str = Form(None),
):
    def error(message: str):
        return templates.TemplateResponse(
            request,
            "signup.html",
            base_context(
                request,
                error=message,
            ),
            status_code=400,
        )

    stage_name = (
        stage_name or ""
    ).strip()

    email_norm = (
        email or ""
    ).strip().lower()

    role = (
        role or "buyer"
    ).strip().lower()

    if not stage_name:
        return error(
            "Stage name / artist name is required."
        )

    if not email_norm:
        return error(
            "Email address is required."
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

    if role in {
        "artist",
        "buyer",
        "customer",
        "user",
    }:
        user_role = UserRole.BUYER

    elif role in {
        "creator",
        "producer",
    }:
        user_role = UserRole.CREATOR

    else:
        user_role = UserRole.BUYER

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

    user = User(
        id=str(uuid.uuid4()),
        email=email_norm,
        username=None,
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

    base_slug = slugify(
        stage_name
    )

    slug = base_slug
    suffix = 1

    while (
        db.query(Profile)
        .filter(Profile.slug == slug)
        .first()
    ):
        suffix += 1
        slug = f"{base_slug}-{suffix}"

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

    db.refresh(user)

    token = create_access_token(
        subject=user.id
    )

    destination = dashboard_url_for_user(
        user
    )

    response = RedirectResponse(
        url=(
            f"{destination}"
            "?success=Account created. "
            "Welcome to BeatHub!"
        ),
        status_code=303,
    )

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=COOKIE_MAX_AGE,
        samesite="lax",
        secure=False,
        path="/",
    )

    return response


# ----------------------------------------------------------------------
# LOGIN
# ----------------------------------------------------------------------

@router.get("/login")
def login_page(
    request: Request,
):
    return templates.TemplateResponse(
        request,
        "login.html",
        base_context(request),
    )


@router.post("/login")
def login_submit(
    request: Request,
    db: Session = Depends(get_db),
    identifier: str = Form(...),
    password: str = Form(...),
):
    def error(message: str):
        return templates.TemplateResponse(
            request,
            "login.html",
            base_context(
                request,
                error=message,
                identifier=identifier,
            ),
            status_code=401,
        )

    identifier_norm = (
        identifier or ""
    ).strip().lower()

    if not identifier_norm:
        return error(
            "Please enter your email or username."
        )

    if not password:
        return error(
            "Please enter your password."
        )

    # --------------------------------------------------------------
    # FIRST: normal email lookup
    # --------------------------------------------------------------

    user = (
        db.query(User)
        .filter(
            User.email == identifier_norm
        )
        .first()
    )

    # --------------------------------------------------------------
    # SECOND: username lookup
    #
    # This is primarily used by the administrator, but it also
    # allows future accounts to use usernames.
    # --------------------------------------------------------------

    if not user:
        user = (
            db.query(User)
            .filter(
                User.username == identifier_norm
            )
            .first()
        )

    # --------------------------------------------------------------
    # THIRD: existing producer profile slug compatibility
    #
    # Keeps your previous login behavior working.
    # --------------------------------------------------------------

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

    if not verify_password(
        password,
        user.hashed_password,
    ):
        return error(
            "Invalid credentials. Please try again."
        )

    if not user.is_active:
        return error(
            "This account has been deactivated. "
            "Contact support."
        )

    db.refresh(user)

    token = create_access_token(
        subject=user.id
    )

    destination = dashboard_url_for_user(
        user
    )

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
        secure=False,
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
        base_context(request),
    )


@router.post("/forgot-password")
def forgot_password_submit(
    request: Request,
    db: Session = Depends(get_db),
    email: str = Form(...),
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
            token=token,
            valid=valid,
        ),
    )


@router.post("/reset-password")
def reset_password_submit(
    request: Request,
    db: Session = Depends(get_db),
    token: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
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
