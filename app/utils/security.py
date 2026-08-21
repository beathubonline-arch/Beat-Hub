"""
Password hashing and JWT session token utilities.

Uses bcrypt directly instead of passlib.
"""

from datetime import datetime, timedelta
from typing import Optional

import bcrypt
import jwt

from app.config import settings


_MAX_PASSWORD_BYTES = 72


def _prepare(
    password: str,
) -> bytes:
    """
    bcrypt has a 72-byte maximum.
    """

    return password.encode(
        "utf-8"
    )[:_MAX_PASSWORD_BYTES]


def hash_password(
    plain_password: str,
) -> str:
    hashed = bcrypt.hashpw(
        _prepare(plain_password),
        bcrypt.gensalt(),
    )

    return hashed.decode(
        "utf-8"
    )


def verify_password(
    plain_password: str,
    hashed_password: str,
) -> bool:
    try:
        return bcrypt.checkpw(
            _prepare(plain_password),
            hashed_password.encode(
                "utf-8"
            ),
        )

    except Exception:
        return False


def create_access_token(
    subject: str,
    extra_claims: Optional[dict] = None,
) -> str:
    now = datetime.utcnow()

    expire = (
        now
        + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    )

    payload = {
        "sub": subject,
        "exp": expire,
        "iat": now,
    }

    if extra_claims:
        payload.update(
            extra_claims
        )

    return jwt.encode(
        payload,
        settings.SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_access_token(
    token: str,
) -> Optional[dict]:
    try:
        return jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[
                settings.JWT_ALGORITHM
            ],
        )

    except jwt.PyJWTError:
        return None
