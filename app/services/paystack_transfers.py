"""Paystack Transfers for BeatHub Kenya.

This module sends BeatHub's own platform withdrawals to an individual
Kenyan M-Pesa wallet through Paystack Transfers.

Important: Paystack returns a queued/pending transfer in live mode. The
withdrawal is therefore only marked paid after Paystack sends transfer.success.
"""

import re
import uuid
from decimal import Decimal, ROUND_HALF_UP

import httpx

from app.config import settings


PHONE_RE = re.compile(r"^2547\d{8}$")


class PaystackTransferError(RuntimeError):
    """Raised when Paystack cannot create an M-Pesa transfer."""


def normalize_ke_phone(phone: str) -> str:
    """Normalize a Kenyan mobile number to 2547XXXXXXXX."""
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())

    if digits.startswith("254"):
        normalized = digits
    elif digits.startswith("0") and len(digits) == 10:
        normalized = "254" + digits[1:]
    elif digits.startswith("7") and len(digits) == 9:
        normalized = "254" + digits
    else:
        raise PaystackTransferError("Enter a valid Kenyan M-Pesa number.")

    if not PHONE_RE.fullmatch(normalized):
        raise PaystackTransferError("Only Safaricom 07XXXXXXXX M-Pesa numbers are supported.")

    return normalized


def amount_to_kobo(amount: Decimal) -> int:
    """Convert KES to Paystack's smallest currency unit."""
    value = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if value <= 0:
        raise PaystackTransferError("Transfer amount must be greater than zero.")
    return int((value * Decimal("100")).quantize(Decimal("1")))


def _headers() -> dict[str, str]:
    if not settings.PAYSTACK_SECRET_KEY:
        raise PaystackTransferError("PAYSTACK_SECRET_KEY is not configured.")
    return {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


async def create_mpesa_transfer(
    *,
    amount: Decimal,
    phone_number: str,
    name: str = "BeatHub Admin",
    reference: str | None = None,
) -> dict:
    """Create a Paystack M-Pesa customer transfer.

    The recipient is created with Paystack's Kenya M-Pesa mobile-money
    recipient type and MPESA bank code, then the transfer is queued from the
    merchant Paystack balance.
    """
    phone = normalize_ke_phone(phone_number)
    amount_kobo = amount_to_kobo(amount)
    transfer_reference = reference or f"bh_admin_{uuid.uuid4().hex}"

    if not 16 <= len(transfer_reference) <= 50:
        raise PaystackTransferError("Generated transfer reference has an invalid length.")

    base = settings.PAYSTACK_BASE_URL.rstrip("/")
    headers = _headers()
    timeout = httpx.Timeout(20.0, connect=5.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            recipient_response = await client.post(
                f"{base}/transferrecipient",
                headers=headers,
                json={
                    "type": "mobile_money",
                    "name": name[:100] or "BeatHub Admin",
                    "account_number": phone,
                    "bank_code": "MPESA",
                    "currency": "KES",
                    "description": "BeatHub admin M-Pesa payout",
                },
            )

            recipient_payload = recipient_response.json()
            if recipient_response.status_code >= 400 or not recipient_payload.get("status"):
                message = recipient_payload.get("message") or "Paystack could not create the M-Pesa recipient."
                raise PaystackTransferError(message)

            recipient_data = recipient_payload.get("data") or {}
            recipient_code = recipient_data.get("recipient_code")
            if not recipient_code:
                raise PaystackTransferError("Paystack did not return an M-Pesa recipient code.")

            transfer_response = await client.post(
                f"{base}/transfer",
                headers=headers,
                json={
                    "source": "balance",
                    "amount": amount_kobo,
                    "recipient": recipient_code,
                    "reference": transfer_reference,
                    "reason": "BeatHub platform earnings withdrawal",
                    "currency": "KES",
                },
            )

            transfer_payload = transfer_response.json()
            if transfer_response.status_code >= 400 or not transfer_payload.get("status"):
                message = transfer_payload.get("message") or "Paystack could not initiate the M-Pesa transfer."
                raise PaystackTransferError(message)

            transfer_data = transfer_payload.get("data") or {}
            return {
                "reference": transfer_data.get("reference") or transfer_reference,
                "transfer_code": transfer_data.get("transfer_code"),
                "status": str(transfer_data.get("status") or "pending").lower(),
                "recipient_code": recipient_code,
                "phone_number": phone,
                "amount": str(Decimal(str(amount)).quantize(Decimal("0.01"))),
                "currency": "KES",
            }

    except PaystackTransferError:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise PaystackTransferError(f"Paystack transfer request failed: {exc}") from exc
