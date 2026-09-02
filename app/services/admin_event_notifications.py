"""Post-commit notifications for important BeatHub business events."""

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.models.ledger import WithdrawalRequest
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.services.admin_notifications import notify_new_user, notify_payment, notify_withdrawal
from app.services.transactional_email_notifications import (
    notify_completed_music_sale,
    notify_failed_payment,
    notify_withdrawal_requested,
    notify_withdrawal_status,
)

_KEY = "beathub_admin_notifications"


def _queue(session: Session, key: str, callback, *args) -> None:
    bucket = session.info.setdefault(_KEY, {})
    bucket.setdefault(key, (callback, args))


def _withdrawal_recipient(target):
    """Capture creator identity while ORM relationships are still available."""
    profile = getattr(target, "creator_profile", None)
    creator_user = getattr(profile, "user", None)
    return (
        str(getattr(creator_user, "email", "") or "").strip(),
        str(getattr(profile, "stage_name", "") or getattr(creator_user, "username", "") or "Creator").strip(),
    )


@event.listens_for(User, "after_insert")
def _new_user(mapper, connection, target):
    session = Session.object_session(target)
    if session is not None:
        _queue(session, f"user:{target.id}", notify_new_user, target)


@event.listens_for(Order, "after_update")
def _order_email_notifications(mapper, connection, target):
    """Queue recipient emails only when an order changes payment state."""
    state = inspect(target)
    history = state.attrs.status.history
    if not history.has_changes():
        return
    session = Session.object_session(target)
    if session is None:
        return
    previous = history.deleted[0] if history.deleted else None
    if previous == target.status:
        return

    if target.status == OrderStatus.COMPLETED:
        _queue(session, f"admin-order:{target.id}", notify_payment, target, "music")
        buyer = getattr(target, "buyer", None)
        track = getattr(target, "track", None)
        album = getattr(target, "album", None)
        buyer_email = str(getattr(buyer, "email", "") or "").strip()
        buyer_name = str(getattr(buyer, "username", "") or "there").strip()
        item_name = str(getattr(track, "title", "") or getattr(album, "title", "") or "BeatHub purchase").strip()
        profile = getattr(track, "creator_profile", None) or getattr(album, "creator_profile", None)
        creator_user = getattr(profile, "user", None)
        creator_email = str(getattr(creator_user, "email", "") or "").strip()
        creator_name = str(getattr(profile, "stage_name", "") or getattr(creator_user, "username", "") or "Creator").strip()
        _queue(session, f"transactional-order:{target.id}", notify_completed_music_sale,
               str(target.id), str(getattr(target, "order_number", target.id)),
               getattr(target, "gross_amount", ""), getattr(target, "net_amount", ""),
               str(getattr(target, "currency", "KES") or "KES"), buyer_email, buyer_name,
               creator_email, creator_name, item_name)
    elif target.status == OrderStatus.FAILED:
        buyer = getattr(target, "buyer", None)
        _queue(session, f"failed-order:{target.id}", notify_failed_payment,
               str(getattr(buyer, "email", "") or "").strip(),
               str(getattr(target, "order_number", target.id)),
               "The payment was unsuccessful or was cancelled.")


@event.listens_for(WithdrawalRequest, "after_insert")
def _new_withdrawal(mapper, connection, target):
    session = Session.object_session(target)
    if session is None:
        return
    _queue(session, f"withdrawal-admin:{target.id}", notify_withdrawal, target)
    creator_email, creator_name = _withdrawal_recipient(target)
    _queue(
        session,
        f"withdrawal-requested:{target.id}",
        notify_withdrawal_requested,
        str(target.id),
        getattr(target, "amount", ""),
        str(getattr(target, "phone_number", "") or ""),
        creator_email,
        creator_name,
    )


@event.listens_for(WithdrawalRequest, "after_update")
def _withdrawal_status_notifications(mapper, connection, target):
    """Queue exactly one creator email when a withdrawal status changes."""
    state = inspect(target)
    history = state.attrs.status.history
    if not history.has_changes():
        return
    session = Session.object_session(target)
    if session is None:
        return
    old = history.deleted[0] if history.deleted else None
    new = str(getattr(target.status, "value", target.status) or "").lower()
    old_value = str(getattr(old, "value", old) or "").lower()
    if old_value == new or new not in {"approved", "processing", "paid", "rejected"}:
        return

    creator_email, creator_name = _withdrawal_recipient(target)
    _queue(
        session,
        f"withdrawal-status:{target.id}:{new}",
        notify_withdrawal_status,
        str(target.id),
        getattr(target, "amount", ""),
        str(getattr(target, "phone_number", "") or ""),
        new,
        creator_email,
        creator_name,
        str(getattr(target, "admin_note", "") or "").strip(),
        str(getattr(target, "payout_reference", "") or "").strip(),
    )


@event.listens_for(Session, "after_commit")
def _send_queued(session: Session):
    """Send after commit so notification delivery can never roll back business data."""
    bucket = session.info.pop(_KEY, {})
    for callback, args in bucket.values():
        try:
            callback(*args)
        except Exception:
            pass
