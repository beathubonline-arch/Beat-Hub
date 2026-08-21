"""
BeatHub M-Pesa / Safaricom Daraja integration.

Supports:
    1. Lipa Na M-Pesa Online (STK Push)
    2. B2C payments (admin/platform payouts)

Credentials are read from app.config.settings.

Expected settings:
    MPESA_ENVIRONMENT
    MPESA_CONSUMER_KEY
    MPESA_CONSUMER_SECRET
    MPESA_SHORTCODE
    MPESA_PASSKEY
    MPESA_STK_CALLBACK_URL

Optional B2C settings:
    MPESA_B2C_SHORTCODE
    MPESA_B2C_INITIATOR_NAME
    MPESA_B2C_SECURITY_CREDENTIAL
    MPESA_B2C_QUEUE_TIMEOUT_URL
    MPESA_B2C_RESULT_URL
"""

import base64
import logging
from datetime import datetime
from typing import Any, Dict, Optional

import httpx

from app.config import settings

logger = logging.getLogger("beathub.mpesa")


# ----------------------------------------------------------------------
# CONFIGURATION
# ----------------------------------------------------------------------

REQUEST_TIMEOUT = 30.0


def _environment() -> str:
    value = getattr(settings, "MPESA_ENVIRONMENT", "sandbox")
    return str(value or "sandbox").strip().lower()


def _base_url() -> str:
    """
    Safaricom Daraja API base URL.
    """
    if _environment() == "live":
        return "https://api.safaricom.co.ke"

    return "https://sandbox.safaricom.co.ke"


def _consumer_key() -> str:
    return str(
        getattr(settings, "MPESA_CONSUMER_KEY", "") or ""
    ).strip()


def _consumer_secret() -> str:
    return str(
        getattr(settings, "MPESA_CONSUMER_SECRET", "") or ""
    ).strip()


def _shortcode() -> str:
    return str(
        getattr(settings, "MPESA_SHORTCODE", "") or ""
    ).strip()


def _passkey() -> str:
    return str(
        getattr(settings, "MPESA_PASSKEY", "") or ""
    ).strip()


def _stk_callback_url() -> str:
    value = getattr(
        settings,
        "MPESA_STK_CALLBACK_URL",
        None,
    )

    if not value:
        value = getattr(
            settings,
            "MPESA_CALLBACK_URL",
            "",
        )

    return str(value or "").strip()


def _b2c_shortcode() -> str:
    value = getattr(
        settings,
        "MPESA_B2C_SHORTCODE",
        None,
    )

    if not value:
        value = _shortcode()

    return str(value or "").strip()


def _b2c_initiator_name() -> str:
    return str(
        getattr(
            settings,
            "MPESA_B2C_INITIATOR_NAME",
            "",
        )
        or ""
    ).strip()


def _b2c_security_credential() -> str:
    return str(
        getattr(
            settings,
            "MPESA_B2C_SECURITY_CREDENTIAL",
            "",
        )
        or ""
    ).strip()


def _b2c_queue_timeout_url() -> str:
    return str(
        getattr(
            settings,
            "MPESA_B2C_QUEUE_TIMEOUT_URL",
            "",
        )
        or ""
    ).strip()


def _b2c_result_url() -> str:
    return str(
        getattr(
            settings,
            "MPESA_B2C_RESULT_URL",
            "",
        )
        or ""
    ).strip()


# ----------------------------------------------------------------------
# PHONE NUMBER
# ----------------------------------------------------------------------

def normalize_phone(phone: str) -> str:
    """
    Normalize Kenyan phone numbers to 254XXXXXXXXX.

    Accepted examples:

        0712345678
        0112345678
        712345678
        +254712345678
        254712345678
    """

    value = str(phone or "").strip()

    # Remove spaces, hyphens and brackets.
    value = (
        value.replace(" ", "")
        .replace("-", "")
        .replace("(", "")
        .replace(")", "")
    )

    if value.startswith("+"):
        value = value[1:]

    if value.startswith("00"):
        value = value[2:]

    if value.startswith("254"):
        number = value

    elif value.startswith("0"):
        number = "254" + value[1:]

    elif value.startswith("7") or value.startswith("1"):
        number = "254" + value

    else:
        raise ValueError(
            "Invalid Kenyan phone number."
        )

    if len(number) != 12:
        raise ValueError(
            "Invalid Kenyan phone number."
        )

    if not number.startswith("254"):
        raise ValueError(
            "Invalid Kenyan phone number."
        )

    return number


# ----------------------------------------------------------------------
# ACCESS TOKEN
# ----------------------------------------------------------------------

