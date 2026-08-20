"""
FastAPI authentication and role-based authorization dependencies.
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
    """Return the authenticated user or None."""

    token = request.cookies.get(SESSION_COOKIE_NAME)

    if not token:
        return None

    try:
        payload = decode_access_token(token)
    except Exception:
        return None

    if not payload:
        return None

    user_id = payload.get("sub")

    if not user_id:
        return None

    try:
        user = db.get(User, str(user_id))
    except Exception:
        return None

    if not user or not user.is_active:
        return None

    return user


def require_user(
    user: Optional[User] = Depends(get_optional_user),
) -> User:
    """Require a valid authenticated user."""

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    return user


def get_role_value(user: User) -> str:
    """
    Normalize SQLAlchemy Enum/string roles.

    This prevents routing problems when role is returned as
    UserRole.BUYER versus 'buyer'.
    """

    role = getattr(user, "role", None)

    if isinstance(role, UserRole):
        return role.value.lower()

    value = getattr(role, "value", role)

    if value is None:
        return ""

    value = str(value).strip().lower()

    if value.startswith("userrole."):
        value = value.split(".", 1)[1]

    return value


def is_buyer(user: User) -> bool:
    return get_role_value(user) == "buyer"


def is_creator(user: User) -> bool:
    return get_role_value(user) == "creator"


def is_admin(user: User) -> bool:
    return get_role_value(user) == "admin"


def require_creator(
    user: User = Depends(require_user),
) -> User:
    """Allow creators and administrators."""

    if not (is_creator(user) or is_admin(user)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Creator account required",
        )

    return user


def require_admin(
    user: User = Depends(require_user),
) -> User:
    """Require administrator access."""

    if not is_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required",
        )

    return user
