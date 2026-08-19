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
from app.utils.deps import SESSION_COOKIE_NAME, get_optional_user
from app.utils.security import create_access_token, hash_password, verify_password

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="app/templates")

COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 days


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value or f"producer-{secrets.token_hex(4)}"


@router.get("/signup")
def signup_page(request: Request):
    return templates.TemplateResponse(request, "signup.html", {"request": request, "current_user": None, "current_year": datetime.utcnow().year})


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
    def error(msg: str):
        return templates.TemplateResponse(request,
            "signup.html",
            {"request": request, "error": msg, "current_user": None, "current_year": datetime.utcnow().year},
            status_code=400,
        )

    email_norm = email.strip().lower()

    if not agree_terms:
        return error("You must agree to the Terms & Conditions.")
    if len(password) < 8:
        return error("Password must be at least 8 characters.")
    if password != confirm_password:
        return error("Passwords do not match.")
    if db.query(User).filter(User.email == email_norm).first():
        return error("An account with this email already exists.")

    user_role = UserRole.CREATOR if role == "creator" else UserRole.BUYER

    user = User(
        id=str(uuid.uuid4()),
        email=email_norm,
        hashed_password=hash_password(password),
        role=user_role,
    )
    db.add(user)

    try:
        db.flush()
    except IntegrityError:
        # Race condition: another request inserted this email between our
        # SELECT check above and this INSERT. Roll back so the session/
        # connection isn't left in an aborted-transaction state for the
        # next request that reuses it, then show a normal error instead
        # of crashing to the global 500 handler.
        db.rollback()
        return error("An account with this email already exists.")

    # Every account gets a public profile (buyers can still be discoverable
    # if they later start uploading; producers need it immediately).
    base_slug = slugify(stage_name)
    slug = base_slug
    suffix = 1
    while db.query(Profile).filter(Profile.slug == slug).first():
        suffix += 1
        slug = f"{base_slug}-{suffix}"

    profile = Profile(
        id=str(uuid.uuid4()),
        user_id=user.id,
        stage_name=stage_name.strip(),
        slug=slug,
        is_producer=(user_role == UserRole.CREATOR),
    )
    db.add(profile)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return error("Could not create account. Please try again.")

    token = create_access_token(subject=user.id)
    response = RedirectResponse(url="/?success=Account created. Welcome to BeatHub!", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=COOKIE_MAX_AGE,
        samesite="lax",
    )
    return response


@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"request": request, "current_user": None, "current_year": datetime.utcnow().year})


@router.post("/login")
def login_submit(
    request: Request,
    db: Session = Depends(get_db),
    identifier: str = Form(...),
    password: str = Form(...),
):
    def error(msg: str):
        return templates.TemplateResponse(request,
            "login.html",
            {"request": request, "error": msg, "current_user": None, "current_year": datetime.utcnow().year},
            status_code=401,
        )

    identifier_norm = identifier.strip().lower()
    user = db.query(User).filter(User.email == identifier_norm).first()

    if not user:
        # allow login by producer/stage name too
        profile = db.query(Profile).filter(Profile.slug == slugify(identifier)).first()
        if profile:
            user = db.query(User).filter(User.id == profile.user_id).first()

    if not user or not verify_password(password, user.hashed_password):
        return error("Invalid credentials. Please try again.")
    if not user.is_active:
        return error("This account has been deactivated. Contact support.")

    token = create_access_token(subject=user.id)
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=COOKIE_MAX_AGE,
        samesite="lax",
    )
    return response


@router.post("/logout")
def logout(request: Request):
    """
    Logout must accept POST (matches the nav's logout form) and must not 404.
    A GET fallback is also provided below for any link-style logout usage.
    """
    response = RedirectResponse(url="/?success=You have been logged out.", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@router.get("/logout")
def logout_get(request: Request):
    response = RedirectResponse(url="/?success=You have been logged out.", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@router.get("/forgot-password")
def forgot_password_page(request: Request):
    return templates.TemplateResponse(request,
        "forgot_password.html", {"request": request, "current_user": None, "current_year": datetime.utcnow().year}
    )


@router.post("/forgot-password")
def forgot_password_submit(request: Request, db: Session = Depends(get_db), email: str = Form(...)):
    email_norm = email.strip().lower()
    user = db.query(User).filter(User.email == email_norm).first()

    # Always show the same message whether or not the account exists,
    # to avoid leaking which emails are registered.
    if user:
        token = secrets.token_urlsafe(32)
        user.reset_token = token
        user.reset_token_expires = datetime.utcnow() + timedelta(hours=1)
        db.commit()
        # In production this link is emailed via app/services/notifications.py.
        # Logged here so the flow is testable even without email configured.
        reset_link = f"/reset-password?token={token}"
        print(f"[BeatHub] Password reset link for {email_norm}: {reset_link}")

    return templates.TemplateResponse(request,
        "forgot_password.html",
        {
            "request": request,
            "current_user": None,
            "current_year": datetime.utcnow().year,
            "success": "If an account exists for that email, a reset link has been sent.",
        },
    )


@router.get("/reset-password")
def reset_password_page(request: Request, token: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.reset_token == token).first()
    valid = bool(user and user.reset_token_expires and user.reset_token_expires > datetime.utcnow())
    return templates.TemplateResponse(request,
        "reset_password.html",
        {"request": request, "current_user": None, "current_year": datetime.utcnow().year, "token": token, "valid": valid},
    )


@router.post("/reset-password")
def reset_password_submit(
    request: Request,
    db: Session = Depends(get_db),
    token: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
):
    user = db.query(User).filter(User.reset_token == token).first()
    valid = bool(user and user.reset_token_expires and user.reset_token_expires > datetime.utcnow())

    if not valid:
        return templates.TemplateResponse(request,
            "reset_password.html",
            {"request": request, "current_user": None, "current_year": datetime.utcnow().year, "token": token, "valid": False,
             "error": "This reset link is invalid or has expired."},
            status_code=400,
        )

    if len(password) < 8 or password != confirm_password:
        return templates.TemplateResponse(request,
            "reset_password.html",
            {"request": request, "current_user": None, "current_year": datetime.utcnow().year, "token": token, "valid": True,
             "error": "Passwords must match and be at least 8 characters."},
            status_code=400,
        )

    user.hashed_password = hash_password(password)
    user.reset_token = None
    user.reset_token_expires = None
    db.commit()

    return RedirectResponse(url="/login?success=Password updated. Please log in.", status_code=303)
