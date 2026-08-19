"""
Safaricom M-Pesa Daraja API integration (STK Push / Lipa Na M-Pesa Online).

All credentials are read from environment configuration (app.config.settings).
Nothing here is a simulation: this makes real HTTPS calls to Safaricom's
sandbox or production endpoints depending on MPESA_ENVIRONMENT.
"""
import base64
import re
from datetime import datetime
from typing import Optional

import httpx

from app.config import settings


class MpesaError(Exception):
    pass


def _require_credentials():
    missing = [
        name for name, val in [
            ("MPESA_CONSUMER_KEY", settings.MPESA_CONSUMER_KEY),
            ("MPESA_CONSUMER_SECRET", settings.MPESA_CONSUMER_SECRET),
            ("MPESA_SHORTCODE", settings.MPESA_SHORTCODE),
            ("MPESA_PASSKEY", settings.MPESA_PASSKEY),
            ("MPESA_CALLBACK_URL", settings.MPESA_CALLBACK_URL),
        ] if not val
    ]
    if missing:
        raise MpesaError(
            "M-Pesa is not configured. Missing environment variables: " + ", ".join(missing)
        )


def normalize_phone_number(raw: str) -> str:
    """
    Normalizes Kenyan phone numbers to the 2547XXXXXXXX / 2541XXXXXXXX
    format required by Daraja. Raises MpesaError for anything that doesn't
    look like a valid Kenyan MSISDN.
    """
    digits = re.sub(r"\D", "", raw or "")

    if digits.startswith("254") and len(digits) == 12:
        normalized = digits
    elif digits.startswith("0") and len(digits) == 10:
        normalized = "254" + digits[1:]
    elif digits.startswith("7") and len(digits) == 9:
        normalized = "254" + digits
    elif digits.startswith("1") and len(digits) == 9:
        normalized = "254" + digits
    else:
        raise MpesaError("Invalid phone number. Use a format like 07XXXXXXXX or 2547XXXXXXXX.")

    if not re.match(r"^254(7|1)\d{8}$", normalized):
        raise MpesaError("Invalid Kenyan phone number.")
    return normalized


async def get_access_token() -> str:
    _require_credentials()
    auth = base64.b64encode(
        f"{settings.MPESA_CONSUMER_KEY}:{settings.MPESA_CONSUMER_SECRET}".encode()
    ).decode()

    url = f"{settings.mpesa_base_url}/oauth/v1/generate?grant_type=client_credentials"
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(url, headers={"Authorization": f"Basic {auth}"})

    if resp.status_code != 200:
        raise MpesaError(f"Failed to authenticate with M-Pesa: {resp.text}")

    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise MpesaError("M-Pesa did not return an access token.")
    return token


def _password_and_timestamp() -> tuple[str, str]:
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    raw = f"{settings.MPESA_SHORTCODE}{settings.MPESA_PASSKEY}{timestamp}"
    password = base64.b64encode(raw.encode()).decode()
    return password, timestamp


async def initiate_stk_push(
    phone_number: str,
    amount: int,
    account_reference: str,
    transaction_desc: str,
) -> dict:
    """
    Initiates a Lipa Na M-Pesa Online (STK Push) request.
    `amount` must be a whole-shilling integer (M-Pesa does not accept decimals).
    Returns the raw Daraja response including MerchantRequestID / CheckoutRequestID.
    Raises MpesaError on any failure — callers must NOT treat a raised error
    as a successful payment.
    """
    _require_credentials()
    phone = normalize_phone_number(phone_number)
    token = await get_access_token()
    password, timestamp = _password_and_timestamp()

    payload = {
        "BusinessShortCode": settings.MPESA_SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": int(amount),
        "PartyA": phone,
        "PartyB": settings.MPESA_SHORTCODE,
        "PhoneNumber": phone,
        "CallBackURL": settings.MPESA_CALLBACK_URL,
        "AccountReference": account_reference[:12],
        "TransactionDesc": transaction_desc[:100],
    }

    url = f"{settings.mpesa_base_url}/mpesa/stkpush/v1/processrequest"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload, headers={"Authorization": f"Bearer {token}"})

    data = resp.json()
    if resp.status_code != 200 or data.get("ResponseCode") not in ("0", 0):
        raise MpesaError(data.get("errorMessage") or data.get("ResponseDescription") or "STK Push failed.")

    return data


async def query_stk_status(checkout_request_id: str) -> dict:
    """Optional: actively poll Daraja for the status of a pending STK push."""
    _require_credentials()
    token = await get_access_token()
    password, timestamp = _password_and_timestamp()

    payload = {
        "BusinessShortCode": settings.MPESA_SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "CheckoutRequestID": checkout_request_id,
    }
    url = f"{settings.mpesa_base_url}/mpesa/stkpushquery/v1/query"
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(url, json=payload, headers={"Authorization": f"Bearer {token}"})
    return resp.json()
