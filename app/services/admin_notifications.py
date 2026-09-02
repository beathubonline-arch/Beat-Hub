"""Reliable, non-blocking transactional notifications to the BeatHub admin mailbox."""

import hashlib
import logging

import httpx

from app.config import settings

logger = logging.getLogger("beathub.admin_notifications")
RESEND_API_URL = "https://api.resend.com/emails"


def notify_admin(subject: str, body: str, *, idempotency_key: str | None = None) -> bool:
    """Send one admin alert without allowing email failure to break a business flow.

    ``idempotency_key`` is stable for a business event so a retry cannot create
    duplicate messages at the provider. A deterministic fallback is used when
    callers do not provide one.
    """
    recipient = str(getattr(settings, "ADMIN_EMAIL", "") or "").strip()
    api_key = str(getattr(settings, "RESEND_API_KEY", "") or "").strip()
    sender = str(
        getattr(settings, "ADMIN_FROM", "") or "BeatHub Admin <admin@mybeathub.com>"
    ).strip()
    reply_to = str(
        getattr(settings, "SUPPORT_EMAIL", "") or "support@mybeathub.com"
    ).strip()

    if not recipient or not api_key or not sender or not bool(
        getattr(settings, "EMAIL_ENABLED", False)
    ):
        logger.error("Admin notification skipped: email configuration is incomplete.")
        return False

    event_key = idempotency_key or hashlib.sha256(
        f"admin:{recipient}:{subject}:{body}".encode("utf-8")
    ).hexdigest()
    payload = {
        "from": sender,
        "to": [recipient],
        "subject": subject,
        "text": body,
        "reply_to": reply_to,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Idempotency-Key": event_key[:64],
        "User-Agent": f"BeatHub/1.0 (+{str(getattr(settings, 'BASE_URL', 'https://mybeathub.com')).rstrip('/')})",
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(RESEND_API_URL, json=payload, headers=headers)
        if 200 <= response.status_code < 300:
            logger.info("Admin notification sent: %s", subject)
            return True
        logger.error(
            "Admin notification failed with HTTP %s: %s",
            response.status_code,
            response.text[:500],
        )
    except Exception:
        logger.exception("Admin notification failed safely: %s", subject)
    return False


def notify_new_user(user) -> bool:
    user_id = getattr(user, "id", "")
    return notify_admin(
        "BeatHub — New user registration",
        "A new BeatHub account was created.\n\n"
        f"Email: {getattr(user, 'email', '')}\n"
        f"Username: {getattr(user, 'username', '')}\n"
        f"Role: {getattr(getattr(user, 'role', None), 'value', getattr(user, 'role', ''))}\n"
        f"User ID: {user_id}\n"
        f"Created: {getattr(user, 'created_at', '')}\n",
        idempotency_key=f"new-user:{user_id}",
    )


def notify_payment(order, payment_type: str = "music") -> bool:
    order_id = getattr(order, "id", "")
    return notify_admin(
        f"BeatHub — Payment completed ({payment_type})",
        "A payment has been successfully verified and fulfilled.\n\n"
        f"Order: {getattr(order, 'order_number', order_id)}\n"
        f"Amount: {getattr(order, 'gross_amount', getattr(order, 'total_amount', ''))} "
        f"{getattr(order, 'currency', 'KES')}\n"
        f"Buyer ID: {getattr(order, 'buyer_id', '')}\n"
        f"Status: {getattr(getattr(order, 'status', None), 'value', getattr(order, 'status', ''))}\n",
        idempotency_key=f"payment:{payment_type}:{order_id}",
    )


def notify_withdrawal(withdrawal) -> bool:
    withdrawal_id = getattr(withdrawal, "id", "")
    return notify_admin(
        "BeatHub — New creator withdrawal request",
        "A creator withdrawal request requires attention.\n\n"
        f"Request ID: {withdrawal_id}\n"
        f"Amount: KSh {getattr(withdrawal, 'amount', '')}\n"
        f"Phone: {getattr(withdrawal, 'phone_number', '')}\n"
        f"Status: {getattr(withdrawal, 'status', '')}\n"
        f"Creator profile: {getattr(withdrawal, 'creator_profile_id', '')}\n",
        idempotency_key=f"withdrawal:{withdrawal_id}",
    )
