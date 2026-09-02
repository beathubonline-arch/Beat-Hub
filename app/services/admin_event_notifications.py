"""Post-commit notifications for important BeatHub business events."""

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.models.ledger import WithdrawalRequest
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.services.admin_notifications import notify_new_user, notify_payment, notify_withdrawal
from app.services.transactional_email_notifications import notify_completed_music_sale

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
    """Queue admin + buyer + creator emails only on the completion transition."""
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

    _queue(session, f"admin-order:{target.id}", notify_payment, target, "music")

    # Capture all recipient data before commit. The post-commit callback must not
    # depend on lazy-loading expired ORM relationships.
    buyer = getattr(target, "buyer", None)
    track = getattr(target, "track", None)
    album = getattr(target, "album", None)
    buyer_email = str(getattr(buyer, "email", "") or "").strip()
    buyer_name = str(getattr(buyer, "username", "") or "there").strip()

    item_name = str(
        getattr(track, "title", "")
        or getattr(album, "title", "")
        or "BeatHub purchase"
    ).strip()

    profile = getattr(track, "creator_profile", None) or getattr(album, "creator_profile", None)
    creator_user = getattr(profile, "user", None)
    creator_email = str(getattr(creator_user, "email", "") or "").strip()
    creator_name = str(
        getattr(profile, "stage_name", "")
        or getattr(creator_user, "username", "")
        or "Creator"
    ).strip()

    _queue(
        session,
        f"transactional-order:{target.id}",
        notify_completed_music_sale,
        str(target.id),
        str(getattr(target, "order_number", target.id)),
        getattr(target, "gross_amount", ""),
        getattr(target, "net_amount", ""),
        str(getattr(target, "currency", "KES") or "KES"),
        buyer_email,
        buyer_name,
        creator_email,
        creator_name,
        item_name,
    )


@event.listens_for(WithdrawalRequest, "after_insert")
def _new_withdrawal(mapper, connection, target):
    session = Session.object_session(target)
    if session is not None:
        _queue(session, f"withdrawal:{target.id}", notify_withdrawal, target)


@event.listens_for(Session, "after_commit")
def _send_queued(session: Session):
    """Send after commit so notification delivery can never roll back business data."""
    bucket = session.info.pop(_KEY, {})
    for callback, args in bucket.values():
        try:
            callback(*args)
        except Exception:
            # Notification failure must never break the already-committed action.
            pass
