"""
FastAPI dependencies for authentication and role-based authorization.

Session is carried via a secure HTTP-only cookie containing a JWT.
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
    """
    Return the currently authenticated user, if a valid session exists.

    Invalid, expired, missing, or inactive sessions simply return None.
    """
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
    """
    Require a valid authenticated user.
    """
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    return user


def require_creator(
    user: User = Depends(require_user),
) -> User:
    """
    Allow creators and administrators to access creator functionality.

    Administrators retain creator-level access where appropriate.
    """
    if user.role not in (UserRole.CREATOR, UserRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Creator account required",
        )

    return user


def require_admin(
    user: User = Depends(require_user),
) -> User:
    """
    Require an authenticated administrator.
    """
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required",
        )

    return user
