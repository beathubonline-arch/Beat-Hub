"""Server-authoritative pricing, currency and commission calculations."""
from decimal import ROUND_HALF_UP, Decimal

from app.config import settings

BEATHUB_COMMISSION_PERCENT = Decimal("10.00")
SUPPORTED_CURRENCIES = ("KES", "USD")
CURRENCY_SYMBOLS = {"KES": "KSh", "USD": "$"}


def normalize_currency(value: str | None) -> str:
    currency = str(value or "KES").strip().upper()
    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError("Unsupported currency. Choose KES or USD.")
    return currency


def currency_symbol(currency: str | None) -> str:
    return CURRENCY_SYMBOLS[normalize_currency(currency)]


def format_money(amount, currency: str | None = "KES") -> str:
    currency = normalize_currency(currency)
    value = Decimal(str(amount or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{currency_symbol(currency)} {value:,.2f}"


def calculate_split(gross_amount: Decimal, commission_percent: Decimal | None = None) -> dict:
    gross_amount = Decimal(gross_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    configured = Decimal(str(commission_percent if commission_percent is not None else settings.PLATFORM_COMMISSION_PERCENT))
    if configured != BEATHUB_COMMISSION_PERCENT:
        raise RuntimeError(
            "BeatHub commission must remain exactly 10% for producer transactions. "
            f"Configured value was {configured}."
        )
    commission = (gross_amount * BEATHUB_COMMISSION_PERCENT / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    net = (gross_amount - commission).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "gross_amount": gross_amount,
        "commission_amount": commission,
        "net_amount": net,
        "commission_percent": BEATHUB_COMMISSION_PERCENT,
    }
