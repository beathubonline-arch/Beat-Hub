"""BeatHub authentication routes."""

import hmac
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.profile import Profile
from app.models.user import User, UserRole
from app.utils.deps import ADMIN_SESSION_SUBJECT, SESSION_COOKIE_NAME, get_role_name, require_user
from app.utils.security import create_access_token, hash_password, verify_password

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="app/templates")
COOKIE_MAX_AGE = 60 * 60 * 24 * 7


def _safe_next_url(value: str | None) -> str:
    candidate = (value or "").strip()
    if not candidate or not candidate.startswith("/") or candidate.startswith("//"):
        return ""
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        return ""
    return candidate


def _cookie_secure() -> bool:
    """Use secure auth cookies in production without referencing a missing setting."""
    return bool(settings.is_production)


def _set_auth_cookie(response: RedirectResponse, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=COOKIE_MAX_AGE,
        samesite="lax",
        secure=_cookie_secure(),
        path="/",
    )


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "").strip()).strip("-").lower()
    return value or f"producer-{secrets.token_hex(4)}"


def dashboard_url_for_user(user: User) -> str:
    role = get_role_name(user)
    if role == "admin":
        return "/admin"
    if role == "creator":
        return "/dashboard"
    return "/"


def _password_matches(plain_password: str, stored_password: str) -> bool:
    """Verify a password while retaining compatibility with legacy hashes."""
    if not stored_password:
        return False
    try:
        return verify_password(plain_password, stored_password)
    except Exception:
        return False


@router.get("/login")
def login_page(request: Request, next: str = "", error: str = ""):
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "next": _safe_next_url(next),
            "error": error,
        },
    )


@router.post("/login")
def login_submit(
    request: Request,
    identifier: str = Form(""),
    email: str = Form(""),
    password: str = Form(...),
    next: str = Form(""),
    db: Session = Depends(get_db),
):
    # Accept both the current login form's `identifier` field and legacy
    # clients/forms that still submit `email`.
    login_identifier = (identifier or email).strip().lower()
    user = db.query(User).filter(User.email == login_identifier).first()

    if not user or not _password_matches(password, user.password_hash):
        return RedirectResponse(
            url=f"/login?error=Invalid%20email%20or%20password&next={_safe_next_url(next)}",
            status_code=303,
        )

    if hasattr(user, "is_active") and not user.is_active:
        return RedirectResponse(
            url=f"/login?error=Your%20account%20is%20inactive&next={_safe_next_url(next)}",
            status_code=303,
        )

    token = create_access_token(subject=str(user.id), extra_claims={"role": get_role_name(user)})
    response = RedirectResponse(url=_safe_next_url(next) or dashboard_url_for_user(user), status_code=303)
    _set_auth_cookie(response, token)
    return response


@router.get("/logout")
def logout(request: Request):
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


@router.get("/signup")
def signup_page(request: Request):
    return templates.TemplateResponse("signup.html", {"request": request, "error": ""})


@router.post("/signup")
def signup_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    role: str = Form("buyer"),
    display_name: str = Form(""),
    db: Session = Depends(get_db),
):
    normalized_email = email.strip().lower()
    display_name = display_name.strip()
    role = role.strip().lower()

    if password != confirm_password:
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error": "Passwords do not match."},
            status_code=400,
        )

    if not normalized_email or "@" not in normalized_email:
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error": "Please enter a valid email address."},
            status_code=400,
        )

    if len(password) < 8:
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error": "Password must be at least 8 characters."},
            status_code=400,
        )

    if role not in {"buyer", "creator"}:
        role = "buyer"

    if role == "creator" and not display_name:
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error": "Please enter your artist/stage name."},
            status_code=400,
        )

    if db.query(User).filter(User.email == normalized_email).first():
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error": "An account with that email already exists."},
            status_code=400,
        )

    try:
        user = User(
            email=normalized_email,
            password_hash=hash_password(password),
            role=UserRole.CREATOR if role == "creator" else UserRole.BUYER,
            is_active=True,
        )
        db.add(user)
        db.flush()

        if role == "creator":
            base_slug = slugify(display_name)
            slug = base_slug
            counter = 2
            while db.query(Profile).filter(Profile.slug == slug).first():
                slug = f"{base_slug}-{counter}"
                counter += 1
            profile = Profile(user_id=user.id, display_name=display_name, slug=slug)
            db.add(profile)

        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error": "An account with those details already exists."},
            status_code=400,
        )

    token = create_access_token(subject=str(user.id), extra_claims={"role": get_role_name(user)})
    response = RedirectResponse(url=dashboard_url_for_user(user), status_code=303)
    _set_auth_cookie(response, token)
    return response
