import unittest
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.ledger import AdminWithdrawal, PlatformLedgerEntry
from app.services.platform_finance import (
    _decimal,
    estimate_mpesa_transfer_fee,
    record_platform_withdrawal,
)


class PlatformFinanceRegressionTests(unittest.TestCase):
    def test_transfer_fee_bands(self):
        self.assertEqual(estimate_mpesa_transfer_fee(Decimal("1.00")), Decimal("20.00"))
        self.assertEqual(estimate_mpesa_transfer_fee(Decimal("1500.00")), Decimal("20.00"))
        self.assertEqual(estimate_mpesa_transfer_fee(Decimal("1500.01")), Decimal("40.00"))
        self.assertEqual(estimate_mpesa_transfer_fee(Decimal("20000.00")), Decimal("40.00"))
        self.assertEqual(estimate_mpesa_transfer_fee(Decimal("20000.01")), Decimal("60.00"))
        self.assertEqual(estimate_mpesa_transfer_fee(Decimal("150000.00")), Decimal("60.00"))

    def test_finance_amounts_are_decimal_safe(self):
        self.assertEqual(_decimal("100.005"), Decimal("100.01"))
        self.assertEqual(_decimal(None), Decimal("0.00"))
        self.assertEqual(_decimal("-10.50"), Decimal("-10.50"))

    def test_platform_withdrawal_debit_is_idempotent(self):
        engine = create_engine("sqlite:///:memory:")
        AdminWithdrawal.__table__.create(engine)
        PlatformLedgerEntry.__table__.create(engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            withdrawal = AdminWithdrawal(
                amount=Decimal("100.00"),
                phone_number="254712345678",
                status="paid",
                payout_reference="test-transfer-001",
            )
            db.add(withdrawal)
            db.commit()

            first = record_platform_withdrawal(db, withdrawal, Decimal("20.00"))
            db.commit()
            second = record_platform_withdrawal(db, withdrawal, Decimal("20.00"))
            db.commit()

            entries = db.query(PlatformLedgerEntry).filter(
                PlatformLedgerEntry.admin_withdrawal_id == withdrawal.id,
                PlatformLedgerEntry.entry_type == "platform_withdrawal",
            ).all()

            self.assertTrue(first)
            self.assertFalse(second)
            self.assertEqual(len(entries), 1)
            self.assertEqual(Decimal(str(entries[0].amount)), Decimal("-120.00"))
        finally:
            db.close()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
