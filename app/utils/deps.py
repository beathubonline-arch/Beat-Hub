from __future__ import annotations

"""BeatHub authentication and authorization dependencies."""

from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.profile import Profile
from app.models.user import User
from app.utils.security import decode_access_token

SESSION_COOKIE_NAME = "beathub_session"
ADMIN_SESSION_SUBJECT = "beathub-admin"
COOKIE_MAX_AGE = 60 * 60 * 24 * 7


def get_role_name(user: Optional[User]) -> str:
    if user is None:
        return ""
    role = getattr(user, "role", None)
    if role is None:
        return ""
    value = getattr(role, "value", None)
    return str(value if value is not None else role).strip().lower()


def _get_user_from_subject(db: Session, subject: str) -> Optional[User]:
    if not subject or str(subject) == ADMIN_SESSION_SUBJECT:
        return None
    return db.query(User).filter(User.id == str(subject)).first()


def _get_token_from_request(request: Request, cookie_token: Optional[str]) -> Optional[str]:
    if cookie_token and cookie_token.strip():
        return cookie_token.strip()

    authorization = request.headers.get("Authorization", "").strip()
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token:
            return token
    return None


def _decode_token(token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    try:
        payload = decode_access_token(token)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def get_optional_user(
    request: Request,
    db: Session = Depends(get_db),
    beathub_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> Optional[User]:
    token = _get_token_from_request(request, beathub_session)
    payload = _decode_token(token)
    if not payload:
        return None

    subject = payload.get("sub") or payload.get("user_id") or payload.get("id")
    if not subject or str(subject) == ADMIN_SESSION_SUBJECT:
        return None

    user = _get_user_from_subject(db, str(subject))
    if user is None or not getattr(user, "is_active", True):
        return None
    return user


def require_user(user: Optional[User] = Depends(get_optional_user)) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="You must be logged in.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def _load_creator_profile(db: Session, user: User) -> Optional[Profile]:
    """Always verify the profile directly against this user's user_id."""
    try:
        profile = db.query(Profile).filter(Profile.user_id == str(user.id)).first()
    except Exception:
        return None

    if profile is not None:
        try:
            user.profile = profile
        except Exception:
            pass
    return profile


def is_creator_user(db: Session, user: Optional[User]) -> bool:
    """Single source of truth for creator/producer authorization."""
    if user is None:
        return False

    role = get_role_name(user)
    if role in {"creator", "producer"}:
        return True

    profile = _load_creator_profile(db, user)
    return bool(profile and getattr(profile, "is_producer", False))


def require_creator(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> User:
    """Require a real creator/producer account, including legacy profiles."""
    role = get_role_name(user)
    profile = _load_creator_profile(db, user)

    if not is_creator_user(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Creator access required. Current account role: {role or 'unknown'}.",
        )

    # Repair the common legacy mismatch: the profile says producer but the
    # user row still says buyer. This is safe because the profile belongs to
    # this exact authenticated user and is explicitly marked is_producer.
    if profile is not None and getattr(profile, "is_producer", False) and role not in {"creator", "producer"}:
        try:
            from app.models.user import UserRole
            user.role = UserRole.CREATOR
            db.commit()
            db.refresh(user)
        except Exception:
            db.rollback()
            # Authorization is still valid from the producer profile.

    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Creator profile is missing. Please contact BeatHub support.",
        )

    try:
        user.profile = profile
    except Exception:
        pass
    return user


def require_buyer(user: User = Depends(require_user)) -> User:
    role = get_role_name(user)
    if role not in {"buyer", "artist", "customer", "user"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Buyer access required.")
    return user


def require_admin(
    request: Request,
    db: Session = Depends(get_db),
    beathub_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
):
    token = _get_token_from_request(request, beathub_session)
    payload = _decode_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Administrator session is invalid or expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    subject = payload.get("sub") or payload.get("user_id") or payload.get("id")
    payload_role = str(payload.get("role", "")).strip().lower()

    if str(subject) == ADMIN_SESSION_SUBJECT and (payload_role == "admin" or payload.get("admin") is True):
        return payload

    if subject:
        user = _get_user_from_subject(db, str(subject))
        if user is not None:
            if not getattr(user, "is_active", True):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator account is inactive.")
            if get_role_name(user) == "admin":
                return user

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required.")


def get_current_user(user: User = Depends(require_user)) -> User:
    return user


def get_current_creator(user: User = Depends(require_creator)) -> User:
    return user


def get_current_admin(admin=Depends(require_admin)):
    return admin
