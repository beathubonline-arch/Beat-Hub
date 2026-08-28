"""Password hashing and JWT authentication utilities."""

from datetime import datetime, timedelta
from typing import Optional

import bcrypt
import jwt

from app.config import settings

_MAX_PASSWORD_BYTES = 72


def _prepare(password: str) -> bytes:
    encoded = password.encode("utf-8")
    if len(encoded) > _MAX_PASSWORD_BYTES:
        raise ValueError(
            "Password is too long for bcrypt. Please use a password of 72 UTF-8 bytes or fewer."
        )
    return encoded


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(_prepare(plain_password), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(_prepare(plain_password), hashed_password.encode("utf-8"))
    except (ValueError, TypeError, UnicodeError, bcrypt.exceptions.BcryptError):
        return False
    except Exception:
        return False


def _jwt_secret() -> str:
    """Return the configured JWT secret.

    SECRET_KEY is preferred. SESSION_SECRET is retained as a compatibility
    fallback because existing BeatHub deployments may already have that value.
    Never invent or hard-code a signing secret.
    """
    secret = (settings.SECRET_KEY or settings.SESSION_SECRET or "").strip()
    if not secret:
        raise RuntimeError(
            "Authentication is not configured. Set SECRET_KEY (or the existing "
            "SESSION_SECRET) in the BeatHub Render environment."
        )
    return secret


def create_access_token(subject: str, extra_claims: Optional[dict] = None) -> str:
    now = datetime.utcnow()
    payload = {
        "sub": str(subject),
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "iat": now,
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, _jwt_secret(), algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=[settings.JWT_ALGORITHM])
    except (jwt.PyJWTError, RuntimeError):
        return None
