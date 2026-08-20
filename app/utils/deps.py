"""
FastAPI authentication and authorization dependencies for BeatHub.
"""

from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User, UserRole
from app.utils.security import decode_access_token


SESSION_COOKIE_NAME = "beathub_session"


def get_optional_user(
    request: Request,
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Return the authenticated user, or None."""

    token = request.cookies.get(SESSION_COOKIE_NAME)

    if not token:
        return None

    payload = decode_access_token(token)

    if not payload:
        return None

    user_id = payload.get("sub")

    if not user_id:
        return None

    user = db.get(User, user_id)

    if not user or not user.is_active:
        return None

    return user


def require_user(
    user: Optional[User] = Depends(get_optional_user),
) -> User:
    """Require an authenticated user."""

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    return user


def is_admin(user: User) -> bool:
    """Safely determine whether the account is an administrator."""

    role = getattr(user, "role", None)

    if role is None:
        return False

    role_name = getattr(role, "name", "")
    role_value = getattr(role, "value", "")

    return (
        str(role_name).upper() == "ADMIN"
        or str(role_value).upper() == "ADMIN"
    )


def is_creator(user: User) -> bool:
    """
    Determine creator status from the user's profile.

    This intentionally uses profile.is_producer rather than relying
    exclusively on UserRole values, preventing buyer/creator routing
    loops if enum values change or overlap.
    """

    if is_admin(user):
        return True

    profile = getattr(user, "profile", None)

    if not profile:
        return False

    return bool(
        getattr(profile, "is_producer", False)
    )


def is_buyer(user: User) -> bool:
    """Determine whether the account is a normal buyer/artist account."""

    if is_admin(user):
        return False

    return not is_creator(user)


def require_creator(
    user: User = Depends(require_user),
) -> User:
    """Require a creator or administrator account."""

    if not is_creator(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Creator account required",
        )

    return user


def require_admin(
    user: User = Depends(require_user),
) -> User:
    """Require an administrator account."""

    if not is_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required",
        )

    return user
