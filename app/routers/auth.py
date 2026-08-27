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


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "").strip()).strip("-").lower()
    return value or f"producer-{secrets.token_hex(4)}"


def dashboard_url_for_user(user: User) -> str:
    role = get_role_name(user)
    if role == "admin":
        return "/admin"
    if role == "creator":
        profile = getattr(user, "profile", None)
        if profile is not None and bool(getattr(profile, "is_artist", False)):
            return "/artist/studio"
        return "/dashboard"
    return "/account"


def base_context(request: Request, current_user=None, **extra):
    context = {"request": request, "current_user": current_user, "current_year": datetime.utcnow().year}
    context.update(extra)
    return context


def get_admin_credentials():
    return (os.getenv("ADMIN_USERNAME") or "").strip(), os.getenv("ADMIN_PASSWORD") or ""


def verify_admin_credentials(identifier: str, password: str) -> bool:
    configured_username, configured_password = get_admin_credentials()
    if not configured_username or not configured_password:
        return False
    return hmac.compare_digest((identifier or "").strip(), configured_username) and hmac.compare_digest(password or "", configured_password)


def create_admin_session_token() -> str:
    return create_access_token(subject=ADMIN_SESSION_SUBJECT, extra_claims={"role": "admin", "admin": True})


def _normalise_signup_role(role: str | None) -> str:
    value = (role or "buyer").strip().lower()
    if value in {"creator", "producer"}:
        return "creator"
    if value == "artist":
        return "artist"
    return "buyer"


def _profile_flags_for_role(role: str) -> tuple[bool, bool]:
    if role == "artist":
        return False, True
    if role == "creator":
        return True, False
    return False, False


def _create_account(db: Session, stage_name: str, email_norm: str, password: str, selected_role: str):
    """Create a buyer or publishing creator atomically."""
    user_role = UserRole.CREATOR if selected_role in {"creator", "artist"} else UserRole.BUYER
    if db.query(User).filter(User.email == email_norm).first():
        raise ValueError("An account with this email already exists.")

    user = User(
        id=str(uuid.uuid4()),
        email=email_norm,
        hashed_password=hash_password(password),
        role=user_role,
        is_active=True,
        is_verified=False,
    )
    db.add(user)
    db.flush()

    base_slug = slugify(stage_name)
    slug = base_slug
    suffix = 1
    while db.query(Profile).filter(Profile.slug == slug).first():
        suffix += 1
        slug = f"{base_slug}-{suffix}"

    is_producer, is_artist = _profile_flags_for_role(selected_role)
    db.add(
        Profile(
            id=str(uuid.uuid4()),
            user_id=user.id,
            stage_name=stage_name,
            slug=slug,
            is_producer=is_producer,
            is_artist=is_artist,
        )
    )
    db.commit()
    db.refresh(user)
    return user


@router.get("/signup")
def signup_page(request: Request):
    requested_role = _normalise_signup_role(request.query_params.get("role"))
    return templates.TemplateResponse(
        request,
        "signup.html",
        base_context(request, stage_name="", email="", role=requested_role),
    )


@router.get("/artist/signup")
def artist_signup_page(request: Request):
    return templates.TemplateResponse(
        request,
        "artist_signup.html",
        base_context(request, stage_name="", email="", role="artist", artist_signup=True),
    )


