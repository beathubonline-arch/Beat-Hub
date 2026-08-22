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
- Robust creator/profile detection
"""

from __future__ import annotations

from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.profile import Profile
from app.models.user import User, UserRole
from app.utils.security import decode_access_token


# ============================================================
# SESSION CONSTANTS
# ============================================================

SESSION_COOKIE_NAME = "beathub_session"

# Compatibility with the administrator authentication flow.
ADMIN_SESSION_SUBJECT = "beathub-admin"

# Seven-day login session.
COOKIE_MAX_AGE = 60 * 60 * 24 * 7


# ============================================================
# ROLE NORMALIZATION
# ============================================================

def get_role_name(user: Optional[User]) -> str:
    """
    Return a normalized role string.

    Supports:
        UserRole.CREATOR
        UserRole.PRODUCER
        "creator"
        "producer"
        "CREATOR"
        "PRODUCER"

    Also handles SQLAlchemy/Python enum implementations where
    the useful value is stored in either .value or .name.
    """

    if user is None:
        return ""

    role = getattr(user, "role", None)

    if role is None:
        return ""

    # --------------------------------------------------------
    # Enum value
    # --------------------------------------------------------

    value = getattr(role, "value", None)

    if value is not None:
        normalized = str(value).strip().lower()

        if normalized:
            return normalized

    # --------------------------------------------------------
    # Enum name
    # --------------------------------------------------------

    name = getattr(role, "name", None)

    if name is not None:
        normalized = str(name).strip().lower()

        if normalized:
            return normalized

    # --------------------------------------------------------
    # Plain string / fallback
    # --------------------------------------------------------

    normalized = str(role).strip().lower()

    # Handles values such as:
    #
    # UserRole.CREATOR
    # UserRole.PRODUCER
    #
    if "." in normalized:
        normalized = normalized.rsplit(".", 1)[-1]

    return normalized


# ============================================================
# ROLE CLASSIFICATION
# ============================================================

def _is_creator_role_value(role: str) -> bool:
    """
    Determine whether a normalized role represents a producer/
    creator account.

    This intentionally accepts the role names used across the
    BeatHub application and older versions of the application.
    """

    normalized = (
        str(role or "")
        .strip()
        .lower()
    )

    return normalized in {
        "creator",
        "producer",
        "beatmaker",
        "music_producer",
        "music-producer",
        "creator_producer",
        "creator-producer",
    }


def _is_buyer_role_value(role: str) -> bool:
    """
    Determine whether a normalized role represents a buyer/artist
    account.
    """

    normalized = (
        str(role or "")
        .strip()
        .lower()
    )

    return normalized in {
        "buyer",
        "artist",
        "customer",
        "user",
    }


def _is_admin_role_value(role: str) -> bool:
    """
    Determine whether a normalized role represents an admin.
    """

    normalized = (
        str(role or "")
        .strip()
        .lower()
    )

    return normalized == "admin"


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

    if str(subject) == ADMIN_SESSION_SUBJECT:
        return None

    user = (
        db.query(User)
        .filter(
            User.id == str(subject)
        )
        .first()
    )

    return user


# ============================================================
# PROFILE LOOKUP
# ============================================================

def _get_user_profile(
    db: Session,
    user: User,
) -> Optional[Profile]:
    """
    Reliably retrieve the user's producer profile.

    Normally user.profile should already work through the
    SQLAlchemy relationship.

    However, older BeatHub database records or relationship
    loading differences can occasionally make:

        user.profile

    appear empty even though the Profile row exists.

    Therefore we check both:
        1. SQLAlchemy relationship
        2. Direct Profile query

    If a profile exists, it is attached back to the user object
    for the rest of the request.
    """

    # --------------------------------------------------------
    # First use the normal SQLAlchemy relationship.
    # --------------------------------------------------------

    profile = getattr(
        user,
        "profile",
        None,
    )

    if profile is not None:
        return profile

    # --------------------------------------------------------
    # Fallback: direct database lookup.
    # --------------------------------------------------------

    user_id = getattr(
        user,
        "id",
        None,
    )

    if not user_id:
        return None

    profile = (
        db.query(Profile)
        .filter(
            Profile.user_id == str(user_id)
        )
        .first()
    )

    if profile is None:
        return None

    # --------------------------------------------------------
    # Re-attach the profile to the SQLAlchemy object.
    # --------------------------------------------------------

    try:
        user.profile = profile
    except Exception:
        # The direct profile lookup is still valid even if the
        # relationship cannot be assigned for some reason.
        pass

    return profile


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

    Also accepts:
        Authorization: Bearer <token>

    This preserves compatibility with existing routes.
    """

    # --------------------------------------------------------
    # Session cookie
    # --------------------------------------------------------

    if cookie_token:
        token = cookie_token.strip()

        if token:
            return token

    # --------------------------------------------------------
    # Authorization header
    # --------------------------------------------------------

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

