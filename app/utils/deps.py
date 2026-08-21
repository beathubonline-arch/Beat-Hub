"""
FastAPI dependencies for authentication and role-based authorization.
"""

from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User, UserRole
from app.utils.security import decode_access_token


SESSION_COOKIE_NAME = "beathub_session"

# Special JWT subject used only for the Render-configured administrator.
ADMIN_SESSION_SUBJECT = "beathub-render-admin"


def _build_virtual_admin() -> User:
    """
    Creates an in-memory User object representing the Render admin.

    The admin is intentionally NOT stored in the users database table.
    """

    admin = User(
        id=ADMIN_SESSION_SUBJECT,
        email="admin",
        hashed_password="",
        role=UserRole.ADMIN,
        is_active=True,
        is_verified=True,
    )

    return admin


def get_optional_user(
    request: Request,
    db: Session = Depends(get_db),
) -> Optional[User]:
    token = request.cookies.get(
        SESSION_COOKIE_NAME
    )

    if not token:
        return None

    payload = decode_access_token(token)

    if not payload:
        return None

    user_id = payload.get("sub")

    if not user_id:
        return None

    # --------------------------------------------------------------
    # Render environment-variable admin
    # --------------------------------------------------------------

    if (
        user_id == ADMIN_SESSION_SUBJECT
        and payload.get("admin") is True
        and payload.get("role") == "admin"
    ):
        return _build_virtual_admin()

    # --------------------------------------------------------------
    # Normal database user
    # --------------------------------------------------------------

    user = db.get(
        User,
        user_id,
    )

    if not user or not user.is_active:
        return None

    return user


def require_user(
    user: Optional[User] = Depends(get_optional_user),
) -> User:
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    return user


def get_role_name(user: User) -> str:
    """
    Safely normalize a user's role.

    Handles:
        UserRole.BUYER
        "buyer"
        "creator"
        "producer"
        "admin"
    """

    role = getattr(
        user,
        "role",
        None,
    )

    if role is None:
        return "buyer"

    value = getattr(
        role,
        "value",
        role,
    )

    value = str(
        value
    ).strip().lower()

    if value in {
        "creator",
        "producer",
        "artist_creator",
    }:
        return "creator"

    if value in {
        "admin",
        "administrator",
        "super_admin",
        "superadmin",
    }:
        return "admin"

    if value in {
        "buyer",
        "artist",
        "user",
        "customer",
    }:
        return "buyer"

    return "buyer"


def is_creator_user(
    user: User,
) -> bool:
    return get_role_name(user) == "creator"


def is_admin_user(
    user: User,
) -> bool:
    return get_role_name(user) == "admin"


def is_creator_or_admin(
    user: User,
) -> bool:
    return get_role_name(user) in {
        "creator",
        "admin",
    }


def require_creator(
    user: User = Depends(require_user),
) -> User:
    if not is_creator_or_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Creator account required",
        )

    return user


def require_admin(
    user: User = Depends(require_user),
) -> User:
    if not is_admin_user(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required",
        )

    return user
