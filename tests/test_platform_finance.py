import unittest
from decimal import Decimal
from unittest.mock import Mock

from app.services.platform_finance import _decimal, estimate_mpesa_transfer_fee, record_platform_withdrawal


class PlatformFinanceRegressionTests(unittest.TestCase):
    def test_transfer_fee_bands(self):
        self.assertEqual(estimate_mpesa_transfer_fee(Decimal("1.00")), Decimal("20.00"))
        self.assertEqual(estimate_mpesa_transfer_fee(Decimal("1500.00")), Decimal("20.00"))
        self.assertEqual(estimate_mpesa_transfer_fee(Decimal("1500.01")), Decimal("40.00"))
        self.assertEqual(estimate_mpesa_transfer_fee(Decimal("20000.00")), Decimal("40.00"))
        self.assertEqual(estimate_mpesa_transfer_fee(Decimal("20000.01")), Decimal("60.00"))
        self.assertEqual(estimate_mpesa_transfer_fee(Decimal("150000.00")), Decimal("60.00"))

    def test_finance_amounts_are_decimal_safe(self):
        self.assertEqual(_decimal("100.005"), Decimal("100.00"))
        self.assertEqual(_decimal("100.006"), Decimal("100.01"))
        self.assertEqual(_decimal(None), Decimal("0.00"))
        self.assertEqual(_decimal("-10.50"), Decimal("-10.50"))

    def test_platform_withdrawal_debit_is_idempotent(self):
        db = Mock()
        query = db.query.return_value
        query.filter.return_value.filter.return_value.first.side_effect = [None, object()]

        withdrawal = Mock()
        withdrawal.id = "withdrawal-1"
        withdrawal.amount = Decimal("100.00")
        withdrawal.payout_reference = "test-transfer-001"

        first = record_platform_withdrawal(db, withdrawal, Decimal("20.00"))
        second = record_platform_withdrawal(db, withdrawal, Decimal("20.00"))

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(db.add.call_count, 1)

        entry = db.add.call_args.args[0]
        self.assertEqual(entry.entry_type, "platform_withdrawal")
        self.assertEqual(entry.amount, Decimal("-120.00"))
        self.assertEqual(entry.admin_withdrawal_id, "withdrawal-1")


if __name__ == "__main__":
    unittest.main()
