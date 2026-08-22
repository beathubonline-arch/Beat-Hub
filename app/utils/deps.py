deps.py


"""
BeatHub authentication dependencies.

This file is the central authentication/authorization layer for:
- normal user sessions
- creator/producer dashboard access
- buyer/artist access
- administrator access
- legacy creator compatibility

Important dashboard fix:
The dashboard route already exists at /dashboard. The recurring 403 was
coming from creator authorization, not from a missing dashboard route.

This version:
- accepts UserRole enum values and strings
- accepts creator/producer role aliases
- explicitly reloads the creator Profile by user_id
- repairs a stale/missing SQLAlchemy relationship in memory
- accepts legacy creator profiles marked is_producer=True
- does not change dashboard templates, CSS, uploads, merchandise, checkout,
  M-Pesa, earnings, or other application behaviour
"""

from __future__ import annotations

from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.profile import Profile
from app.models.user import User
from app.utils.security import decode_access_token


# ============================================================
# SESSION CONSTANTS
# ============================================================

SESSION_COOKIE_NAME = "beathub_session"
ADMIN_SESSION_SUBJECT = "beathub-admin"
COOKIE_MAX_AGE = 60 * 60 * 24 * 7


# ============================================================
# ROLE NORMALIZATION
# ============================================================

def get_role_name(user: Optional[User]) -> str:
    """
    Return the normalized database role.

    Supports:
        UserRole.CREATOR
        "creator"
        "producer"
        UserRole.BUYER
        "buyer"
        UserRole.ADMIN
        "admin"
    """

    if user is None:
        return ""

    role = getattr(user, "role", None)

    if role is None:
        return ""

    value = getattr(role, "value", None)

    if value is not None:
        return str(value).strip().lower()

    return str(role).strip().lower()


# ============================================================
# USER LOOKUP
# ============================================================

def _get_user_from_subject(
    db: Session,
    subject: str,
) -> Optional[User]:
    """
    Load a normal BeatHub user from the JWT subject.
    """

    if not subject:
        return None

    if str(subject) == ADMIN_SESSION_SUBJECT:
        return None

    return (
        db.query(User)
        .filter(User.id == str(subject))
        .first()
    )


# ============================================================
# TOKEN EXTRACTION
# ============================================================

def _get_token_from_request(
    request: Request,
    cookie_token: Optional[str],
) -> Optional[str]:
    """
    Read the BeatHub authentication token.

    Primary:
        beathub_session cookie

    Compatibility:
        Authorization: Bearer <token>
    """

    if cookie_token:
        token = cookie_token.strip()

        if token:
            return token

    authorization = (
        request.headers.get(
            "Authorization",
            "",
        )
        .strip()
    )

    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

        if token:
            return token

    return None


# ============================================================
# TOKEN DECODING
# ============================================================

def _decode_token(
    token: Optional[str],
) -> Optional[dict]:
    """
    Safely decode an existing BeatHub access token.
    """

    if not token:
        return None

    try:
        payload = decode_access_token(token)
    except Exception:
        return None

    if not payload:
        return None

    if not isinstance(payload, dict):
        return None

    return payload


# ============================================================
# CURRENT USER
# ============================================================

def get_optional_user(
    request: Request,
    db: Session = Depends(get_db),
    beathub_session: Optional[str] = Cookie(
        default=None,
        alias=SESSION_COOKIE_NAME,
    ),
) -> Optional[User]:
    """
    Return the currently authenticated database user.

    Invalid, expired, missing, inactive, or administrator tokens
    return None.
    """

    token = _get_token_from_request(
        request,
        beathub_session,
    )

    if not token:
        return None

    payload = _decode_token(token)

    if not payload:
        return None

    subject = (
        payload.get("sub")
        or payload.get("user_id")
        or payload.get("id")
    )

    if not subject:
        return None

    if str(subject) == ADMIN_SESSION_SUBJECT:
        return None

    user = _get_user_from_subject(
        db,
        str(subject),
    )

    if user is None:
        return None

    if not getattr(
        user,
        "is_active",
        True,
    ):
        return None

    return user


# ============================================================
# REQUIRED USER
# ============================================================

def require_user(
    user: Optional[User] = Depends(
        get_optional_user
    ),
) -> User:
    """
    Require a valid authenticated normal user.
    """

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="You must be logged in.",
            headers={
                "WWW-Authenticate": "Bearer",
            },
        )

    return user


