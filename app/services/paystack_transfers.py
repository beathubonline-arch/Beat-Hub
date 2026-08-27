"""Paystack transfer helpers for BeatHub outbound KES M-Pesa payouts."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

import httpx

from app.config import settings


class PaystackTransferError(RuntimeError):
    """Raised when Paystack cannot create or verify a transfer."""


def _headers() -> dict[str, str]:
    secret = (settings.PAYSTACK_SECRET_KEY or "").strip()
    if not secret:
        raise PaystackTransferError("PAYSTACK_SECRET_KEY is not configured.")
    return {
        "Authorization": f"Bearer {secret}",
        "Content-Type": "application/json",
    }


def _base_url() -> str:
    return (settings.PAYSTACK_BASE_URL or "https://api.paystack.co").rstrip("/")


def normalize_kenyan_phone(phone: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if digits.startswith("254") and len(digits) == 12:
        return digits
    if digits.startswith("0") and len(digits) == 10:
        return "254" + digits[1:]
    if digits.startswith("7") and len(digits) == 9:
        return "254" + digits
    raise PaystackTransferError("Enter a valid Kenyan M-Pesa number.")


def _kes_subunits(amount: Decimal) -> int:
    value = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if value <= 0:
        raise PaystackTransferError("Transfer amount must be greater than zero.")
    return int(value * 100)


def create_mpesa_recipient(phone_number: str, name: str = "BeatHub Admin") -> str:
    phone = normalize_kenyan_phone(phone_number)
    payload = {
        "type": "mobile_money",
        "name": name[:100] or "BeatHub Admin",
        "account_number": phone,
        "bank_code": "MPESA",
        "currency": "KES",
    }
    try:
        response = httpx.post(
            f"{_base_url()}/transferrecipient",
            headers=_headers(),
            json=payload,
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise PaystackTransferError(f"Paystack recipient request failed: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise PaystackTransferError("Paystack returned an invalid recipient response.") from exc

    if response.status_code >= 400 or not data.get("status"):
        message = data.get("message") or "Paystack rejected the M-Pesa recipient."
        raise PaystackTransferError(str(message))

    recipient_code = (data.get("data") or {}).get("recipient_code")
    if not recipient_code:
        raise PaystackTransferError("Paystack did not return a transfer recipient code.")
    return str(recipient_code)


def initiate_mpesa_transfer(
    amount: Decimal,
    phone_number: str,
    *,
    reason: str = "BeatHub platform withdrawal",
    name: str = "BeatHub Admin",
) -> dict:
    """Create a real KES M-Pesa transfer from the Paystack balance."""
    recipient = create_mpesa_recipient(phone_number, name=name)
    reference = f"bh_admin_{uuid4().hex}"
    payload = {
        "source": "balance",
        "amount": _kes_subunits(amount),
        "recipient": recipient,
        "reference": reference,
        "reason": reason[:100],
        "currency": "KES",
    }
    try:
        response = httpx.post(
            f"{_base_url()}/transfer",
            headers=_headers(),
            json=payload,
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise PaystackTransferError(f"Paystack transfer request failed: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise PaystackTransferError("Paystack returned an invalid transfer response.") from exc

    if response.status_code >= 400 or not data.get("status"):
        message = data.get("message") or "Paystack rejected the transfer."
        raise PaystackTransferError(str(message))

    transfer = data.get("data") or {}
    return {
        "reference": str(transfer.get("reference") or reference),
        "transfer_code": transfer.get("transfer_code"),
        "status": str(transfer.get("status") or "pending").lower(),
        "recipient_code": recipient,
        "message": str(data.get("message") or "Transfer queued."),
    }


def verify_transfer(reference: str) -> dict:
    reference = (reference or "").strip()
    if not reference:
        raise PaystackTransferError("Transfer reference is required.")
    try:
        response = httpx.get(
            f"{_base_url()}/transfer/verify/{reference}",
            headers=_headers(),
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise PaystackTransferError(f"Paystack verification request failed: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise PaystackTransferError("Paystack returned an invalid verification response.") from exc

    if response.status_code >= 400 or not data.get("status"):
        message = data.get("message") or "Paystack could not verify the transfer."
        raise PaystackTransferError(str(message))

    transfer = data.get("data") or {}
    return {
        "reference": str(transfer.get("reference") or reference),
        "status": str(transfer.get("status") or "").lower(),
        "transfer_code": transfer.get("transfer_code"),
        "failures": transfer.get("failures"),
    }
