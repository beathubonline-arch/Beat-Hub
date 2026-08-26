from __future__ import annotations

"""BeatHub authentication and authorization dependencies."""

from typing import Optional
import uuid

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.models.profile import Profile
from app.models.user import User
from app.models.user import UserRole
from app.utils.security import decode_access_token
from app.utils.text import unique_slug

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
    """Load the creator profile once and attach it to the authenticated user."""
    cached = getattr(user, "profile", None)
    if cached is not None and str(getattr(cached, "user_id", "")) == str(user.id):
        return cached
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


def _repair_creator_profile(db: Session, user: User) -> Optional[Profile]:
    """Repair legacy/incomplete creator accounts without granting buyer accounts creator access.

    A user must already have the canonical creator role before this helper can
    create a missing creator profile. This makes the repair safe while allowing
    older producer accounts to regain dashboard access after migrations or
    incomplete profile creation.
    """
    if get_role_name(user) not in {"creator", "producer"}:
        return None

    profile = _load_creator_profile(db, user)
    if profile is not None:
        changed = False
        if not getattr(profile, "is_producer", False):
            profile.is_producer = True
            changed = True
        if changed:
            try:
                db.commit()
                db.refresh(profile)
            except Exception:
                db.rollback()
        return profile

    # Build a sensible legacy stage name from the account identity.
    email = str(getattr(user, "email", "") or "").strip()
    username = str(getattr(user, "username", "") or "").strip()
    local_part = email.split("@", 1)[0] if "@" in email else email
    stage_name = username or local_part or "BeatHub Creator"
    stage_name = stage_name[:120]

    try:
        slug = unique_slug(db, Profile, stage_name, fallback_prefix="creator")
        profile = Profile(
            id=str(uuid.uuid4()),
            user_id=str(user.id),
            stage_name=stage_name,
            slug=slug,
            is_producer=True,
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
        try:
            user.profile = profile
        except Exception:
            pass
        return profile
    except IntegrityError:
        # Another request/process may have created the profile concurrently.
        db.rollback()
        profile = _load_creator_profile(db, user)
        if profile is not None:
            if not getattr(profile, "is_producer", False):
                profile.is_producer = True
                try:
                    db.commit()
                    db.refresh(profile)
                except Exception:
                    db.rollback()
            return profile
        return None
    except Exception:
        db.rollback()
        return None


def is_creator_user(db: Session, user: Optional[User]) -> bool:
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
    """Require creator access and repair incomplete legacy creator accounts.

    Access is granted when the authenticated database account is a creator or
    when an existing profile explicitly identifies the account as a producer.
    Creator-role accounts with a missing profile are repaired automatically so
    they do not get stuck at a 403 after signup/migration issues.
    """
    role = get_role_name(user)
    profile = _load_creator_profile(db, user)

    # Canonical creator accounts are always eligible for the creator dashboard.
    # Existing producer profiles remain supported for backward compatibility.
    creator_allowed = (
        role in {"creator", "producer"}
        or bool(profile and getattr(profile, "is_producer", False))
    )

    if not creator_allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Creator access required. Current account role: {role or 'unknown'}.",
        )

    # Keep the database/profile state consistent. If an older account has a
    # creator role but its profile is missing, create the profile instead of
    # incorrectly rejecting the creator with a 403.
    if role in {"creator", "producer"}:
        if profile is None:
            profile = _repair_creator_profile(db, user)
        elif not getattr(profile, "is_producer", False):
            try:
                profile.is_producer = True
                db.commit()
                db.refresh(profile)
            except Exception:
                db.rollback()

    # A creator dashboard requires a usable creator profile because the
    # dashboard/store/upload code depends on profile.id and profile.slug.
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Creator profile could not be created. Please contact BeatHub support.",
        )

    try:
        user.profile = profile
    except Exception:
        pass
    return user


def require_buyer(user: User = Depends(require_user)) -> User:
    role = get_role_name(user)
    if role not in {"buyer", "artist", "customer", "user"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Buyer access required.",
        )
    return user


def require_admin(
    request: Request,
    db: Session = Depends(get_db),
    beathub_session: Optional[str] = Cookie(
        default=None,
        alias=SESSION_COOKIE_NAME,
    ),
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
    if str(subject) == ADMIN_SESSION_SUBJECT and (
        payload_role == "admin" or payload.get("admin") is True
    ):
        return payload
    if subject:
        user = _get_user_from_subject(db, str(subject))
        if user is not None:
            if not getattr(user, "is_active", True):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Administrator account is inactive.",
                )
            if get_role_name(user) == "admin":
                return user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Administrator access required.",
    )


def get_current_user(user: User = Depends(require_user)) -> User:
    return user


def get_current_creator(user: User = Depends(require_creator)) -> User:
    return user


def get_current_admin(admin=Depends(require_admin)):
    return admin
