import hashlib
import re
import secrets
import smtplib
import uuid
from datetime import datetime, timedelta
from email.message import EmailMessage
from urllib.parse import quote, unquote, urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.profile import Profile
from app.models.user import User, UserRole
from app.utils.security import create_access_token, hash_password, verify_password

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="app/templates")

SESSION_COOKIE_NAME = "beathub_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30
RESET_TOKEN_TTL = timedelta(hours=1)


def get_role_name(user: User) -> str:
    role = getattr(user, "role", "buyer")
    return getattr(role, "value", role) or "buyer"


def _cookie_secure() -> bool:
    return bool(settings.is_production)


def _set_auth_cookie(response: RedirectResponse, token: str) -> None:
    response.set_cookie(key=SESSION_COOKIE_NAME, value=token, httponly=True, max_age=COOKIE_MAX_AGE, samesite="lax", secure=_cookie_secure(), path="/")


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
    if parsed.scheme or parsed.netloc or not value.startswith("/") or value.startswith("//"):
        return ""
    return value


def _signup_context(request: Request, **extra):
    context = {"request": request, "stage_name": "", "email": "", "role": "buyer"}
    context.update(extra)
    return context


def _reset_token_digest(token: str) -> str:
    """Store only a SHA-256 digest of a password-reset token.

    The raw token remains in the user's reset URL, but a database/log leak no
    longer provides a directly usable reset credential.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _send_password_reset_email(email: str, reset_url: str) -> bool:
    """Send a reset message when SMTP delivery is explicitly enabled.

    Failure is deliberately logged without the token or reset URL. The caller
    keeps the response generic so account existence is not disclosed.
    """
    if not bool(getattr(settings, "EMAIL_ENABLED", False)):
        return False

    host = str(getattr(settings, "EMAIL_HOST", "") or "").strip()
    username = str(getattr(settings, "EMAIL_USERNAME", "") or "").strip()
    password = str(getattr(settings, "EMAIL_PASSWORD", "") or "")
    sender = str(getattr(settings, "EMAIL_FROM", "") or username).strip()
    try:
        port = int(getattr(settings, "EMAIL_PORT", 587) or 587)
    except (TypeError, ValueError):
        port = 587

    if not host or not sender:
        return False

    message = EmailMessage()
    message["Subject"] = "BeatHub password reset"
    message["From"] = sender
    message["To"] = email
    message.set_content(
        "We received a request to reset your BeatHub password.\n\n"
        f"Use this link within 1 hour:\n{reset_url}\n\n"
        "If you did not request this, you can safely ignore this email."
    )

    try:
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            if username:
                smtp.login(username, password)
            smtp.send_message(message)
        return True
    except Exception:
        return False


@router.get("/signup")
def signup_page(request: Request, role: str = "buyer"):
    role = role.strip().lower()
    if role not in {"artist", "creator", "buyer"}:
        role = "buyer"
    return templates.TemplateResponse(request, "signup.html", _signup_context(request, role=role))


@router.get("/artist/signup")
def artist_signup_page(request: Request):
    return templates.TemplateResponse(
        request,
        "artist_signup.html",
        _signup_context(request, role="artist", artist_signup=True),
    )


@router.get("/artist/studio")
def artist_studio_page(
    request: Request,
    user: User = Depends(__import__("app.utils.deps", fromlist=["get_current_user"]).get_current_user),
    db: Session = Depends(get_db),
):
    profile = db.query(Profile).filter(Profile.user_id == str(user.id)).first()
    if get_role_name(user) != "creator" or not getattr(profile, "is_artist", False):
        return RedirectResponse(url="/dashboard?error=Artist%20Studio%20requires%20an%20artist%20account.", status_code=303)

    return templates.TemplateResponse(
        request,
        "artist_studio.html",
        _signup_context(request, current_user=user, profile=profile),
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
    agree_terms: str = Form(""),
):
    stage_name = (stage_name or "").strip()
    email_norm = (email or "").strip().lower()
    role = (role or "buyer").strip().lower()
    selected_role = role if role in {"artist", "creator", "buyer"} else "buyer"

    def error(message: str):
        return templates.TemplateResponse(request, "signup.html", _signup_context(request, error=message, stage_name=stage_name, email=email_norm, role=selected_role), status_code=400)

    if not stage_name:
        return error("Artist / stage name is required.")
    if len(stage_name) > 120:
        return error("Artist / stage name is too long.")
    if not email_norm:
        return error("Email address is required.")
    if len(email_norm) > 255 or "@" not in email_norm:
        return error("Please enter a valid email address.")
    if not agree_terms:
        return error("You must agree to the Terms & Conditions.")
    if len(password) < 8:
        return error("Password must be at least 8 characters.")
    if password != confirm_password:
        return error("Passwords do not match.")

    user_role = UserRole.CREATOR if selected_role in {"artist", "creator"} else UserRole.BUYER

    if db.query(User).filter(func.lower(User.email) == email_norm).first():
        return error("An account with this email already exists.")

    base_username = slugify(stage_name).replace("-", "")[:90] or f"user{secrets.token_hex(4)}"
    username = base_username
    suffix = 2
    while db.query(User).filter(func.lower(User.username) == username.lower()).first():
        username = f"{base_username}{suffix}"
        suffix += 1

    user = User(id=str(uuid.uuid4()), email=email_norm, username=username, hashed_password=hash_password(password), role=user_role, is_active=True, is_verified=False)
    db.add(user)
    try:
        db.flush()
        base_slug = slugify(stage_name)
        slug = base_slug
        suffix = 2
        while db.query(Profile).filter(Profile.slug == slug).first():
            slug = f"{base_slug}-{suffix}"
            suffix += 1

        profile = Profile(
            id=str(uuid.uuid4()), user_id=user.id, stage_name=stage_name, slug=slug,
            is_producer=(user_role == UserRole.CREATOR),
            is_artist=(selected_role == "artist"),
        )
        db.add(profile)
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        return error("Could not create the account. The email or username may already be in use.")

    token = create_access_token(subject=str(user.id), extra_claims={"role": get_role_name(user)})
    response = RedirectResponse(url=dashboard_url_for_user(user) + "?success=Account%20created.%20Welcome%20to%20BeatHub!", status_code=303)
    _set_auth_cookie(response, token)
    return response


@router.get("/login")
def login_page(request: Request, next: str = "", error: str = ""):
    safe_next = _safe_next_url(next)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"request": request, "next": safe_next, "next_url": quote(safe_next, safe=""), "error": error},
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
    requested_next = next or request.query_params.get("next", "")
    safe_next = _safe_next_url(requested_next)

    login_identifier = (identifier or email).strip().lower()
    user = db.query(User).filter(or_(func.lower(User.email) == login_identifier, func.lower(User.username) == login_identifier)).first()

    if not user and login_identifier:
        possible_slug = slugify(login_identifier)
        profile = db.query(Profile).filter(func.lower(Profile.slug) == possible_slug).first()
        if profile:
            user = db.query(User).filter(User.id == profile.user_id).first()

    if not user or not _password_matches(password, getattr(user, "hashed_password", "")):
        return RedirectResponse(url=f"/login?error=Invalid%20email%20or%20password&next={quote(safe_next, safe='')}", status_code=303)
    if hasattr(user, "is_active") and not user.is_active:
        return RedirectResponse(url=f"/login?error=Your%20account%20is%20inactive&next={quote(safe_next, safe='')}", status_code=303)

    token = create_access_token(subject=str(user.id), extra_claims={"role": get_role_name(user)})
    response = RedirectResponse(url=safe_next or dashboard_url_for_user(user), status_code=303)
    _set_auth_cookie(response, token)
    return response


def perform_logout() -> RedirectResponse:
    response = RedirectResponse(url="/?success=You%20have%20been%20logged%20out.", status_code=303)
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
    return templates.TemplateResponse(request, "forgot_password.html", {"request": request, "error": "", "success": ""})


@router.post("/forgot-password")
def forgot_password_submit(request: Request, db: Session = Depends(get_db), email: str = Form(...)):
    email_norm = (email or "").strip().lower()
    user = db.query(User).filter(func.lower(User.email) == email_norm).first()
    if user:
        token = secrets.token_urlsafe(32)
        user.reset_token = _reset_token_digest(token)
        user.reset_token_expires = datetime.utcnow() + RESET_TOKEN_TTL
        db.commit()

        base_url = str(getattr(settings, "BASE_URL", "") or "").strip().rstrip("/")
        if not base_url:
            base_url = str(request.base_url).rstrip("/")
        reset_url = f"{base_url}/reset-password?token={quote(token, safe='')}"

        delivered = _send_password_reset_email(email_norm, reset_url)
        if not delivered and bool(getattr(settings, "EMAIL_ENABLED", False)):
            # Never include the token/reset URL in logs. The account holder can
            # request another message after email delivery is corrected.
            import logging
            logging.getLogger("beathub.auth").warning(
                "Password reset email delivery failed for configured account."
            )

    return templates.TemplateResponse(
        request,
        "forgot_password.html",
        {
            "request": request,
            "error": "",
            "success": "If an account exists for that email, a password reset link will be sent shortly.",
        },
    )


@router.get("/reset-password")
def reset_password_page(request: Request, token: str, db: Session = Depends(get_db)):
    token_digest = _reset_token_digest(token)
    user = db.query(User).filter(User.reset_token == token_digest).first()
    valid = bool(user and user.reset_token_expires and user.reset_token_expires > datetime.utcnow())
    return templates.TemplateResponse(request, "reset_password.html", {"request": request, "token": token, "valid": valid, "error": ""})


@router.post("/reset-password")
def reset_password_submit(request: Request, db: Session = Depends(get_db), token: str = Form(...), password: str = Form(...), confirm_password: str = Form(...)):
    token_digest = _reset_token_digest(token)
    user = db.query(User).filter(User.reset_token == token_digest).first()
    valid = bool(user and user.reset_token_expires and user.reset_token_expires > datetime.utcnow())
    if not valid:
        return templates.TemplateResponse(request, "reset_password.html", {"request": request, "token": token, "valid": False, "error": "This reset link is invalid or has expired."}, status_code=400)
    if len(password) < 8 or password != confirm_password:
        return templates.TemplateResponse(request, "reset_password.html", {"request": request, "token": token, "valid": True, "error": "Passwords must match and be at least 8 characters."}, status_code=400)
    user.hashed_password = hash_password(password)
    user.reset_token = None
    user.reset_token_expires = None
    db.commit()
    return RedirectResponse(url="/login?success=Password%20updated.%20Please%20log%20in.", status_code=303)