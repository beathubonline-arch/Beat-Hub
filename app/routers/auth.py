import hashlib
import hmac
import logging
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta
from urllib.parse import quote, unquote, urlparse

import httpx
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
logger = logging.getLogger("beathub.auth")

SESSION_COOKIE_NAME = "beathub_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30
RESET_TOKEN_TTL = timedelta(hours=1)
VERIFICATION_CODE_TTL = timedelta(minutes=10)
VERIFICATION_MAX_ATTEMPTS = 5
RESEND_API_URL = "https://api.resend.com/emails"
RESEND_USER_AGENT = f"BeatHub/1.0 (+{str(getattr(settings, 'BASE_URL', 'https://mybeathub.com')).rstrip('/')})"


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
    if role == "admin": return "/admin"
    if role == "creator":
        profile = getattr(user, "profile", None)
        if profile is not None and bool(getattr(profile, "is_artist", False)): return "/artist/studio"
        return "/dashboard"
    return "/account"


def _password_matches(plain_password: str, stored_password: str) -> bool:
    if not stored_password: return False
    try: return verify_password(plain_password, stored_password)
    except Exception: return False


def _safe_next_url(value: str) -> str:
    value = unquote((value or "").strip())
    if not value: return ""
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or not value.startswith("/") or value.startswith("//"): return ""
    return value


def _signup_context(request: Request, **extra):
    context = {"request": request, "stage_name": "", "email": "", "role": "buyer"}
    context.update(extra)
    return context


def _reset_token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _verification_code_digest(code: str) -> str:
    secret = str(getattr(settings, "SECRET_KEY", None) or getattr(settings, "SESSION_SECRET", None) or "beathub-development-verification")
    return hmac.new(secret.encode("utf-8"), code.encode("utf-8"), hashlib.sha256).hexdigest()


def _new_verification_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _set_verification_code(user: User) -> str:
    code = _new_verification_code()
    user.verification_code_hash = _verification_code_digest(code)
    user.verification_code_expires = datetime.utcnow() + VERIFICATION_CODE_TTL
    user.verification_attempts = 0
    return code


def _send_email_resend(to_email: str, subject: str, body: str, sender: str | None = None, reply_to: str | None = None) -> bool:
    api_key = str(getattr(settings, "RESEND_API_KEY", "") or "").strip()
    sender = str(sender or getattr(settings, "RESEND_FROM", "") or getattr(settings, "EMAIL_FROM", "") or "").strip()
    if not api_key or not sender:
        logger.error("Resend email delivery is misconfigured: RESEND_API_KEY or sender is missing.")
        return False
    payload = {"from": sender, "to": [to_email], "subject": subject, "text": body}
    if reply_to: payload["reply_to"] = reply_to
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": RESEND_USER_AGENT}
    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(RESEND_API_URL, json=payload, headers=headers)
        if 200 <= response.status_code < 300:
            logger.info("Email sent successfully through Resend from configured transactional sender.")
            return True
        error_name = ""; error_message = ""
        try:
            error_data = response.json()
            if isinstance(error_data, dict):
                error_name = str(error_data.get("name", ""))[:120]
                error_message = str(error_data.get("message", ""))[:240]
        except ValueError: pass
        if error_name or error_message: logger.error("Resend email delivery failed with HTTP %s (%s): %s", response.status_code, error_name, error_message)
        else: logger.error("Resend email delivery failed with HTTP %s.", response.status_code)
    except (httpx.TimeoutException, httpx.NetworkError) as exc: logger.error("Resend network/timeout error: %s", type(exc).__name__)
    except httpx.HTTPError as exc: logger.error("Resend HTTP error: %s", type(exc).__name__)
    except Exception as exc: logger.error("Unexpected Resend delivery error: %s", type(exc).__name__)
    return False


def _send_email(to_email: str, subject: str, body: str, sender: str | None = None, reply_to: str | None = None) -> bool:
    if not bool(getattr(settings, "EMAIL_ENABLED", False)):
        logger.warning("Email delivery disabled by EMAIL_ENABLED.")
        return False
    provider = str(getattr(settings, "EMAIL_PROVIDER", "resend") or "resend").strip().lower()
    if provider == "resend": return _send_email_resend(to_email, subject, body, sender=sender, reply_to=reply_to)
    logger.error("Unsupported email provider: %s", provider)
    return False


def _send_verification_email(email: str, code: str) -> bool:
    sender = str(getattr(settings, "EMAIL_VERIFICATION_FROM", "") or "BeatHub <no-reply@mybeathub.com>").strip()
    return _send_email(email, "Verify your BeatHub email", "Welcome to BeatHub.\n\n" f"Your verification code is: {code}\n\n" "This code expires in 10 minutes and can only be used once.\n\n" "If you did not create this account, you can ignore this email.", sender=sender)