# ============================================================
# CREATOR PROFILE RECOVERY
# ============================================================

def _load_creator_profile(
    db: Session,
    user: User,
) -> Optional[Profile]:
    """
    Explicitly load the creator profile by user_id.

    This is deliberately independent of the SQLAlchemy relationship.
    It fixes deployments where an existing creator account has a valid
    profile row but user.profile is stale or unavailable.

    No database row is created here.
    """

    try:
        profile = (
            db.query(Profile)
            .filter(
                Profile.user_id == str(user.id)
            )
            .first()
        )
    except Exception:
        return None

    if profile is None:
        return None

    try:
        user.profile = profile
    except Exception:
        pass

    return profile


# ============================================================
# CREATOR CHECK
# ============================================================

def _is_creator(
    user: User,
    profile: Optional[Profile] = None,
) -> bool:
    """
    Determine whether this account is a creator.

    Normal creator role:
        creator

    Legacy compatibility:
        producer

    Additional compatibility:
        a profile explicitly marked is_producer=True

    The profile fallback is only used when a profile belongs to this
    exact user, so buyer accounts are not promoted accidentally.
    """

    role = get_role_name(user)

    if role in {
        "creator",
        "producer",
    }:
        return True

    if profile is not None:
        return bool(
            getattr(
                profile,
                "is_producer",
                False,
            )
        )

    return False


# ============================================================
# REQUIRED CREATOR
# ============================================================

def require_creator(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> User:
    """
    Require an authenticated BeatHub creator/producer.

    This is the dependency used by:
        /dashboard
        /dashboard/
        /dashboard/upload
        /dashboard/albums/new
        /dashboard/withdraw
        /dashboard/merch
        and other creator-only routes.

    The important repair is that the creator profile is explicitly
    recovered from the database before access is denied.
    """

    role = get_role_name(user)

    profile = None

    try:
        profile = getattr(
            user,
            "profile",
            None,
        )
    except Exception:
        profile = None

    if profile is None:
        profile = _load_creator_profile(
            db,
            user,
        )

    creator = _is_creator(
        user,
        profile,
    )

    if not creator:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Creator access required. "
                f"Current account role: {role or 'unknown'}."
            ),
        )

    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Creator profile is missing. "
                "Please contact BeatHub support."
            ),
        )

    try:
        user.profile = profile
    except Exception:
        pass

    return user


# ============================================================
# BUYER / ARTIST CHECK
# ============================================================

def require_buyer(
    user: User = Depends(require_user),
) -> User:
    """
    Require a buyer/artist/customer account.

    Creator/admin users are intentionally excluded.
    """

    role = get_role_name(user)

    if role not in {
        "buyer",
        "artist",
        "customer",
        "user",
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Buyer access required.",
        )

    return user


# ============================================================
# ADMIN CHECK
# ============================================================

def require_admin(
    request: Request,
    db: Session = Depends(get_db),
    beathub_session: Optional[str] = Cookie(
        default=None,
        alias=SESSION_COOKIE_NAME,
    ),
):
    """
    Require the configured BeatHub administrator session.

    Supports:
        - environment-configured admin JWT
        - database users whose role is admin
    """

    token = _get_token_from_request(
        request,
        beathub_session,
    )

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Administrator login required.",
            headers={
                "WWW-Authenticate": "Bearer",
            },
        )

    payload = _decode_token(token)

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Administrator session is invalid or expired.",
            headers={
                "WWW-Authenticate": "Bearer",
            },
        )

    subject = (
        payload.get("sub")
        or payload.get("user_id")
        or payload.get("id")
    )

    payload_role = str(
        payload.get("role", "")
    ).strip().lower()

    if (
        str(subject) == ADMIN_SESSION_SUBJECT
        and (
            payload_role == "admin"
            or payload.get("admin") is True
        )
    ):
        return payload

    if subject:
        user = _get_user_from_subject(
            db,
            str(subject),
        )

        if user is not None:

            if not getattr(
                user,
                "is_active",
                True,
            ):
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


# ============================================================
# COMPATIBILITY ALIASES
# ============================================================

def get_current_user(
    user: User = Depends(require_user),
) -> User:
    """
    Backward-compatible current-user dependency.
    """

    return user


def get_current_creator(
    user: User = Depends(require_creator),
) -> User:
    """
    Backward-compatible creator dependency.
    """

    return user


def get_current_admin(
    admin=Depends(require_admin),
):
    """
    Backward-compatible admin dependency.
    """

    return admin
