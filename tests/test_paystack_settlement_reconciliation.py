from decimal import Decimal

from app.services.paystack_settlement import _money


def test_paystack_subunit_money_conversion():
    assert _money(10000) == Decimal("100.00")
    assert _money("12345") == Decimal("123.45")


def test_paystack_reconciliation_routes_are_registered():
    from main import app

    paths = {route.path for route in app.routes}
    assert "/admin/paystack/reconciliation" in paths
    assert "/admin/paystack/reconciliation/sync" in paths
