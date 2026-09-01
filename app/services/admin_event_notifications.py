"""Post-commit notifications for important BeatHub business events."""

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models.order import Order, OrderStatus
from app.models.user import User
from app.services.admin_notifications import notify_new_user, notify_payment

_KEY = "beathub_admin_notifications"


def _queue(session: Session, key: str, callback, *args) -> None:
    bucket = session.info.setdefault(_KEY, {})
    bucket.setdefault(key, (callback, args))


@event.listens_for(User, "after_insert")
def _new_user(mapper, connection, target):
    session = Session.object_session(target)
    if session is not None:
        _queue(session, f"user:{target.id}", notify_new_user, target)


@event.listens_for(Order, "after_update")
def _completed_order(mapper, connection, target):
    if getattr(target, "status", None) != OrderStatus.COMPLETED:
        return
    session = Session.object_session(target)
    if session is not None:
        _queue(session, f"order:{target.id}", notify_payment, target, "music")


@event.listens_for(Session, "after_commit")
def _send_queued(session: Session):
    bucket = session.info.pop(_KEY, {})
    for callback, args in bucket.values():
        try:
            callback(*args)
        except Exception:
            # Notification failures must never affect a committed business transaction.
            pass
