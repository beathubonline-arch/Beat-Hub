import unittest
from entitlements import effective_plan, can_use

class EntitlementsTests(unittest.TestCase):
    def test_unpaid_cannot_unlock_pro(self):
        self.assertEqual(effective_plan("agency", "canceled"), "free")
        self.assertEqual(effective_plan("business", "past_due"), "free")
    def test_unknown_plan_fails_closed(self):
        self.assertEqual(effective_plan("enterprise", "active"), "free")
    def test_free_limit(self):
        self.assertTrue(can_use("free", "none", "monthly_leads", 24))
        self.assertFalse(can_use("free", "none", "monthly_leads", 25))
    def test_paid_limit(self):
        self.assertTrue(can_use("pro", "active", "monthly_leads", 499))
        self.assertFalse(can_use("pro", "active", "monthly_leads", 500))
    def test_invalid_metric(self):
        self.assertFalse(can_use("pro", "active", "unlimited", 0))

if __name__ == "__main__":
    unittest.main()
