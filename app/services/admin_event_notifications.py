"""Post-commit notifications for important BeatHub business events."""

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.models.ledger import WithdrawalRequest
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.services.admin_notifications import notify_new_user, notify_payment, notify_withdrawal

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
    """Queue only the pending/failed/rejected -> completed transition."""
    state = inspect(target)
    history = state.attrs.status.history
    if not history.has_changes() or target.status != OrderStatus.COMPLETED:
        return

    session = Session.object_session(target)
    if session is None:
        return

    previous = history.deleted[0] if history.deleted else None
    if previous == OrderStatus.COMPLETED:
        return

    # Orders in this model represent music purchases. Merchandise payments
    # use their own payment service and are not represented by Order.
    _queue(session, f"order:{target.id}", notify_payment, target, "music")


@event.listens_for(WithdrawalRequest, "after_insert")
def _new_withdrawal(mapper, connection, target):
    session = Session.object_session(target)
    if session is not None:
        _queue(session, f"withdrawal:{target.id}", notify_withdrawal, target)


@event.listens_for(Session, "after_commit")
def _send_queued(session: Session):
    """Send after the transaction commits so email can never roll it back."""
    bucket = session.info.pop(_KEY, {})
    for callback, args in bucket.values():
        try:
            callback(*args)
        except Exception:
            # Notification failure must never break the already-committed action.
            pass
