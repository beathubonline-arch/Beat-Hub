"""
Password hashing and JWT session token utilities.

Uses the `bcrypt` library directly rather than passlib's bcrypt wrapper —
passlib's backend version-detection is incompatible with modern bcrypt
(>=4.1) releases and raises spurious errors.
"""
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
import jwt

from app.config import settings

# bcrypt has a hard 72-byte input limit; longer passwords are truncated
# consistently on both hash and verify so behavior stays correct.
_MAX_PASSWORD_BYTES = 72


def _prepare(password: str) -> bytes:
    return password.encode("utf-8")[:_MAX_PASSWORD_BYTES]


def hash_password(plain_password: str) -> str:
    hashed = bcrypt.hashpw(_prepare(plain_password), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(_prepare(plain_password), hashed_password.encode("utf-8"))
    except Exception:
        return False


def create_access_token(subject: str, extra_claims: Optional[dict] = None) -> str:
    expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": subject, "exp": expire, "iat": datetime.utcnow()}
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
