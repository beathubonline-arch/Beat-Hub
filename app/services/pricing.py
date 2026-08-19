"""
Server-authoritative pricing and commission calculations.
Never trust a price or split coming from the client/browser.
"""
from decimal import ROUND_HALF_UP, Decimal

from app.config import settings


def calculate_split(gross_amount: Decimal, commission_percent: Decimal | None = None) -> dict:
    """
    Returns {gross, commission, net, commission_percent} using Decimal
    arithmetic throughout (no floats) to avoid currency rounding errors.
    """
    gross_amount = Decimal(gross_amount)
    pct = Decimal(str(commission_percent if commission_percent is not None else settings.PLATFORM_COMMISSION_PERCENT))

    commission = (gross_amount * pct / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    net = (gross_amount - commission).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return {
        "gross_amount": gross_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "commission_amount": commission,
        "net_amount": net,
        "commission_percent": pct,
    }
