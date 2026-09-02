"""Buyer/creator transactional email notifications for completed music sales."""

from __future__ import annotations

import hashlib
import logging
from decimal import Decimal
from typing import Any

from app.config import settings
from app.utils.resend_client import send_email

logger = logging.getLogger("beathub.transactional_email")


def _sender() -> str:
    return str(
        getattr(settings, "RESEND_FROM", "")
        or getattr(settings, "EMAIL_FROM", "")
        or "BeatHub <no-reply@mybeathub.com>"
    ).strip()


def _reply_to() -> str:
    return str(getattr(settings, "SUPPORT_EMAIL", "") or "support@mybeathub.com").strip()


def _enabled() -> bool:
    return bool(getattr(settings, "EMAIL_ENABLED", False)) and bool(
        getattr(settings, "RESEND_API_KEY", "")
    )


def _money(value: Any, currency: Any = "KES") -> str:
    try:
        amount = Decimal(str(value))
        return f"{currency or 'KES'} {amount:,.2f}"
    except Exception:
        return f"{currency or 'KES'} {value}"


def _deliver(to_email: str, subject: str, body: str, event_key: str) -> bool:
    recipient = str(to_email or "").strip().lower()
    if not recipient:
        logger.warning("Transactional email skipped: recipient email is missing.")
        return False
    if not _enabled():
        logger.warning("Transactional email skipped: email delivery is disabled or misconfigured.")
        return False

    stable_key = hashlib.sha256(event_key.encode("utf-8")).hexdigest()
    return send_email(
        str(settings.RESEND_API_KEY).strip(),
        _sender(),
        recipient,
        subject,
        body,
        reply_to=_reply_to(),
        idempotency_key=f"transactional-{stable_key}",
    )


def notify_buyer_purchase(order, item_name: str, creator_name: str) -> bool:
    """Send a purchase confirmation to the buyer after payment is committed."""
    buyer = getattr(order, "buyer", None)
    email = getattr(buyer, "email", "")
    order_id = str(getattr(order, "id", ""))
    order_number = getattr(order, "order_number", order_id)
    amount = _money(getattr(order, "gross_amount", ""), getattr(order, "currency", "KES"))
    dashboard_url = f"{str(getattr(settings, 'BASE_URL', 'https://mybeathub.com')).rstrip('/')}/account"

    body = (
        f"Hi {getattr(buyer, 'username', '') or 'there'},\n\n"
        "Your BeatHub purchase was completed successfully.\n\n"
        f"Item: {item_name}\n"
        f"Creator: {creator_name}\n"
        f"Order: {order_number}\n"
        f"Amount: {amount}\n\n"
        "Your purchase and license are now recorded on your BeatHub account.\n"
        f"Account: {dashboard_url}\n\n"
        "Thank you for supporting independent creators on BeatHub.\n\n"
        "BeatHub Support"
    )
    return _deliver(email, "BeatHub — Your purchase is complete", body, f"buyer-purchase:{order_id}")


def notify_creator_sale(order, item_name: str, creator_name: str, creator_email: str) -> bool:
    """Notify the creator that a sale was successfully completed."""
    order_id = str(getattr(order, "id", ""))
    order_number = getattr(order, "order_number", order_id)
    gross = _money(getattr(order, "gross_amount", ""), getattr(order, "currency", "KES"))
    net = _money(getattr(order, "net_amount", ""), getattr(order, "currency", "KES"))
    dashboard_url = f"{str(getattr(settings, 'BASE_URL', 'https://mybeathub.com')).rstrip('/')}/dashboard"

    body = (
        f"Hi {creator_name or 'Creator'},\n\n"
        "Good news — you made a sale on BeatHub.\n\n"
        f"Item: {item_name}\n"
        f"Order: {order_number}\n"
        f"Customer amount: {gross}\n"
        f"Your earnings: {net}\n\n"
        "The payment has been verified and your creator earnings have been recorded.\n"
        f"Creator dashboard: {dashboard_url}\n\n"
        "Keep creating.\n\n"
        "BeatHub"
    )
    return _deliver(
        creator_email,
        "BeatHub — You made a sale",
        body,
        f"creator-sale:{order_id}:{creator_email.strip().lower()}",
    )


def notify_failed_payment(email: str, order_number: str, reason: str = "") -> bool:
    """Notify a buyer when a payment is verified as failed."""
    body = (
        "Hi,\n\n"
        "Your BeatHub payment could not be completed. No successful purchase was recorded.\n\n"
        f"Order: {order_number}\n"
        f"Reason: {reason or 'The payment was unsuccessful.'}\n\n"
        "You can try the purchase again from BeatHub. If you believe you were charged, please contact support with your order number.\n\n"
        "BeatHub Support"
    )
    return _deliver(email, "BeatHub — Payment not completed", body, f"payment-failed:{order_number}")
