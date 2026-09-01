import hashlib
import hmac
import logging
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
    if reply_to:
        payload["reply_to"] = reply_to
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": RESEND_USER_AGENT}
    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(RESEND_API_URL, json=payload, headers=headers)
        if 200 <= response.status_code < 300:
            logger.info("Email sent successfully through Resend from configured transactional sender.")
            return True
        error_name = ""
        error_message = ""
        try:
            error_data = response.json()
            if isinstance(error_data, dict):
                error_name = str(error_data.get("name", ""))[:120]
                error_message = str(error_data.get("message", ""))[:240]
        except ValueError:
            pass
        if error_name or error_message:
            logger.error("Resend email delivery failed with HTTP %s (%s): %s", response.status_code, error_name, error_message)
        else:
            logger.error("Resend email delivery failed with HTTP %s.", response.status_code)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        logger.error("Resend network/timeout error: %s", type(exc).__name__)
    except httpx.HTTPError as exc:
        logger.error("Resend HTTP error: %s", type(exc).__name__)
    except Exception as exc:
        logger.error("Unexpected Resend delivery error: %s", type(exc).__name__)
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


def _prepare_verification_code() -> str:
    return _new_verification_code()


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
def artist_signup_page(request: Request):
    return templates.TemplateResponse(request, "artist_signup.html", _signup_context(request, role="artist", artist_signup=True))


@router.get("/artist/studio")
def artist_studio_page(request: Request, user: User = Depends(__import__("app.utils.deps", fromlist=["get_current_user"]).get_current_user), db: Session = Depends(get_db)):
    profile = db.query(Profile).filter(Profile.user_id == str(user.id)).first()
    if get_role_name(user) != "creator" or not getattr(profile, "is_artist", False):
        return RedirectResponse(url="/dashboard?error=Artist%20Studio%20requires%20an%20artist%20account.", status_code=303)
    return templates.TemplateResponse(request, "artist_studio.html", _signup_context(request, current_user=user, profile=profile))


@router.post("/signup")
def signup_submit(request: Request, db: Session = Depends(get_db), stage_name: str = Form(...), email: str = Form(...), password: str = Form(...), confirm_password: str = Form(...), role: str = Form("buyer"), agree_terms: str = Form("")):
    stage_name = (stage_name or "").strip(); email_norm = (email or "").strip().lower(); role = (role or "buyer").strip().lower(); selected_role = role if role in {"artist", "creator", "buyer"} else "buyer"
    def error(message: str): return templates.TemplateResponse(request, "signup.html", _signup_context(request, error=message, stage_name=stage_name, email=email_norm, role=selected_role), status_code=400)
    if not stage_name: return error("Artist / stage name is required.")
    if len(stage_name) > 120: return error("Artist / stage name is too long.")
    if not email_norm: return error("Email address is required.")
    if len(email_norm) > 255 or "@" not in email_norm: return error("Please enter a valid email address.")
    if not agree_terms: return error("You must agree to the Terms & Conditions.")
    if len(password) < 8: return error("Password must be at least 8 characters.")
    if password != confirm_password: return error("Passwords do not match.")
    user_role = UserRole.CREATOR if selected_role in {"artist", "creator"} else UserRole.BUYER
    existing = db.query(User).filter(func.lower(User.email) == email_norm).first()
    if existing:
        if not getattr(existing, "is_verified", False):
            code = _prepare_verification_code()
            if _send_verification_email(email_norm, code):
                _store_verification_code(existing, code); db.commit()
                return RedirectResponse(url=f"/verify-email?email={quote(email_norm, safe='')}&success=Your%20existing%20account%20is%20not%20verified.%20We%20sent%20you%20a%20new%20verification%20code.", status_code=303)
            return RedirectResponse(url=f"/verify-email?email={quote(email_norm, safe='')}&error={quote(_verification_delivery_error_message(), safe='')}", status_code=303)
        return error("An account with this email already exists. Please log in instead.")
    base_username = slugify(stage_name).replace("-", "")[:90] or f"user{secrets.token_hex(4)}"; username = base_username; suffix = 2
    while db.query(User).filter(func.lower(User.username) == username.lower()).first(): username = f"{base_username}{suffix}"; suffix += 1
    user = User(id=str(uuid.uuid4()), email=email_norm, username=username, hashed_password=hash_password(password), role=user_role, is_active=True, is_verified=False)
    verification_code = _set_verification_code(user); db.add(user)
    try:
        db.flush(); base_slug = slugify(stage_name); slug = base_slug; suffix = 2
        while db.query(Profile).filter(Profile.slug == slug).first(): slug = f"{base_slug}-{suffix}"; suffix += 1
        db.add(Profile(id=str(uuid.uuid4()), user_id=user.id, stage_name=stage_name, slug=slug, is_producer=(user_role == UserRole.CREATOR), is_artist=(selected_role == "artist"))); db.commit(); db.refresh(user)
    except IntegrityError:
        db.rollback(); return error("Could not create the account. The email or username may already be in use.")
    if not _send_verification_email(email_norm, verification_code):
        return RedirectResponse(url=f"/verify-email?email={quote(email_norm, safe='')}&error={quote(_verification_delivery_error_message(), safe='')}", status_code=303)
    return RedirectResponse(url=f"/verify-email?email={quote(email_norm, safe='')}&success=We%20sent%20a%206-digit%20verification%20code%20to%20your%20email.", status_code=303)


