"""
BeatHub authentication dependencies.

Handles:
- Login session cookie
- JWT authentication
- Current user
- Optional current user
- Role detection
- Creator dashboard access
- Buyer/account access
- Admin access
- Compatibility with UserRole enum/string values
"""

from __future__ import annotations

from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User, UserRole
from app.utils.security import decode_access_token


# ============================================================
# SESSION CONSTANTS
# ============================================================

SESSION_COOKIE_NAME = "beathub_session"

# Compatibility with the administrator authentication flow.
ADMIN_SESSION_SUBJECT = "beathub-admin"

# Kept compatible with the existing authentication system.
COOKIE_MAX_AGE = 60 * 60 * 24 * 7


# ============================================================
# ROLE HELPER
# ============================================================

def get_role_name(user: Optional[User]) -> str:
    """
    Return a normalized role string.

    Supports both:
        UserRole.CREATOR
    and:
        "creator"

    This prevents enum/string mismatches from causing false 403s.
    """

    if user is None:
        return ""

    role = getattr(user, "role", None)

    if role is None:
        return ""

    # Enum instance
    value = getattr(role, "value", None)

    if value is not None:
        return str(value).strip().lower()

    # Plain string
    return str(role).strip().lower()


# ============================================================
# USER LOOKUP
# ============================================================

def _get_user_from_subject(
    db: Session,
    subject: str,
) -> Optional[User]:
    """
    Load a BeatHub user from the JWT subject.

    Admin sessions use ADMIN_SESSION_SUBJECT and therefore
    do not correspond to a database User.
    """

    if not subject:
        return None

    if subject == ADMIN_SESSION_SUBJECT:
        return None

    user = (
        db.query(User)
        .filter(User.id == str(subject))
        .first()
    )

    return user


# ============================================================
# TOKEN EXTRACTION
# ============================================================

def _get_token_from_request(
    request: Request,
    cookie_token: Optional[str],
) -> Optional[str]:
    """
    Read the BeatHub authentication token.

    Primary source:
        beathub_session cookie

    Also accepts an Authorization Bearer token for compatibility.
    """

    if cookie_token:
        return cookie_token.strip() or None

    authorization = request.headers.get(
        "Authorization",
        "",
    ).strip()

    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

        if token:
            return token

    return None


# ============================================================
# TOKEN DECODING
# ============================================================

def _decode_token(token: str) -> Optional[dict]:
    """
    Safely decode the existing BeatHub access token.

    Supports security implementations where decode_access_token()
    returns either:
        dict
    or:
        None

    Any invalid/expired token is treated as unauthenticated.
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
# CURRENT AUTHENTICATED USER
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

    Returns None when:
    - no session exists
    - token is invalid
    - token is expired
    - token belongs to the configured admin session
    - user no longer exists
    - user is inactive
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

    # --------------------------------------------------------
    # JWT subject
    # --------------------------------------------------------

    subject = (
        payload.get("sub")
        or payload.get("user_id")
        or payload.get("id")
    )

    if not subject:
        return None

    # --------------------------------------------------------
    # Administrator session
    # --------------------------------------------------------

    if str(subject) == ADMIN_SESSION_SUBJECT:
        return None

    # --------------------------------------------------------
    # Database user
    # --------------------------------------------------------

    user = _get_user_from_subject(
        db,
        str(subject),
    )

    if user is None:
        return None

    if not getattr(user, "is_active", True):
        return None

    return user


# ============================================================
# REQUIRED USER
# ============================================================

def require_user(
    user: Optional[User] = Depends(get_optional_user),
) -> User:
    """
    Require a normal authenticated BeatHub user.
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
# CREATOR CHECK
# ============================================================

def _is_creator(user: User) -> bool:
    """
    Central creator-role check.

    IMPORTANT:
    Both enum and string values are accepted.

    This fixes the false 403 caused by comparing:

        UserRole.CREATOR

    directly against:

        "creator"
    """

    role = get_role_name(user)

    if role == "creator":
        return True

    # Compatibility with producer naming used by older code.
    if role == "producer":
        return True

    return False


# ============================================================
# REQUIRED CREATOR
# ============================================================

def require_creator(
    user: User = Depends(require_user),
) -> User:
    """
    Require an authenticated creator/producer.

    /dashboard and all producer functions use this dependency.
    """

    if not _is_creator(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Creator access required.",
        )

    # A creator must have a profile because the dashboard,
    # uploads, withdrawals and store all depend on it.
    if getattr(user, "profile", None) is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Creator profile is missing.",
        )

    return user


# ============================================================
# BUYER CHECK
# ============================================================

def require_buyer(
    user: User = Depends(require_user),
) -> User:
    """
    Require a buyer/artist account.

    Creator and admin users are not treated as buyers here.
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
        - admin JWT session
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

    # --------------------------------------------------------
    # Dedicated environment-configured admin session
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Database admin
    # --------------------------------------------------------

    if subject:
        user = _get_user_from_subject(
            db,
            str(subject),
        )

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


# ============================================================
# COMPATIBILITY ALIASES
# ============================================================

def get_current_user(
    user: User = Depends(require_user),
) -> User:
    """
    Backward-compatible alias used by older routers.
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