def _send_password_reset_email(email: str, reset_url: str) -> bool:
    sender = str(getattr(settings, "PASSWORD_RESET_FROM", "") or "BeatHub Password Reset <reset-password@mybeathub.com>").strip()
    reply_to = str(getattr(settings, "SUPPORT_EMAIL", "") or "support@mybeathub.com").strip()
    return _send_email(email, "BeatHub password reset", "We received a request to reset your BeatHub password.\n\n" f"Use this link within 1 hour:\n{reset_url}\n\n" "If you did not request this, you can safely ignore this email.", sender=sender, reply_to=reply_to)


def _verification_delivery_error_message() -> str:
    return "We couldn't send your verification email right now. Please try again later."


def _prepare_verification_code() -> str: return _new_verification_code()


def _store_verification_code(user: User, code: str) -> None:
    user.verification_code_hash = _verification_code_digest(code)
    user.verification_code_expires = datetime.utcnow() + VERIFICATION_CODE_TTL
    user.verification_attempts = 0


@router.get("/signup")
def signup_page(request: Request, role: str = "buyer"):
    role = role.strip().lower()
    if role not in {"artist", "creator", "buyer"}: role = "buyer"
    return templates.TemplateResponse(request, "signup.html", _signup_context(request, role=role))


@router.get("/artist/signup")
def artist_signup_page(request: Request): return templates.TemplateResponse(request, "artist_signup.html", _signup_context(request, role="artist", artist_signup=True))


@router.get("/artist/studio")
def artist_studio_page(request: Request, user: User = Depends(__import__("app.utils.deps", fromlist=["get_current_user"]).get_current_user), db: Session = Depends(get_db)):
    profile = db.query(Profile).filter(Profile.user_id == str(user.id)).first()
    if get_role_name(user) != "creator" or not getattr(profile, "is_artist", False): return RedirectResponse(url="/dashboard?error=Artist%20Studio%20requires%20an%20artist%20account.", status_code=303)
    return templates.TemplateResponse(request, "artist_studio.html", _signup_context(request, current_user=user, profile=profile))


@router.get("/login")
def login_page(request: Request, next: str = "", error: str = "", success: str = ""):
    safe_next = _safe_next_url(next)
    # Keep next_url raw here; login.html applies URL encoding exactly once.
    return templates.TemplateResponse(request, "login.html", {"request": request, "next": safe_next, "next_url": safe_next, "error": error, "success": success})


@router.post("/login")
def login_submit(request: Request, identifier: str = Form(""), email: str = Form(""), password: str = Form(...), next: str = Form(""), db: Session = Depends(get_db)):
    requested_next = next or request.query_params.get("next", ""); safe_next = _safe_next_url(requested_next); login_identifier = (identifier or email).strip().lower()
    user = db.query(User).filter(or_(func.lower(User.email) == login_identifier, func.lower(User.username) == login_identifier)).first()
    admin_email = str(getattr(settings, "ADMIN_EMAIL", "admin@mybeathub.com") or "").strip().lower(); admin_password = str(getattr(settings, "ADMIN_PASSWORD", "") or os.getenv("ADMIN_PASSWORD", "")).strip()
    if user is None and login_identifier == admin_email and admin_password and password == admin_password:
        user = User(id=str(uuid.uuid4()), email=admin_email, username="admin", hashed_password=hash_password(admin_password), role=UserRole.ADMIN, is_active=True, is_verified=True); db.add(user)
        try: db.commit(); db.refresh(user)
        except IntegrityError: db.rollback(); user = db.query(User).filter(func.lower(User.email) == admin_email).first()
    if not user and login_identifier:
        profile = db.query(Profile).filter(func.lower(Profile.slug) == slugify(login_identifier)).first()
        if profile: user = db.query(User).filter(User.id == profile.user_id).first()
    if not user or not _password_matches(password, getattr(user, "hashed_password", "")): return RedirectResponse(url=f"/login?error=Invalid%20email%20or%20password&next={quote(safe_next, safe='')}", status_code=303)
    if hasattr(user, "is_active") and not user.is_active: return RedirectResponse(url=f"/login?error=Your%20account%20is%20inactive&next={quote(safe_next, safe='')}", status_code=303)
    if not getattr(user, "is_verified", False):
        code = _prepare_verification_code(); delivered = _send_verification_email(user.email, code)
        if delivered: _store_verification_code(user, code); db.commit(); message = "Please verify your email. A new verification code has been sent."
        else: message = _verification_delivery_error_message()
        return RedirectResponse(url=f"/verify-email?email={quote(user.email, safe='')}&error={quote(message, safe='')}&next={quote(safe_next, safe='')}", status_code=303)
    token = create_access_token(subject=str(user.id), extra_claims={"role": get_role_name(user)}); response = RedirectResponse(url=safe_next or dashboard_url_for_user(user), status_code=303); _set_auth_cookie(response, token); return response


def perform_logout() -> RedirectResponse:
    response = RedirectResponse(url="/?success=You%20have%20been%20logged%20out.", status_code=303); response.delete_cookie(key=SESSION_COOKIE_NAME, path="/"); return response


@router.post("/logout")
def logout(): return perform_logout()


@router.get("/logout")
def logout_get(): return perform_logout()
