"""Non-blocking transactional notifications to the BeatHub admin mailbox."""

import logging

from app.config import settings
from app.routers.auth import _send_email

logger = logging.getLogger("beathub.admin_notifications")


def notify_admin(subject: str, body: str) -> bool:
    """Send an internal alert without ever failing the user transaction."""
    recipient = str(getattr(settings, "ADMIN_EMAIL", "") or "").strip()
    if not recipient:
        logger.error("Admin notification skipped: ADMIN_EMAIL is not configured.")
        return False
    try:
        sent = _send_email(
            recipient,
            subject,
            body,
            sender=str(getattr(settings, "ADMIN_FROM", "") or "BeatHub Admin <admin@mybeathub.com>").strip(),
            reply_to=str(getattr(settings, "SUPPORT_EMAIL", "") or "support@mybeathub.com").strip(),
        )
        if not sent:
            logger.error("Admin notification could not be delivered: %s", subject)
        return sent
    except Exception:
        logger.exception("Admin notification crashed safely: %s", subject)
        return False


def notify_new_user(user) -> bool:
    return notify_admin(
        "BeatHub — New user registration",
        "A new BeatHub account was created.\n\n"
        f"Email: {getattr(user, 'email', '')}\n"
        f"Username: {getattr(user, 'username', '')}\n"
        f"Role: {getattr(getattr(user, 'role', None), 'value', getattr(user, 'role', ''))}\n"
        f"User ID: {getattr(user, 'id', '')}\n"
        f"Created: {getattr(user, 'created_at', '')}\n",
    )


def notify_payment(order, payment_type: str = "music") -> bool:
    return notify_admin(
        f"BeatHub — Payment completed ({payment_type})",
        "A payment has been successfully verified and fulfilled.\n\n"
        f"Order: {getattr(order, 'order_number', getattr(order, 'id', ''))}\n"
        f"Amount: {getattr(order, 'gross_amount', getattr(order, 'total_amount', ''))} {getattr(order, 'currency', 'KES')}\n"
        f"Buyer ID: {getattr(order, 'buyer_id', '')}\n"
        f"Status: {getattr(getattr(order, 'status', None), 'value', getattr(order, 'status', ''))}\n",
    )