def _decode_token(
    token: str,
) -> Optional[dict]:
    """
    Safely decode the existing BeatHub access token.

    Invalid or expired tokens are treated as unauthenticated.

    decode_access_token() may return:
        dict
    or:
        None
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
    - token belongs to the dedicated admin session
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

    subject = str(subject)

    # --------------------------------------------------------
    # Dedicated administrator session
    # --------------------------------------------------------

    if subject == ADMIN_SESSION_SUBJECT:
        return None

    # --------------------------------------------------------
    # Database user
    # --------------------------------------------------------

    user = _get_user_from_subject(
        db,
        subject,
    )

    if user is None:
        return None

    # --------------------------------------------------------
    # Active account check
    # --------------------------------------------------------

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
    Require an authenticated normal BeatHub user.
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

def _is_creator(
    user: User,
) -> bool:
    """
    Central creator-role check.

    Supports:
        creator
        producer
        beatmaker
        music_producer
        creator_producer

    and equivalent enum representations.
    """

    if user is None:
        return False

    role = get_role_name(user)

    return _is_creator_role_value(role)


# ============================================================
# REQUIRED CREATOR
# ============================================================

def require_creator(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> User:
    """
    Require an authenticated creator/producer.

    Used by:
        /dashboard
        /dashboard/upload
        /dashboard/albums/new
        /dashboard/withdraw

    IMPORTANT:
    The profile check is now performed through a direct database
    fallback as well as the normal SQLAlchemy relationship.

    This prevents a legitimate producer from receiving a false
    403 merely because user.profile was not populated.
    """

    # --------------------------------------------------------
    # Role
    # --------------------------------------------------------

    if not _is_creator(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Creator access required.",
        )

    # --------------------------------------------------------
    # Producer profile
    # --------------------------------------------------------

    profile = _get_user_profile(
        db,
        user,
    )

    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Creator profile is missing. "
                "Please complete your creator profile."
            ),
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

    Creator and admin users are not treated as buyers.
    """

    role = get_role_name(user)

    if not _is_buyer_role_value(role):
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

        1. Environment-configured admin JWT
        2. Database users whose role is admin
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
            detail=(
                "Administrator session is invalid or expired."
            ),
            headers={
                "WWW-Authenticate": "Bearer",
            },
        )

    # --------------------------------------------------------
    # JWT subject
    # --------------------------------------------------------

    subject = (
        payload.get("sub")
        or payload.get("user_id")
        or payload.get("id")
    )

    if subject is not None:
        subject = str(subject)

    payload_role = str(
        payload.get("role", "")
    ).strip().lower()

    # --------------------------------------------------------
    # Dedicated environment-configured admin session
    # --------------------------------------------------------

    if (
        subject == ADMIN_SESSION_SUBJECT
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
            subject,
        )

        if user is not None:

            # ------------------------------------------------
            # Active account
            # ------------------------------------------------

            if not getattr(
                user,
                "is_active",
                True,
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "Administrator account is inactive."
                    ),
                )

            # ------------------------------------------------
            # Admin role
            # ------------------------------------------------

            if _is_admin_role_value(
                get_role_name(user)
            ):
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
