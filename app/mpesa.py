import base64
from datetime import datetime
from typing import Optional

import httpx

from app.config import settings


def normalize_phone(phone: str) -> str:
    """
    Convert Kenyan phone numbers to 2547XXXXXXXX format.
    """

    phone = (
        phone.strip()
        .replace(" ", "")
        .replace("-", "")
    )

    if phone.startswith("+254"):
        phone = phone[1:]

    elif phone.startswith("07"):
        phone = "254" + phone[1:]

    elif phone.startswith("01"):
        phone = "254" + phone[1:]

    elif phone.startswith("7") or phone.startswith("1"):
        phone = "254" + phone

    return phone


def _base_url() -> str:
    environment = getattr(
        settings,
        "MPESA_ENVIRONMENT",
        "sandbox",
    )

    if str(environment).lower() == "live":
        return "https://api.safaricom.co.ke"

    return "https://sandbox.safaricom.co.ke"


async def get_access_token() -> str:
    consumer_key = getattr(
        settings,
        "MPESA_CONSUMER_KEY",
        None,
    )

    consumer_secret = getattr(
        settings,
        "MPESA_CONSUMER_SECRET",
        None,
    )

    if not consumer_key or not consumer_secret:
        raise RuntimeError(
            "M-Pesa Consumer Key and Consumer Secret are not configured."
        )

    credentials = (
        f"{consumer_key}:{consumer_secret}"
    )

    encoded = base64.b64encode(
        credentials.encode("utf-8")
    ).decode("utf-8")

    url = (
        f"{_base_url()}"
        "/oauth/v1/generate?grant_type=client_credentials"
    )

    headers = {
        "Authorization": f"Basic {encoded}",
    }

    async with httpx.AsyncClient(
        timeout=30
    ) as client:

        response = await client.get(
            url,
            headers=headers,
        )

    response.raise_for_status()

    data = response.json()

    token = data.get("access_token")

    if not token:
        raise RuntimeError(
            "M-Pesa access token was not returned."
        )

    return token


def _timestamp() -> str:
    return datetime.now().strftime(
        "%Y%m%d%H%M%S"
    )


def _password(timestamp: str) -> str:
    shortcode = getattr(
        settings,
        "MPESA_SHORTCODE",
        None,
    )

    passkey = getattr(
        settings,
        "MPESA_PASSKEY",
        None,
    )

    if not shortcode or not passkey:
        raise RuntimeError(
            "MPESA_SHORTCODE and MPESA_PASSKEY are required."
        )

    raw = (
        f"{shortcode}"
        f"{passkey}"
        f"{timestamp}"
    )

    return base64.b64encode(
        raw.encode("utf-8")
    ).decode("utf-8")


async def stk_push(
    phone_number: str,
    amount: float,
    account_reference: str,
    transaction_desc: str,
) -> dict:

    token = await get_access_token()

    timestamp = _timestamp()

    shortcode = getattr(
        settings,
        "MPESA_SHORTCODE",
        None,
    )

    callback_url = getattr(
        settings,
        "MPESA_STK_CALLBACK_URL",
        None,
    ) or getattr(
        settings,
        "MPESA_CALLBACK_URL",
        None,
    )

    if not callback_url:
        raise RuntimeError(
            "M-Pesa STK callback URL is not configured."
        )

    phone = normalize_phone(
        phone_number
    )

    payload = {
        "BusinessShortCode": shortcode,
        "Password": _password(timestamp),
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": int(
            round(float(amount))
        ),
        "PartyA": phone,
        "PartyB": shortcode,
        "PhoneNumber": phone,
        "CallBackURL": callback_url,
        "AccountReference": account_reference,
        "TransactionDesc": transaction_desc,
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    url = (
        f"{_base_url()}"
        "/mpesa/stkpush/v1/processrequest"
    )

    async with httpx.AsyncClient(
        timeout=45
    ) as client:

        response = await client.post(
            url,
            json=payload,
            headers=headers,
        )

    response.raise_for_status()

    return response.json()


async def b2c_payment(
    phone_number: str,
    amount: float,
    remarks: str,
    occasion: str = "BeatHub payout",
) -> dict:
    """
    B2C payout.

    This is the endpoint that can eventually be used to
    automatically send approved creator/admin withdrawals
    to M-Pesa.

    Safaricom B2C credentials must be configured before
    enabling automatic payouts.
    """

    token = await get_access_token()

    shortcode = getattr(
        settings,
        "MPESA_B2C_SHORTCODE",
        None,
    )

    initiator_name = getattr(
        settings,
        "MPESA_B2C_INITIATOR_NAME",
        None,
    )

    security_credential = getattr(
        settings,
        "MPESA_B2C_SECURITY_CREDENTIAL",
        None,
    )

    result_url = getattr(
        settings,
        "MPESA_B2C_RESULT_URL",
        None,
    )

    timeout_url = getattr(
        settings,
        "MPESA_B2C_TIMEOUT_URL",
        None,
    )

    if not shortcode:
        raise RuntimeError(
            "MPESA_B2C_SHORTCODE is not configured."
        )

    if not initiator_name:
        raise RuntimeError(
            "MPESA_B2C_INITIATOR_NAME is not configured."
        )

    if not security_credential:
        raise RuntimeError(
            "MPESA_B2C_SECURITY_CREDENTIAL is not configured."
        )

    if not result_url:
        raise RuntimeError(
            "MPESA_B2C_RESULT_URL is not configured."
        )

    if not timeout_url:
        raise RuntimeError(
            "MPESA_B2C_TIMEOUT_URL is not configured."
        )

    phone = normalize_phone(
        phone_number
    )

    payload = {
        "InitiatorName": initiator_name,
        "SecurityCredential": security_credential,
        "CommandID": "BusinessPayment",
        "Amount": int(
            round(float(amount))
        ),
        "PartyA": shortcode,
        "PartyB": phone,
        "Remarks": remarks[:100],
        "QueueTimeOutURL": timeout_url,
        "ResultURL": result_url,
        "Occasion": occasion[:100],
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    url = (
        f"{_base_url()}"
        "/mpesa/b2c/v3/paymentrequest"
    )

    async with httpx.AsyncClient(
        timeout=45
    ) as client:

        response = await client.post(
            url,
            json=payload,
            headers=headers,
        )

    response.raise_for_status()

    return response.json()
