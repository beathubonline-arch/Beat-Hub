"""Non-blocking transactional notifications to the BeatHub admin mailbox."""

import logging
import httpx
from app.config import settings

logger = logging.getLogger("beathub.admin_notifications")
RESEND_API_URL = "https://api.resend.com/emails"


def notify_admin(subject: str, body: str) -> bool:
    recipient = str(getattr(settings, "ADMIN_EMAIL", "") or "").strip()
    api_key = str(getattr(settings, "RESEND_API_KEY", "") or "").strip()
    sender = str(getattr(settings, "ADMIN_FROM", "") or "BeatHub Admin <admin@mybeathub.com>").strip()
    if not recipient or not api_key or not sender or not bool(getattr(settings, "EMAIL_ENABLED", False)):
        logger.error("Admin notification skipped: email configuration is incomplete.")
        return False
    payload = {
        "from": sender,
        "to": [recipient],
        "subject": subject,
        "text": body,
        "reply_to": str(getattr(settings, "SUPPORT_EMAIL", "") or "support@mybeathub.com").strip(),
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": f"BeatHub/1.0 (+{str(getattr(settings, 'BASE_URL', 'https://mybeathub.com')).rstrip('/')})",
    }
    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(RESEND_API_URL, json=payload, headers=headers)
        if 200 <= response.status_code < 300:
            logger.info("Admin notification sent: %s", subject)
            return True
        logger.error("Admin notification failed with HTTP %s", response.status_code)
    except Exception:
        logger.exception("Admin notification failed safely: %s", subject)
    return False


def notify_new_user(user) -> bool:
    return notify_admin("BeatHub — New user registration", "A new BeatHub account was created.\n\n" f"Email: {getattr(user, 'email', '')}\nUsername: {getattr(user, 'username', '')}\nRole: {getattr(getattr(user, 'role', None), 'value', getattr(user, 'role', ''))}\nUser ID: {getattr(user, 'id', '')}\nCreated: {getattr(user, 'created_at', '')}\n")


def notify_payment(order, payment_type: str = "music") -> bool:
    return notify_admin(f"BeatHub — Payment completed ({payment_type})", "A payment has been successfully verified and fulfilled.\n\n" f"Order: {getattr(order, 'order_number', getattr(order, 'id', ''))}\nAmount: {getattr(order, 'gross_amount', getattr(order, 'total_amount', ''))} {getattr(order, 'currency', 'KES')}\nBuyer ID: {getattr(order, 'buyer_id', '')}\nStatus: {getattr(getattr(order, 'status', None), 'value', getattr(order, 'status', ''))}\n")