@router.get("/artist/studio")
def artist_studio_page(
    request: Request,
    user: User = Depends(require_user),
):
    if get_role_name(user) != "creator" or not getattr(getattr(user, "profile", None), "is_artist", False):
        return RedirectResponse(url="/login?error=Artist%20Studio%20access%20requires%20an%20artist%20creator%20account.", status_code=303)

    return templates.TemplateResponse(
        request,
        "artist_studio.html",
        base_context(request, current_user=user, profile=user.profile),
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
    stage_name = (stage_name or "").strip()
    email_norm = (email or "").strip().lower()
    selected_role = _normalise_signup_role(role)

    def error(message: str):
        template = "artist_signup.html" if selected_role == "artist" else "signup.html"
        return templates.TemplateResponse(
            request,
            template,
            base_context(request, error=message, stage_name=stage_name, email=email_norm, role=selected_role, artist_signup=(selected_role == "artist")),
            status_code=400,
        )

    if not stage_name:
        return error("Stage name / artist name is required.")
    if len(stage_name) > 255:
        return error("Stage name is too long.")
    if not email_norm:
        return error("Email address is required.")
    if len(email_norm) > 255:
        return error("Email address is too long.")
    if not agree_terms:
        return error("You must agree to the Terms & Conditions.")
    if len(password) < 8:
        return error("Password must be at least 8 characters.")
    if password != confirm_password:
        return error("Passwords do not match.")

    try:
        user = _create_account(db, stage_name, email_norm, password, selected_role)
    except ValueError as exc:
        db.rollback()
        return error(str(exc))
    except IntegrityError:
        db.rollback()
        return error("Could not create account. Please try again.")
    except Exception:
        db.rollback()
        return error("Could not create account. Please try again.")

    token = create_access_token(subject=user.id, extra_claims={"role": get_role_name(user)})
    response = RedirectResponse(url=f"{dashboard_url_for_user(user)}?success=Account created. Welcome to BeatHub!", status_code=303)
    response.set_cookie(key=SESSION_COOKIE_NAME, value=token, httponly=True, max_age=COOKIE_MAX_AGE, samesite="lax", secure=False, path="/")
    return response


@router.get("/login")
def login_page(request: Request):
    next_url = _safe_next_url(request.query_params.get("next"))
    if not next_url:
        referer = request.headers.get("referer") or ""
        try:
            parsed = urlparse(referer)
            if parsed.netloc in {"", request.url.hostname}:
                referred_path = parsed.path or ""
                if referred_path.startswith("/merch/") or referred_path.startswith("/store/"):
                    query = f"?{parsed.query}" if parsed.query else ""
                    next_url = _safe_next_url(referred_path + query)
        except Exception:
            next_url = ""
    return templates.TemplateResponse(request, "login.html", base_context(request, next_url=next_url))


@router.post("/login")
def login_submit(request: Request, db: Session = Depends(get_db), identifier: str = Form(...), password: str = Form(...)):
    next_url = _safe_next_url(request.query_params.get("next"))
    if not next_url:
        referer = request.headers.get("referer") or ""
        try:
            parsed_referer = urlparse(referer)
            if parsed_referer.netloc in {"", request.url.hostname}:
                referred_path = parsed_referer.path or ""
                if referred_path.startswith("/merch/") or referred_path.startswith("/store/"):
                    query = f"?{parsed_referer.query}" if parsed_referer.query else ""
                    next_url = _safe_next_url(referred_path + query)
        except Exception:
            next_url = ""

    def error(message: str):
        return templates.TemplateResponse(request, "login.html", base_context(request, error=message, next_url=next_url), status_code=401)

    identifier_raw = (identifier or "").strip()
    identifier_norm = identifier_raw.lower()
    password = password or ""
    if not identifier_raw:
        return error("Email or username is required.")
    if not password:
        return error("Password is required.")

    if verify_admin_credentials(identifier_raw, password):
        token = create_admin_session_token()
        response = RedirectResponse(url="/admin", status_code=303)
        response.set_cookie(key=SESSION_COOKIE_NAME, value=token, httponly=True, max_age=COOKIE_MAX_AGE, samesite="lax", secure=False, path="/")
        return response

    user = db.query(User).filter(User.email == identifier_norm).first()
    if not user and identifier_norm:
        possible_slug = slugify(identifier_norm)
        profile = db.query(Profile).filter(Profile.slug == possible_slug).first()
        if profile:
            user = db.query(User).filter(User.id == profile.user_id).first()

    if not user:
        return error("Invalid credentials. Please try again.")
    if not verify_password(password, user.hashed_password):
        return error("Invalid credentials. Please try again.")
    if not user.is_active:
        return error("This account has been deactivated. Contact support.")

    db.refresh(user)
    token = create_access_token(subject=user.id, extra_claims={"role": get_role_name(user)})
    destination = next_url or dashboard_url_for_user(user)
    response = RedirectResponse(url=destination, status_code=303)
    response.set_cookie(key=SESSION_COOKIE_NAME, value=token, httponly=True, max_age=COOKIE_MAX_AGE, samesite="lax", secure=False, path="/")
    return response


def perform_logout() -> RedirectResponse:
    response = RedirectResponse(url="/?success=You have been logged out.", status_code=303)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return response


@router.post("/logout")
def logout():
    return perform_logout()


@router.get("/logout")
def logout_get():
    return perform_logout()


@router.get("/forgot-password")
def forgot_password_page(request: Request):
    return templates.TemplateResponse(request, "forgot_password.html", base_context(request))


@router.post("/forgot-password")
def forgot_password_submit(request: Request, db: Session = Depends(get_db), email: str = Form(...)):
    email_norm = (email or "").strip().lower()
    user = db.query(User).filter(User.email == email_norm).first()
    if user:
        token = secrets.token_urlsafe(32)
        user.reset_token = token
        user.reset_token_expires = datetime.utcnow() + timedelta(hours=1)
        db.commit()
        print(f"[BeatHub] Password reset link for {email_norm}: /reset-password?token={token}")
    return templates.TemplateResponse(request, "forgot_password.html", base_context(request, success="If an account exists for that email, a reset link has been sent."))


@router.get("/reset-password")
def reset_password_page(request: Request, token: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.reset_token == token).first()
    valid = bool(user and user.reset_token_expires and user.reset_token_expires > datetime.utcnow())
    return templates.TemplateResponse(request, "reset_password.html", base_context(request, token=token, valid=valid))


@router.post("/reset-password")
def reset_password_submit(request: Request, db: Session = Depends(get_db), token: str = Form(...), password: str = Form(...), confirm_password: str = Form(...)):
    user = db.query(User).filter(User.reset_token == token).first()
    valid = bool(user and user.reset_token_expires and user.reset_token_expires > datetime.utcnow())
    if not valid:
        return templates.TemplateResponse(request, "reset_password.html", base_context(request, token=token, valid=False, error="This reset link is invalid or has expired."), status_code=400)
    if len(password) < 8 or password != confirm_password:
        return templates.TemplateResponse(request, "reset_password.html", base_context(request, token=token, valid=True, error="Passwords must match and be at least 8 characters."), status_code=400)
    user.hashed_password = hash_password(password)
    user.reset_token = None
    user.reset_token_expires = None
    db.commit()
    return RedirectResponse(url="/login?success=Password updated. Please log in.", status_code=303)