async def get_access_token() -> str:
    """
    Get OAuth access token from Daraja.
    """

    consumer_key = _consumer_key()
    consumer_secret = _consumer_secret()

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

    encoded_credentials = base64.b64encode(
        credentials.encode("utf-8")
    ).decode("utf-8")

    url = (
        f"{_base_url()}"
        "/oauth/v1/generate"
        "?grant_type=client_credentials"
    )

    headers = {
        "Authorization":
            f"Basic {encoded_credentials}",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT
    ) as client:

        response = await client.get(
            url,
            headers=headers,
        )

    if response.status_code != 200:
        logger.error(
            "M-Pesa OAuth failed: %s %s",
            response.status_code,
            response.text,
        )

        raise RuntimeError(
            "Unable to authenticate with M-Pesa."
        )

    try:
        data = response.json()
    except Exception as exc:
        logger.error(
            "Invalid M-Pesa OAuth response: %s",
            exc,
        )
        raise RuntimeError(
            "Invalid response from M-Pesa."
        )

    token = data.get("access_token")

    if not token:
        logger.error(
            "M-Pesa OAuth response contained no access token: %s",
            data,
        )

        raise RuntimeError(
            "M-Pesa access token was not returned."
        )

    return token


# ----------------------------------------------------------------------
# STK PASSWORD
# ----------------------------------------------------------------------

def create_stk_password(
    timestamp: Optional[str] = None,
) -> tuple[str, str]:
    """
    Create the password required for STK Push.

    Returns:
        password, timestamp
    """

    shortcode = _shortcode()
    passkey = _passkey()

    if not shortcode:
        raise RuntimeError(
            "MPESA_SHORTCODE is not configured."
        )

    if not passkey:
        raise RuntimeError(
            "MPESA_PASSKEY is not configured."
        )

    if timestamp is None:
        timestamp = datetime.utcnow().strftime(
            "%Y%m%d%H%M%S"
        )

    raw = (
        shortcode
        + passkey
        + timestamp
    )

    password = base64.b64encode(
        raw.encode("utf-8")
    ).decode("utf-8")

    return password, timestamp


# ----------------------------------------------------------------------
# STK PUSH
# ----------------------------------------------------------------------

