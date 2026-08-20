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
    is_admin,
    is_creator,
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
    Central dashboard routing.

    Creator/admin -> /dashboard
    Buyer/artist  -> /artist/dashboard

    Creator detection is based on profile.is_producer so that
    UserRole enum changes cannot create redirect loops.
    """

    if is_creator(user) or is_admin(user):
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
def signup_page(request: Request):
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

    stage_name = (stage_name or "").strip()
    email_norm = (email or "").strip().lower()
    role = (role or "buyer").strip().lower()

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

    if role not in {"buyer", "creator"}:
        role = "buyer"

    user_role = (
        UserRole.CREATOR
        if role == "creator"
        else UserRole.BUYER
    )

    existing_user = (
        db.query(User)
        .filter(User.email == email_norm)
        .first()
    )

    if existing_user:
        return error(
            "An account with this email already exists."
        )

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

    base_slug = slugify(stage_name)

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
        is_producer=(role == "creator"),
    )

    db.add(profile)

    try:
        db.commit()

    except IntegrityError:
        db.rollback()

        return error(
            "Could not create account. Please try again."
        )

    token = create_access_token(
        subject=user.id
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
        secure=False,
    )

    return response


# ----------------------------------------------------------------------
# LOGIN
# ----------------------------------------------------------------------

@router.get("/login")
def login_page(request: Request):
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
            ),
            status_code=401,
        )

    identifier_norm = (
        identifier or ""
    ).strip().lower()

    user = (
        db.query(User)
        .filter(User.email == identifier_norm)
        .first()
    )

    if not user and identifier_norm:

        possible_slug = slugify(identifier_norm)

        profile = (
            db.query(Profile)
            .filter(Profile.slug == possible_slug)
            .first()
        )

        if profile:
            user = (
                db.query(User)
                .filter(User.id == profile.user_id)
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
            "This account has been deactivated. Contact support."
        )

    token = create_access_token(
        subject=user.id
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
        secure=False,
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
        httponly=True,
        samesite="lax",
    )

    return response


@router.post("/logout")
def logout(request: Request):
    return perform_logout()


@router.get("/logout")
def logout_get(request: Request):
    return perform_logout()


# ----------------------------------------------------------------------
# FORGOT PASSWORD
# ----------------------------------------------------------------------

@router.get("/forgot-password")
def forgot_password_page(request: Request):
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
        .filter(User.email == email_norm)
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
        .filter(User.reset_token == token)
        .first()
    )

    valid = bool(
        user
        and user.reset_token_expires
        and user.reset_token_expires > datetime.utcnow()
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
        .filter(User.reset_token == token)
        .first()
    )

    valid = bool(
        user
        and user.reset_token_expires
        and user.reset_token_expires > datetime.utcnow()
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

    user.hashed_password = hash_password(password)
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
