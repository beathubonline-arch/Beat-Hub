from app.models.currency import Currency, DEFAULT_CURRENCY, SUPPORTED_CURRENCIES, normalize_currency
from app.models.ledger import AdminWithdrawal, CreatorLedgerEntry, PlatformLedgerEntry, WithdrawalRequest
from app.models.music import Track
from app.models.order import Order
from app.models.payment import PaymentTransaction


def test_supported_currencies_are_limited_to_kes_and_usd():
    assert DEFAULT_CURRENCY == "KES"
    assert SUPPORTED_CURRENCIES == ("KES", "USD")
    assert normalize_currency("kes") == "KES"
    assert normalize_currency(Currency.USD) == "USD"


def test_currency_columns_exist_on_financial_models():
    assert Track.currency.name == "currency"
    assert Order.currency.name == "currency"
    assert PaymentTransaction.currency.name == "currency"
    assert CreatorLedgerEntry.currency.name == "currency"
    assert PlatformLedgerEntry.currency.name == "currency"
    assert WithdrawalRequest.currency.name == "currency"
    assert AdminWithdrawal.currency.name == "currency"


def test_invalid_currency_is_rejected():
    try:
        normalize_currency("EUR")
    except ValueError as exc:
        assert "Unsupported currency" in str(exc)
    else:
        raise AssertionError("Unsupported currency should be rejected")
