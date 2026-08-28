import enum
import re
import secrets
from datetime import datetime, timedelta
from urllib.parse import quote, unquote, urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.utils.security import create_access_token, verify_password
from app.templates import templates

router = APIRouter()

SESSION_COOKIE_NAME = "beathub_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30


def get_role_name(user: User) -> str:
    role = getattr(user, "role", "buyer")
    return getattr(role, "value", role) or "buyer"


def _cookie_secure() -> bool:
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
    if not stored_password:
        return False
    try:
        return verify_password(plain_password, stored_password)
    except Exception:
        return False


def _safe_next_url(value: str) -> str:
    value = unquote((value or "").strip())
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return ""
    if not value.startswith("/") or value.startswith("//"):
        return ""
    return value


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
    login_identifier = (identifier or email).strip().lower()
    user = db.query(User).filter(User.email == login_identifier).first()

    if not user or not _password_matches(password, getattr(user, "hashed_password", "")):
        return RedirectResponse(
            url=f"/login?error=Invalid%20email%20or%20password&next={quote(_safe_next_url(next), safe='')}",
            status_code=303,
        )

    if hasattr(user, "is_active") and not user.is_active:
        return RedirectResponse(
            url=f"/login?error=Your%20account%20is%20inactive&next={quote(_safe_next_url(next), safe='')}",
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
