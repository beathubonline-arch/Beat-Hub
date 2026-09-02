"""Post-commit email and in-app notifications for important BeatHub events."""
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.models.ledger import WithdrawalRequest
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.services.admin_notifications import notify_new_user, notify_payment, notify_withdrawal
from app.services.notifications import create_notification, notify_admins
from app.services.transactional_email_notifications import notify_completed_music_sale, notify_failed_payment, notify_withdrawal_requested, notify_withdrawal_status

_KEY = "beathub_admin_notifications"


def _queue(session: Session, key: str, callback, *args) -> None:
    session.info.setdefault(_KEY, {}).setdefault(key, (callback, args))


def _withdrawal_recipient(target):
    profile = getattr(target, "creator_profile", None)
    creator_user = getattr(profile, "user", None)
    return str(getattr(creator_user, "id", "") or ""), str(getattr(creator_user, "email", "") or "").strip(), str(getattr(profile, "stage_name", "") or getattr(creator_user, "username", "") or "Creator").strip()


def _in_app(user_id, key, type_, title, message, link=None):
    if user_id:
        create_notification(user_id, key, type_, title, message, link)


@event.listens_for(User, "after_insert")
def _new_user(mapper, connection, target):
    session = Session.object_session(target)
    if session is not None:
        _queue(session, f"user:{target.id}", notify_new_user, target)
        _queue(session, f"user-inapp:{target.id}", notify_admins, f"user:{target.id}", "account", "New BeatHub account", f"A new user account ({target.email}) has been created.", "/admin")


@event.listens_for(Order, "after_update")
def _order_notifications(mapper, connection, target):
    state = inspect(target); history = state.attrs.status.history
    if not history.has_changes(): return
    session = Session.object_session(target)
    if session is None: return
    previous = history.deleted[0] if history.deleted else None
    if previous == target.status: return

    buyer = getattr(target, "buyer", None)
    track = getattr(target, "track", None); album = getattr(target, "album", None)
    buyer_id = str(getattr(buyer, "id", "") or "")
    buyer_email = str(getattr(buyer, "email", "") or "").strip()
    buyer_name = str(getattr(buyer, "username", "") or "there").strip()
    item_name = str(getattr(track, "title", "") or getattr(album, "title", "") or "BeatHub purchase").strip()
    profile = getattr(track, "creator_profile", None) or getattr(album, "creator_profile", None)
    creator_user = getattr(profile, "user", None)
    creator_id = str(getattr(creator_user, "id", "") or "")
    creator_email = str(getattr(creator_user, "email", "") or "").strip()
    creator_name = str(getattr(profile, "stage_name", "") or getattr(creator_user, "username", "") or "Creator").strip()
    order_number = str(getattr(target, "order_number", target.id))

    if target.status == OrderStatus.COMPLETED:
        _queue(session, f"admin-order:{target.id}", notify_payment, target, "music")
        _queue(session, f"transactional-order:{target.id}", notify_completed_music_sale, str(target.id), order_number, getattr(target, "gross_amount", ""), getattr(target, "net_amount", ""), str(getattr(target, "currency", "KES") or "KES"), buyer_email, buyer_name, creator_email, creator_name, item_name)
        _queue(session, f"inapp-buyer-sale:{target.id}", _in_app, buyer_id, f"purchase:{target.id}", "sale", "Purchase complete", f"Your purchase of {item_name} is complete.", "/account/orders")
        _queue(session, f"inapp-creator-sale:{target.id}", _in_app, creator_id, f"sale:{target.id}", "sale", "You made a sale", f"{item_name} was purchased on BeatHub.", "/dashboard")
        _queue(session, f"inapp-admin-sale:{target.id}", notify_admins, f"sale:{target.id}", "payment", "Payment completed", f"Order {order_number} was completed for {item_name}.", "/admin")
    elif target.status == OrderStatus.FAILED:
        _queue(session, f"failed-order:{target.id}", notify_failed_payment, buyer_email, order_number, "The payment was unsuccessful or was cancelled.")
        _queue(session, f"inapp-buyer-failed:{target.id}", _in_app, buyer_id, f"payment-failed:{target.id}", "payment", "Payment not completed", f"Your payment for {item_name} was not completed.", "/account/orders")


@event.listens_for(WithdrawalRequest, "after_insert")
def _new_withdrawal(mapper, connection, target):
    session = Session.object_session(target)
    if session is None: return
    _queue(session, f"withdrawal-admin:{target.id}", notify_withdrawal, target)
    creator_id, creator_email, creator_name = _withdrawal_recipient(target)
    _queue(session, f"withdrawal-requested:{target.id}", notify_withdrawal_requested, str(target.id), getattr(target, "amount", ""), str(getattr(target, "phone_number", "") or ""), creator_email, creator_name)
    _queue(session, f"withdrawal-requested-inapp:{target.id}", _in_app, creator_id, f"withdrawal-requested:{target.id}", "withdrawal", "Withdrawal request received", "Your withdrawal request has been received and is awaiting review.", "/dashboard/withdraw")
    _queue(session, f"withdrawal-requested-admin-inapp:{target.id}", notify_admins, f"withdrawal:{target.id}", "withdrawal", "New withdrawal request", f"A creator requested a withdrawal of KSh {getattr(target, 'amount', '')}.", "/admin/withdraw")


@event.listens_for(WithdrawalRequest, "after_update")
def _withdrawal_status_notifications(mapper, connection, target):
    state = inspect(target); history = state.attrs.status.history
    if not history.has_changes(): return
    session = Session.object_session(target)
    if session is None: return
    old = history.deleted[0] if history.deleted else None
    new = str(getattr(target.status, "value", target.status) or "").lower(); old_value = str(getattr(old, "value", old) or "").lower()
    if old_value == new or new not in {"approved", "processing", "paid", "rejected"}: return
    creator_id, creator_email, creator_name = _withdrawal_recipient(target)
    request_id = str(target.id)
    _queue(session, f"withdrawal-status:{request_id}:{new}", notify_withdrawal_status, request_id, getattr(target, "amount", ""), str(getattr(target, "phone_number", "") or ""), new, creator_email, creator_name, str(getattr(target, "admin_note", "") or "").strip(), str(getattr(target, "payout_reference", "") or "").strip())
    labels = {"approved": "Withdrawal approved", "processing": "Withdrawal processing", "paid": "Withdrawal paid", "rejected": "Withdrawal rejected"}
    messages = {"approved": "Your withdrawal has been approved.", "processing": "Your withdrawal is now being processed.", "paid": "Your withdrawal has been paid.", "rejected": "Your withdrawal request was rejected."}
    _queue(session, f"withdrawal-status-inapp:{request_id}:{new}", _in_app, creator_id, f"withdrawal-status:{request_id}:{new}", "withdrawal", labels[new], messages[new], "/dashboard/withdraw")


@event.listens_for(Session, "after_commit")
def _send_queued(session: Session):
    bucket = session.info.pop(_KEY, {})
    for callback, args in bucket.values():
        try: callback(*args)
        except Exception: pass
