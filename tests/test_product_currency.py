from decimal import Decimal

import pytest

from app.services.pricing import format_money, normalize_currency


def test_supported_product_currencies():
    assert normalize_currency("kes") == "KES"
    assert normalize_currency("USD") == "USD"


def test_invalid_product_currency_is_rejected():
    with pytest.raises(ValueError):
        normalize_currency("EUR")


def test_currency_formatting_is_unambiguous():
    assert format_money(Decimal("1500"), "KES") == "KSh 1,500.00"
    assert format_money(Decimal("10"), "USD") == "$ 10.00"