@router.get("/verify-email")
def verify_email_page(request: Request, email: str = "", error: str = "", success: str = "", next: str = ""):
    safe_next = _safe_next_url(next)
    return templates.TemplateResponse(request, "verify_email.html", {"request": request, "email": (email or "").strip().lower(), "error": error, "success": success, "next": safe_next, "next_url": quote(safe_next, safe="")})


@router.post("/verify-email")
def verify_email_submit(request: Request, db: Session = Depends(get_db), email: str = Form(...), code: str = Form(...), next: str = Form("")):
    email_norm = (email or "").strip().lower(); code_norm = re.sub(r"\s+", "", code or ""); safe_next = _safe_next_url(next)
    user = db.query(User).filter(func.lower(User.email) == email_norm).first()
    if not user: return templates.TemplateResponse(request, "verify_email.html", {"request": request, "email": email_norm, "error": "The verification code is invalid or has expired.", "success": "", "next": safe_next, "next_url": quote(safe_next, safe="")}, status_code=400)
    if user.is_verified: return RedirectResponse(url=f"/login?success=Your%20email%20is%20already%20verified.%20Please%20log%20in.&next={quote(safe_next, safe='')}", status_code=303)
    if not re.fullmatch(r"\d{6}", code_norm): return templates.TemplateResponse(request, "verify_email.html", {"request": request, "email": email_norm, "error": "Enter the 6-digit verification code.", "success": "", "next": safe_next, "next_url": quote(safe_next, safe="")}, status_code=400)
    if int(getattr(user, "verification_attempts", 0) or 0) >= VERIFICATION_MAX_ATTEMPTS: return templates.TemplateResponse(request, "verify_email.html", {"request": request, "email": email_norm, "error": "Too many incorrect attempts. Please request a new code.", "success": "", "next": safe_next, "next_url": quote(safe_next, safe="")}, status_code=429)
    expires = getattr(user, "verification_code_expires", None); stored_hash = getattr(user, "verification_code_hash", None)
    if not stored_hash or not expires or expires <= datetime.utcnow(): return templates.TemplateResponse(request, "verify_email.html", {"request": request, "email": email_norm, "error": "This verification code has expired. Please request a new code.", "success": "", "next": safe_next, "next_url": quote(safe_next, safe="")}, status_code=400)
    if not hmac.compare_digest(stored_hash, _verification_code_digest(code_norm)):
        user.verification_attempts = int(getattr(user, "verification_attempts", 0) or 0) + 1; db.commit()
        return templates.TemplateResponse(request, "verify_email.html", {"request": request, "email": email_norm, "error": "The verification code is incorrect.", "success": "", "next": safe_next, "next_url": quote(safe_next, safe="")}, status_code=400)
    user.is_verified = True; user.verification_code_hash = None; user.verification_code_expires = None; user.verification_attempts = 0; db.commit()
    return RedirectResponse(url=f"/login?success=Email%20verified.%20You%20can%20now%20sign%20in.&next={quote(safe_next, safe='')}", status_code=303)


@router.post("/verify-email/resend")
def resend_verification_email(request: Request, db: Session = Depends(get_db), email: str = Form(...), next: str = Form("")):
    email_norm = (email or "").strip().lower(); safe_next = _safe_next_url(next); user = db.query(User).filter(func.lower(User.email) == email_norm).first()
    if not user or user.is_verified: return RedirectResponse(url=f"/verify-email?email={quote(email_norm, safe='')}&success=If%20verification%20is%20needed,%20a%20new%20code%20has%20been%20sent.&next={quote(safe_next, safe='')}", status_code=303)
    code = _prepare_verification_code(); delivered = _send_verification_email(email_norm, code)
    if not delivered: return RedirectResponse(url=f"/verify-email?email={quote(email_norm, safe='')}&error={quote(_verification_delivery_error_message(), safe='')}&next={quote(safe_next, safe='')}", status_code=303)
    _store_verification_code(user, code); db.commit()
    return RedirectResponse(url=f"/verify-email?email={quote(email_norm, safe='')}&success=A%20new%20verification%20code%20has%20been%20sent.&next={quote(safe_next, safe='')}", status_code=303)


@router.get("/login")
def login_page(request: Request, next: str = "", error: str = "", success: str = ""):
    safe_next = _safe_next_url(next)
    return templates.TemplateResponse(request, "login.html", {"request": request, "next": safe_next, "next_url": quote(safe_next, safe=""), "error": error, "success": success})


