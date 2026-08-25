"""
Server-authoritative pricing and commission calculations.
Never trust a price or split coming from the client/browser.

BeatHub's marketplace commission is a product rule, not a customer-controlled
setting: every producer sale is split 90% producer / 10% BeatHub.
"""
from decimal import ROUND_HALF_UP, Decimal

from app.config import settings

BEATHUB_COMMISSION_PERCENT = Decimal("10.00")


def calculate_split(gross_amount: Decimal, commission_percent: Decimal | None = None) -> dict:
    """
    Return gross, commission, net and the immutable BeatHub commission rate.

    The optional argument is retained for compatibility with older callers,
    but producer transactions are never allowed to silently change the
    platform rate away from the contractual 10% rule.
    """
    gross_amount = Decimal(gross_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    configured = Decimal(str(
        commission_percent
        if commission_percent is not None
        else settings.PLATFORM_COMMISSION_PERCENT
    ))

    if configured != BEATHUB_COMMISSION_PERCENT:
        raise RuntimeError(
            "BeatHub commission must remain exactly 10% for producer transactions. "
            f"Configured value was {configured}."
        )

    commission = (gross_amount * BEATHUB_COMMISSION_PERCENT / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    net = (gross_amount - commission).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    return {
        "gross_amount": gross_amount,
        "commission_amount": commission,
        "net_amount": net,
        "commission_percent": BEATHUB_COMMISSION_PERCENT,
    }
