"""Legacy import compatibility for the merchandise router.

Daraja/Safaricom is no longer used by BeatHub.  The historical merchandise
router still imports this module, so this small adapter keeps that legacy
router import-safe while routing its STK-style call into the single Paystack
customer gateway.  No Safaricom/Daraja API is called here.
"""

from __future__ import annotations

import re
from decimal import Decimal

import httpx

from app.config import settings


_PHONE_RE = re.compile(r"^\+?\d{9,15}$")


def normalize_phone(phone: str) -> str:
    value = str(phone or "").strip().replace(" ", "").replace("-", "")
    if value.startswith("0") and len(value) == 10:
        value = "+254" + value[1:]
    elif value.startswith("254") and len(value) == 12:
        value = "+" + value
    elif value.startswith("7") and len(value) == 9:
        value = "+254" + value

    if not _PHONE_RE.fullmatch(value):
        raise ValueError("Enter a valid phone number.")
    return value


def stk_push(
    phone: str,
    amount: int | Decimal,
    account_reference: str,
    description: str,
    callback_url: str | None = None,
) -> dict:
    """Initialize a Paystack checkout while preserving the old call shape."""
    if not settings.PAYSTACK_SECRET_KEY:
        raise RuntimeError("PAYSTACK_SECRET_KEY is not configured.")

    customer_phone = normalize_phone(phone)
    reference = str(account_reference or "").strip() or "BH" + customer_phone[-9:]
    amount_kobo = int((Decimal(str(amount)) * Decimal("100")).quantize(Decimal("1")))

    payload = {
        "email": "paystack@beathub.local",
        "amount": amount_kobo,
        "currency": "KES",
        "reference": reference,
        "callback_url": callback_url or f"{settings.BASE_URL.rstrip('/')}/paystack/callback",
        "metadata": {
            "beathub_legacy_phone": customer_phone,
            "beathub_checkout_description": str(description)[:100],
        },
    }

    response = httpx.post(
        f"{settings.PAYSTACK_BASE_URL}/transaction/initialize",
        headers={
            "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()

    if not data.get("status") or not isinstance(data.get("data"), dict):
        raise RuntimeError(data.get("message") or "Paystack checkout initialization failed.")

    checkout = data["data"]
    return {
        "checkout_request_id": checkout.get("reference") or reference,
        "merchant_request_id": checkout.get("authorization_url"),
        "authorization_url": checkout.get("authorization_url"),
        "customer_message": "Continue to Paystack to complete payment.",
        "simulated": False,
    }
