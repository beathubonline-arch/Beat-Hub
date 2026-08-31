from enum import Enum


class Currency(str, Enum):
    """Currencies currently supported by BeatHub's payment architecture."""

    KES = "KES"
    USD = "USD"


SUPPORTED_CURRENCIES = tuple(currency.value for currency in Currency)
DEFAULT_CURRENCY = Currency.KES.value


def normalize_currency(value: str | Currency | None) -> str:
    """Return a canonical supported ISO-4217 currency code."""
    raw = getattr(value, "value", value)
    code = str(raw or DEFAULT_CURRENCY).strip().upper()
    if code not in SUPPORTED_CURRENCIES:
        raise ValueError(f"Unsupported currency: {code}")
    return code
