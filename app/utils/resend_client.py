"""Small, defensive HTTP client helpers for Resend."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger("beathub.resend")
RESEND_API_URL = "https://api.resend.com/emails"
RESEND_USER_AGENT = "BeatHub/1.0 (+https://mybeathub.com)"


def send_email(
    api_key: str,
    sender: str,
    to_email: str,
    subject: str,
    body: str,
    *,
    reply_to: str | None = None,
    idempotency_key: str | None = None,
) -> bool:
    """Send one transactional email through Resend safely.

    A stable idempotency key can be supplied for a business event. Delivery
    failures return False and never escape into the business transaction.
    """
    payload: dict[str, Any] = {
        "from": sender,
        "to": [to_email],
        "subject": subject,
        "text": body,
    }
    if reply_to:
        payload["reply_to"] = reply_to

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": RESEND_USER_AGENT,
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key[:64]

    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(RESEND_API_URL, json=payload, headers=headers)
        if 200 <= response.status_code < 300:
            logger.info("Email sent successfully through Resend.")
            return True
        try:
            detail = response.json()
            if isinstance(detail, dict):
                detail = {k: detail[k] for k in ("name", "message", "statusCode") if k in detail}
            else:
                detail = str(detail)[:500]
        except Exception:
            detail = response.text[:500]
        logger.error("Resend email delivery failed with HTTP %s: %s", response.status_code, detail)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        logger.error("Resend network/timeout error: %s", type(exc).__name__)
    except httpx.HTTPError as exc:
        logger.error("Resend HTTP error: %s", type(exc).__name__)
    except Exception as exc:
        logger.error("Unexpected Resend delivery error: %s", type(exc).__name__)
    return False
