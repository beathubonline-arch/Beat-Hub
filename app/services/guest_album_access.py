"""Guest album purchase access: signed, order-scoped, time-limited browser cookie.

The token is NOT a BeatHub login session. It never authorizes other purchases,
accounts or creator actions. Every download still requires a completed order
and a matching album license.
"""
from datetime import datetime, timedelta, timezone
import jwt
from fastapi import Request
from sqlalchemy.orm import Session
from app.config import settings
from app.models.order import License, Order, OrderStatus
from app.utils.security import _jwt_secret

COOKIE_NAME = "beathub_guest_album"
MAX_AGE_SECONDS = 90 * 24 * 3600


def issue_guest_token(order_id: str) -> str:
    return jwt.encode(
        {"guest_album_order": str(order_id),
         "exp": datetime.now(timezone.utc) + timedelta(seconds=MAX_AGE_SECONDS)},
        _jwt_secret(), algorithm=settings.JWT_ALGORITHM,
    )


def authorized_guest_order(request: Request, db: Session, album_id: str) -> Order | None:
    token = request.cookies.get(COOKIE_NAME, "")
    if not token:
        return None
    try:
        claims = jwt.decode(token, _jwt_secret(), algorithms=[settings.JWT_ALGORITHM])
        order_id = claims.get("guest_album_order")
        if not isinstance(order_id, str):
            return None
        order = db.get(Order, order_id)
        if not order or str(order.album_id) != str(album_id) or order.status != OrderStatus.COMPLETED:
            return None
        licensed = db.query(License).filter(
            License.order_id == order.id, License.album_id == album_id,
            License.buyer_id == order.buyer_id,
        ).first()
        return order if licensed else None
    except (jwt.PyJWTError, RuntimeError, ValueError):
        return None
