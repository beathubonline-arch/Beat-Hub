import unittest
from decimal import Decimal

from app.services.paystack_settlement import _money


class PaystackSettlementReconciliationTests(unittest.TestCase):
    def test_paystack_subunit_money_conversion(self):
        self.assertEqual(_money(10000), Decimal("100.00"))
        self.assertEqual(_money("12345"), Decimal("123.45"))
        self.assertEqual(_money(0), Decimal("0.00"))

    def test_paystack_reconciliation_routes_are_registered(self):
        from main import app

        paths = {route.path for route in app.routes}
        self.assertIn("/admin/paystack/reconciliation", paths)
        self.assertIn("/admin/paystack/reconciliation/sync", paths)
