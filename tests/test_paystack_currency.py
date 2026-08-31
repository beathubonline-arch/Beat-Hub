from decimal import Decimal

import pytest

from app.models.currency import normalize_currency
from app.routers.paystack_checkout import PAYSTACK_MINIMUMS, _minor_units


@pytest.mark.parametrize(
    ("currency", "amount", "expected_minor"),
    [
        ("KES", Decimal("1500.00"), 150000),
        ("KES", Decimal("3.00"), 300),
        ("USD", Decimal("10.00"), 1000),
        ("USD", Decimal("2.00"), 200),
    ],
)
def test_paystack_minor_units(currency, amount, expected_minor):
    assert _minor_units(amount, currency) == expected_minor


def test_supported_currency_normalization():
    assert normalize_currency("kes") == "KES"
    assert normalize_currency(" USD ") == "USD"


def test_paystack_minimums_are_currency_specific():
    assert PAYSTACK_MINIMUMS["KES"] == Decimal("3.00")
    assert PAYSTACK_MINIMUMS["USD"] == Decimal("2.00")


def test_unsupported_currency_is_rejected():
    with pytest.raises(ValueError):
        normalize_currency("EUR")