@router.post("/login")
def login_submit(request: Request, identifier: str = Form(""), email: str = Form(""), password: str = Form(...), next: str = Form(""), db: Session = Depends(get_db)):
    requested_next = next or request.query_params.get("next", ""); safe_next = _safe_next_url(requested_next); login_identifier = (identifier or email).strip().lower()
    user = db.query(User).filter(or_(func.lower(User.email) == login_identifier, func.lower(User.username) == login_identifier)).first()
    if not user and login_identifier:
        profile = db.query(Profile).filter(func.lower(Profile.slug) == slugify(login_identifier)).first()
        if profile: user = db.query(User).filter(User.id == profile.user_id).first()
    if not user or not _password_matches(password, getattr(user, "hashed_password", "")):
        return RedirectResponse(url=f"/login?error=Invalid%20email%20or%20password&next={quote(safe_next, safe='')}", status_code=303)
    if hasattr(user, "is_active") and not user.is_active:
        return RedirectResponse(url=f"/login?error=Your%20account%20is%20inactive&next={quote(safe_next, safe='')}", status_code=303)
    if not getattr(user, "is_verified", False):
        code = _prepare_verification_code(); delivered = _send_verification_email(user.email, code)
        if delivered:
            _store_verification_code(user, code); db.commit(); message = "Please verify your email. A new verification code has been sent."
        else:
            message = _verification_delivery_error_message()
        return RedirectResponse(url=f"/verify-email?email={quote(user.email, safe='')}&error={quote(message, safe='')}&next={quote(safe_next, safe='')}", status_code=303)
    token = create_access_token(subject=str(user.id), extra_claims={"role": get_role_name(user)}); response = RedirectResponse(url=safe_next or dashboard_url_for_user(user), status_code=303); _set_auth_cookie(response, token); return response


def perform_logout() -> RedirectResponse:
    response = RedirectResponse(url="/?success=You%20have%20been%20logged%20out.", status_code=303); response.delete_cookie(key=SESSION_COOKIE_NAME, path="/"); return response


@router.post("/logout")
def logout(): return perform_logout()


@router.get("/logout")
def logout_get(): return perform_logout()


@router.get("/forgot-password")
def forgot_password_page(request: Request): return templates.TemplateResponse(request, "forgot_password.html", {"request": request, "error": "", "success": ""})


@router.post("/forgot-password")
def forgot_password_submit(request: Request, db: Session = Depends(get_db), email: str = Form(...)):
    email_norm = (email or "").strip().lower(); user = db.query(User).filter(func.lower(User.email) == email_norm).first()
    if user:
        token = secrets.token_urlsafe(32); user.reset_token = _reset_token_digest(token); user.reset_token_expires = datetime.utcnow() + RESET_TOKEN_TTL; db.commit()
        base_url = str(getattr(settings, "BASE_URL", "") or "").strip().rstrip("/") or str(request.base_url).rstrip("/"); reset_url = f"{base_url}/reset-password?token={quote(token, safe='')}"
        delivered = _send_password_reset_email(email_norm, reset_url)
        if not delivered and bool(getattr(settings, "EMAIL_ENABLED", False)): logger.warning("Password reset email delivery failed for configured account.")
    return templates.TemplateResponse(request, "forgot_password.html", {"request": request, "error": "", "success": "If an account exists for that email, a password reset link will be sent shortly."})


@router.get("/reset-password")
def reset_password_page(request: Request, token: str, db: Session = Depends(get_db)):
    token_digest = _reset_token_digest(token); user = db.query(User).filter(User.reset_token == token_digest).first(); valid = bool(user and user.reset_token_expires and user.reset_token_expires > datetime.utcnow())
    return templates.TemplateResponse(request, "reset_password.html", {"request": request, "token": token, "valid": valid, "error": ""})


@router.post("/reset-password")
def reset_password_submit(request: Request, db: Session = Depends(get_db), token: str = Form(...), password: str = Form(...), confirm_password: str = Form(...)):
    token_digest = _reset_token_digest(token); user = db.query(User).filter(User.reset_token == token_digest).first(); valid = bool(user and user.reset_token_expires and user.reset_token_expires > datetime.utcnow())
    if not valid: return templates.TemplateResponse(request, "reset_password.html", {"request": request, "token": token, "valid": False, "error": "This reset link is invalid or has expired."}, status_code=400)
    if len(password) < 8 or password != confirm_password: return templates.TemplateResponse(request, "reset_password.html", {"request": request, "token": token, "valid": True, "error": "Passwords must match and be at least 8 characters."}, status_code=400)
    user.hashed_password = hash_password(password); user.reset_token = None; user.reset_token_expires = None; db.commit()
    return RedirectResponse(url="/login?success=Password%20updated.%20Please%20log%20in.", status_code=303)
