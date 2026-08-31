import asyncio
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

from app.models.order import Order
from app.models.payment import PaymentTransaction
from app.models.paystack_settlement import PaystackSettlement
from app.services import paystack_reconciliation as reconciliation


class FakeQuery:
    def __init__(self, model, scalars=None, rows=None, existing=None):
        self.model = model
        self.scalars = scalars or {}
        self.rows = rows or []
        self.existing = existing

    def filter(self, *args, **kwargs):
        return self

    def scalar(self):
        return self.scalars.get(self.model, Decimal("0.00"))

    def all(self):
        return list(self.rows)

    def first(self):
        return self.existing


class FakeDB:
    def __init__(self, scalars=None, settlements=None, existing=None):
        self.scalars = scalars or {}
        self.settlements = settlements or []
        self.existing = existing
        self.added = []
        self.commits = 0

    def query(self, model):
        if model is PaystackSettlement:
            return FakeQuery(model, rows=self.settlements, existing=self.existing)
        return FakeQuery(model, scalars=self.scalars)

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commits += 1


class PaystackReconciliationTests(unittest.TestCase):
    def test_reconciliation_flags_unsettled_amount(self):
        settlement = Mock(status="success", currency="KES", total_amount=Decimal("900.00"))
        db = FakeDB(
            scalars={PaymentTransaction: Decimal("1000.00"), Order: Decimal("1000.00")},
            settlements=[settlement],
        )
        report = reconciliation.build_reconciliation(db, "KES")
        self.assertEqual(report["local_completed_payment_total"], Decimal("1000.00"))
        self.assertEqual(report["paystack_settled_total"], Decimal("900.00"))
        self.assertEqual(report["payment_vs_settlement_difference"], Decimal("100.00"))
        self.assertEqual(report["status"], "review")

    def test_reconciliation_balances_when_totals_match(self):
        settlement = Mock(status="success", currency="KES", total_amount=Decimal("1000.00"))
        db = FakeDB(
            scalars={PaymentTransaction: Decimal("1000.00"), Order: Decimal("1000.00")},
            settlements=[settlement],
        )
        report = reconciliation.build_reconciliation(db, "KES")
        self.assertEqual(report["payment_vs_order_difference"], Decimal("0.00"))
        self.assertEqual(report["payment_vs_settlement_difference"], Decimal("0.00"))
        self.assertEqual(report["status"], "balanced")

    def test_sync_is_idempotent(self):
        class FakeResponse:
            status_code = 200
            def json(self):
                return {"data": [{"id": "stl_001", "status": "success", "currency": "KES", "total_amount": 90000, "settlement_date": "2026-09-01T12:00:00Z"}]}
            def raise_for_status(self):
                return None

        class FakeClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            async def get(self, *args, **kwargs): return FakeResponse()

        db = FakeDB()
        with patch.object(reconciliation.httpx, "AsyncClient", FakeClient), patch.object(reconciliation.settings, "PAYSTACK_SECRET_KEY", "test-key"):
            first = asyncio.run(reconciliation.sync_paystack_settlements(db, max_pages=1))
            self.assertEqual(first["imported"], 1)
            self.assertEqual(len(db.added), 1)
            db.existing = db.added[0]
            second = asyncio.run(reconciliation.sync_paystack_settlements(db, max_pages=1))
            self.assertEqual(second["imported"], 0)
            self.assertEqual(second["updated"], 1)
            self.assertEqual(db.commits, 2)


if __name__ == "__main__":
    unittest.main()
