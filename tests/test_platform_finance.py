import unittest
from decimal import Decimal

from app.services.platform_finance import estimate_mpesa_transfer_fee


class PlatformFinanceRegressionTests(unittest.TestCase):
    def test_transfer_fee_bands(self):
        self.assertEqual(estimate_mpesa_transfer_fee(Decimal("1.00")), Decimal("20.00"))
        self.assertEqual(estimate_mpesa_transfer_fee(Decimal("1500.00")), Decimal("20.00"))
        self.assertEqual(estimate_mpesa_transfer_fee(Decimal("1500.01")), Decimal("40.00"))
        self.assertEqual(estimate_mpesa_transfer_fee(Decimal("20000.00")), Decimal("40.00"))
        self.assertEqual(estimate_mpesa_transfer_fee(Decimal("20000.01")), Decimal("60.00"))
        self.assertEqual(estimate_mpesa_transfer_fee(Decimal("150000.00")), Decimal("60.00"))

    def test_finance_amounts_are_decimal_safe(self):
        from app.services.platform_finance import _decimal

        self.assertEqual(_decimal("100.005"), Decimal("100.01"))
        self.assertEqual(_decimal(None), Decimal("0.00"))
        self.assertEqual(_decimal("-10.50"), Decimal("-10.50"))


if __name__ == "__main__":
    unittest.main()
