import unittest
from datetime import datetime
from decimal import Decimal

from app.routers import creator_merch_integration as integration
import app.routers.dashboard as dashboard


class _FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, *_args, **_kwargs):
        return _FakeResult(self.rows)


class CreatorMerchandiseDashboardTests(unittest.TestCase):
    def _rows(self, *items):
        return [
            {
                "id": item[0],
                "total_amount": Decimal(str(item[1])),
                "commission_amount": Decimal(str(item[2])),
                "net_amount": Decimal(str(item[3])),
                "created_at": item[4],
                "paid_at": item[4],
                "quantity": 1,
                "product_name": item[5],
            }
            for item in items
        ]

    def test_paid_merchandise_is_aggregated_for_creator(self):
        when = datetime(2026, 8, 30, 9, 0, 0)
        db = _FakeDB(
            self._rows(
                ("m1", 3000, 300, 2700, when, "Bono Hoodie"),
            )
        )

        gross, commission, net, count, recent = integration._paid_merchandise_stats(
            db, "bono-profile"
        )

        self.assertEqual(gross, Decimal("3000"))
        self.assertEqual(commission, Decimal("300"))
        self.assertEqual(net, Decimal("2700"))
        self.assertEqual(count, 1)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0].track.title, "Merch: Bono Hoodie")
        self.assertEqual(recent[0].net_amount, Decimal("2700"))

    def test_dashboard_wrapper_adds_merch_without_double_counting(self):
        original = dashboard._creator_stats
        original_marker = getattr(original, "_beathub_merch_integrated", False)

        def fake_original(_db, _profile_id):
            return {
                "total_sales": 1,
                "gross_revenue": Decimal("1000"),
                "platform_commission": Decimal("100"),
                "net_earnings": Decimal("900"),
                "available_balance": Decimal("900"),
                "pending_withdrawal": Decimal("0"),
                "recent_orders": [],
            }

        try:
            dashboard._creator_stats = fake_original
            integration.patch_creator_dashboard()
            wrapped = dashboard._creator_stats
            result = wrapped(
                _FakeDB(
                    self._rows(
                        ("m1", 3000, 300, 2700, datetime(2026, 8, 30, 9, 0, 0), "Bono Hoodie"),
                    )
                ),
                "bono-profile",
            )

            self.assertEqual(result["total_sales"], 2)
            self.assertEqual(result["gross_revenue"], Decimal("4000"))
            self.assertEqual(result["platform_commission"], Decimal("400"))
            self.assertEqual(result["net_earnings"], Decimal("3600"))
            self.assertEqual(result["available_balance"], Decimal("3600"))
            self.assertEqual(len(result["recent_orders"]), 1)
            self.assertEqual(result["recent_orders"][0].track.title, "Merch: Bono Hoodie")
        finally:
            dashboard._creator_stats = original
            if original_marker:
                dashboard._creator_stats._beathub_merch_integrated = True


if __name__ == "__main__":
    unittest.main()