async def stk_push(
    phone_number: str,
    amount: float,
    account_reference: str,
    transaction_desc: str,
    callback_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Initiate a Lipa Na M-Pesa Online STK Push.

    Returns Safaricom's response dictionary.
    """

    shortcode = _shortcode()

    if not shortcode:
        raise RuntimeError(
            "MPESA_SHORTCODE is not configured."
        )

    callback = (
        callback_url
        or _stk_callback_url()
    )

    if not callback:
        raise RuntimeError(
            "MPESA_STK_CALLBACK_URL is not configured."
        )

    try:
        phone = normalize_phone(
            phone_number
        )
    except ValueError as exc:
        raise RuntimeError(
            str(exc)
        ) from exc

    try:
        amount_value = int(
            float(amount)
        )
    except Exception as exc:
        raise RuntimeError(
            "Invalid M-Pesa amount."
        ) from exc

    if amount_value <= 0:
        raise RuntimeError(
            "M-Pesa amount must be greater than zero."
        )

    access_token = await get_access_token()

    password, timestamp = (
        create_stk_password()
    )

    payload = {
        "BusinessShortCode": shortcode,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType":
            "CustomerPayBillOnline",
        "Amount": amount_value,
        "PartyA": phone,
        "PartyB": shortcode,
        "PhoneNumber": phone,
        "CallBackURL": callback,
        "AccountReference":
            str(account_reference)[:12],
        "TransactionDesc":
            str(transaction_desc)[:13],
    }

    url = (
        f"{_base_url()}"
        "/mpesa/stkpush/v1/processrequest"
    )

    headers = {
        "Authorization":
            f"Bearer {access_token}",
        "Content-Type":
            "application/json",
        "Accept":
            "application/json",
    }

    logger.info(
        "Initiating M-Pesa STK Push for %s",
        phone,
    )

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT
    ) as client:

        response = await client.post(
            url,
            json=payload,
            headers=headers,
        )

    if response.status_code != 200:
        logger.error(
            "STK Push failed: %s %s",
            response.status_code,
            response.text,
        )

        raise RuntimeError(
            "M-Pesa STK Push request failed."
        )

    try:
        data = response.json()
    except Exception as exc:
        logger.error(
            "Invalid STK response: %s",
            exc,
        )

        raise RuntimeError(
            "Invalid response from M-Pesa STK Push."
        ) from exc

    response_code = str(
        data.get("ResponseCode", "")
    )

    if response_code not in {
        "",
        "0",
    }:
        logger.error(
            "STK Push rejected by M-Pesa: %s",
            data,
        )

        raise RuntimeError(
            data.get(
                "errorMessage",
                data.get(
                    "ResponseDescription",
                    "M-Pesa rejected the STK request.",
                ),
            )
        )

    return data


# ----------------------------------------------------------------------
# B2C VALIDATION
# ----------------------------------------------------------------------

def validate_b2c_configuration() -> None:
    """
    Validate configuration required for B2C payouts.
    """

    if not _b2c_shortcode():
        raise RuntimeError(
            "MPESA_B2C_SHORTCODE is not configured."
        )

    if not _b2c_initiator_name():
        raise RuntimeError(
            "MPESA_B2C_INITIATOR_NAME is not configured."
        )

    if not _b2c_security_credential():
        raise RuntimeError(
            "MPESA_B2C_SECURITY_CREDENTIAL is not configured."
        )

    if not _b2c_queue_timeout_url():
        raise RuntimeError(
            "MPESA_B2C_QUEUE_TIMEOUT_URL is not configured."
        )

    if not _b2c_result_url():
        raise RuntimeError(
            "MPESA_B2C_RESULT_URL is not configured."
        )


# ----------------------------------------------------------------------
# B2C PAYOUT
# ----------------------------------------------------------------------

async def b2c_payment(
    phone_number: str,
    amount: float,
    remarks: str = "BeatHub platform payout",
    occasion: str = "BeatHub",
    command_id: str = "BusinessPayment",
) -> Dict[str, Any]:
    """
    Send money from the BeatHub M-Pesa shortcode
    to a Kenyan phone number using B2C.

    This is intended for:
        - BeatHub platform commission withdrawals
        - Other legitimate platform payouts

    It is NOT the same as a creator withdrawal request.
    """

    validate_b2c_configuration()

    try:
        phone = normalize_phone(
            phone_number
        )
    except ValueError as exc:
        raise RuntimeError(
            str(exc)
        ) from exc

    try:
        amount_value = int(
            float(amount)
        )
    except Exception as exc:
        raise RuntimeError(
            "Invalid B2C amount."
        ) from exc

    if amount_value <= 0:
        raise RuntimeError(
            "B2C amount must be greater than zero."
        )

    allowed_commands = {
        "SalaryPayment",
        "BusinessPayment",
        "PromotionPayment",
    }

    if command_id not in allowed_commands:
        raise RuntimeError(
            "Invalid B2C command ID."
        )

    access_token = await get_access_token()

    payload = {
        "InitiatorName":
            _b2c_initiator_name(),

        "SecurityCredential":
            _b2c_security_credential(),

        "CommandID":
            command_id,

        "Amount":
            amount_value,

        "PartyA":
            _b2c_shortcode(),

        "PartyB":
            phone,

        "Remarks":
            str(remarks)[:100],

        "QueueTimeOutURL":
            _b2c_queue_timeout_url(),

        "ResultURL":
            _b2c_result_url(),

        "Occasion":
            str(occasion)[:100],
    }

    url = (
        f"{_base_url()}"
        "/mpesa/b2c/v3/paymentrequest"
    )

    headers = {
        "Authorization":
            f"Bearer {access_token}",
        "Content-Type":
            "application/json",
        "Accept":
            "application/json",
    }

    logger.info(
        "Initiating B2C payout of KES %s to %s",
        amount_value,
        phone,
    )

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT
    ) as client:

        response = await client.post(
            url,
            json=payload,
            headers=headers,
        )

    if response.status_code != 200:
        logger.error(
            "B2C payout failed: %s %s",
            response.status_code,
            response.text,
        )

        raise RuntimeError(
            "M-Pesa B2C payout request failed."
        )

    try:
        data = response.json()
    except Exception as exc:
        logger.error(
            "Invalid B2C response: %s",
            exc,
        )

        raise RuntimeError(
            "Invalid response from M-Pesa B2C."
        ) from exc

    response_code = str(
        data.get("ResponseCode", "")
    )

    if response_code not in {
        "",
        "0",
    }:
        logger.error(
            "B2C payout rejected by M-Pesa: %s",
            data,
        )

        raise RuntimeError(
            data.get(
                "errorMessage",
                data.get(
                    "ResponseDescription",
                    "M-Pesa rejected the B2C request.",
                ),
            )
        )

    return data


# ----------------------------------------------------------------------
# SIMPLE ALIASES
# ----------------------------------------------------------------------

async def send_b2c(
    phone_number: str,
    amount: float,
    remarks: str = "BeatHub platform payout",
    occasion: str = "BeatHub",
) -> Dict[str, Any]:
    """
    Friendly alias for b2c_payment().
    """

    return await b2c_payment(
        phone_number=phone_number,
        amount=amount,
        remarks=remarks,
        occasion=occasion,
    )


# ----------------------------------------------------------------------
# RESPONSE HELPERS
# ----------------------------------------------------------------------

def get_checkout_request_id(
    response: Dict[str, Any],
) -> Optional[str]:
    """
    Extract CheckoutRequestID from an STK response.
    """

    value = response.get(
        "CheckoutRequestID"
    )

    if value:
        return str(value)

    return None


def get_merchant_request_id(
    response: Dict[str, Any],
) -> Optional[str]:
    """
    Extract MerchantRequestID from an STK response.
    """

    value = response.get(
        "MerchantRequestID"
    )

    if value:
        return str(value)

    return None


def get_b2c_conversation_id(
    response: Dict[str, Any],
) -> Optional[str]:
    """
    Extract ConversationID from a B2C response.
    """

    value = response.get(
        "ConversationID"
    )

    if value:
        return str(value)

    return None


def get_b2c_originator_conversation_id(
    response: Dict[str, Any],
) -> Optional[str]:
    """
    Extract OriginatorConversationID from
    a B2C response.
    """

    value = response.get(
        "OriginatorConversationID"
    )

    if value:
        return str(value)

    return None
