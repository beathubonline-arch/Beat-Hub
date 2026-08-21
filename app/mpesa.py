import base64
from datetime import datetime
from typing import Any

import httpx

from app.config import settings


def _get_setting(name: str, default=None):
    return getattr(
        settings,
        name,
        default,
    )


def _environment() -> str:
    value = (
        _get_setting(
            "MPESA_ENVIRONMENT",
            "sandbox",
        )
        or "sandbox"
    )

    return str(value).strip().lower()


def _base_url() -> str:
    if _environment() == "live":
        return "https://api.safaricom.co.ke"

    return "https://sandbox.safaricom.co.ke"


def normalize_phone(phone: str) -> str:
    """
    Converts common Kenyan phone formats into 2547XXXXXXXX.
    """

    value = (
        phone or ""
    ).strip().replace(
        " ",
        "",
    ).replace(
        "-",
        "",
    )

    if value.startswith("+"):
        value = value[1:]

    if value.startswith("07") or value.startswith("01"):
        value = "254" + value[1:]

    elif value.startswith("7") or value.startswith("1"):
        value = "254" + value

    if not value.startswith("254"):
        raise ValueError(
            "Invalid Kenyan M-Pesa phone number."
        )

    if len(value) != 12:
        raise ValueError(
            "Invalid Kenyan M-Pesa phone number."
        )

    return value


async def get_access_token() -> str:
    consumer_key = _get_setting(
        "MPESA_CONSUMER_KEY"
    )

    consumer_secret = _get_setting(
        "MPESA_CONSUMER_SECRET"
    )

    if not consumer_key:
        raise RuntimeError(
            "MPESA_CONSUMER_KEY is not configured."
        )

    if not consumer_secret:
        raise RuntimeError(
            "MPESA_CONSUMER_SECRET is not configured."
        )

    credentials = (
        f"{consumer_key}:{consumer_secret}"
    )

    encoded = base64.b64encode(
        credentials.encode("utf-8")
    ).decode("utf-8")

    url = (
        f"{_base_url()}"
        "/oauth/v1/generate"
        "?grant_type=client_credentials"
    )

    async with httpx.AsyncClient(
        timeout=30.0
    ) as client:
        response = await client.get(
            url,
            headers={
                "Authorization":
                    f"Basic {encoded}",
            },
        )

    response.raise_for_status()

    data = response.json()

    token = data.get(
        "access_token"
    )

    if not token:
        raise RuntimeError(
            "M-Pesa access token was not returned."
        )

    return token


async def b2c_payment(
    phone_number: str,
    amount,
    remarks: str = "BeatHub Admin Withdrawal",
    occasion: str = "BeatHub Platform Withdrawal",
) -> dict[str, Any]:
    """
    Initiates a Safaricom B2C payment.

    IMPORTANT:
    This requires B2C credentials and a correctly configured
    Safaricom/Daraja production or sandbox account.

    It returns Safaricom's response. The caller should only mark
    the withdrawal as PAID after the appropriate B2C result/callback
    confirms successful payment.
    """

    phone = normalize_phone(
        phone_number
    )

    token = await get_access_token()

    initiator_name = _get_setting(
        "MPESA_B2C_INITIATOR_NAME"
    )

    security_credential = _get_setting(
        "MPESA_B2C_SECURITY_CREDENTIAL"
    )

    shortcode = _get_setting(
        "MPESA_B2C_SHORTCODE"
    )

    result_url = (
        _get_setting(
            "MPESA_B2C_RESULT_URL"
        )
        or _get_setting(
            "MPESA_CALLBACK_URL"
        )
    )

    timeout_url = _get_setting(
        "MPESA_B2C_TIMEOUT_URL"
    ) or result_url

    if not initiator_name:
        raise RuntimeError(
            "MPESA_B2C_INITIATOR_NAME is not configured."
        )

    if not security_credential:
        raise RuntimeError(
            "MPESA_B2C_SECURITY_CREDENTIAL is not configured."
        )

    if not shortcode:
        raise RuntimeError(
            "MPESA_B2C_SHORTCODE is not configured."
        )

    if not result_url:
        raise RuntimeError(
            "MPESA_B2C_RESULT_URL is not configured."
        )

    payload = {
        "InitiatorName": initiator_name,
        "SecurityCredential": security_credential,
        "CommandID": "BusinessPayment",
        "Amount": int(amount),
        "PartyA": shortcode,
        "PartyB": phone,
        "Remarks": remarks[:100],
        "QueueTimeOutURL": timeout_url,
        "ResultURL": result_url,
        "Occasion": occasion[:100],
    }

    url = (
        f"{_base_url()}"
        "/mpesa/b2c/v1/paymentrequest"
    )

    async with httpx.AsyncClient(
        timeout=30.0
    ) as client:
        response = await client.post(
            url,
            json=payload,
            headers={
                "Authorization":
                    f"Bearer {token}",
                "Content-Type":
                    "application/json",
            },
        )

    response.raise_for_status()

    return response.json()


async def admin_b2c_payment(
    withdrawal_id: str,
    phone_number: str,
    amount,
) -> dict[str, Any]:
    """
    Convenience wrapper for BeatHub admin withdrawals.

    The withdrawal ID is included in the occasion field so the
    transaction can be identified in your own records.
    """

    return await b2c_payment(
        phone_number=phone_number,
        amount=amount,
        remarks=(
            "BeatHub Admin Withdrawal "
            f"{withdrawal_id}"
        ),
        occasion=(
            f"Admin withdrawal {withdrawal_id}"
        ),
    )
